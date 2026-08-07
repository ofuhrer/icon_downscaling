#!/usr/bin/env python3
"""Characterize native-SMI and legacy cold-start error over model age 0--72 h."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import netCDF4
import numpy as np


ORIGINS = ("20200702", "20200703")
BIN_HOURS = 6
END_HOURS = 72
SCALARS = (
    "hpbl", "sfc_Ri", "ustar", "taix", "hus2m", "tsfe", "hfss", "hfls",
    "soil_column_total_water", "soil_water_content", "soil_temperature", "snow_height",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or not Path(f"{path}.ready").is_file():
        raise ValueError(f"input is not published: {path}")
    payload = json.loads(path.read_text())
    if payload.get("status") not in {"PASS", "PLANNED"}:
        raise ValueError(f"input is not passing: {path}")
    return payload


def as_datetime(value: Any) -> datetime:
    return datetime.fromisoformat(str(value)[:19])


def dataset_times(dataset: netCDF4.Dataset) -> list[datetime]:
    variable = dataset.variables["time"]
    return [
        as_datetime(value)
        for value in netCDF4.num2date(
            variable[:], units=variable.units,
            calendar=getattr(variable, "calendar", "standard"),
        )
    ]


class HistoryIndex:
    """Timestamp index that never keeps an HDF5 dataset open."""

    def __init__(self, report: dict[str, Any]):
        self.index: dict[datetime, tuple[Path, int]] = {}
        for segment in report["segments"]:
            for item in segment["compressed"]:
                path = Path(item["path"])
                if not path.is_file() or not Path(f"{path}.ready").is_file():
                    raise ValueError(f"compressed output is not published: {path}")
                with netCDF4.Dataset(path) as dataset:
                    for record, valid in enumerate(dataset_times(dataset)):
                        if valid in self.index:
                            raise ValueError(f"duplicate timestamp in chain: {valid}")
                        self.index[valid] = (path, record)

    def heights(self) -> np.ndarray:
        path, _ = next(iter(self.index.values()))
        with netCDF4.Dataset(path) as dataset:
            return np.asarray(dataset.variables["height_agl"][:])


class ScalarAccumulator:
    def __init__(self) -> None:
        self.count = 0
        self.error_sum = 0.0
        self.error_sq_sum = 0.0
        self.reference_sq_sum = 0.0
        self.max_abs = 0.0

    def update(self, candidate: np.ndarray, reference: np.ndarray) -> None:
        finite = np.isfinite(candidate) & np.isfinite(reference)
        if not np.any(finite):
            return
        left = np.asarray(candidate[finite], dtype=np.float64)
        right = np.asarray(reference[finite], dtype=np.float64)
        error = left - right
        self.count += error.size
        self.error_sum += float(np.sum(error))
        self.error_sq_sum += float(np.sum(error * error))
        self.reference_sq_sum += float(np.sum(right * right))
        self.max_abs = max(self.max_abs, float(np.max(np.abs(error))))

    def merge(self, other: "ScalarAccumulator") -> None:
        self.count += other.count
        self.error_sum += other.error_sum
        self.error_sq_sum += other.error_sq_sum
        self.reference_sq_sum += other.reference_sq_sum
        self.max_abs = max(self.max_abs, other.max_abs)

    def result(self) -> dict[str, float | int]:
        if not self.count:
            raise ValueError("scalar comparison has no finite samples")
        rmse = math.sqrt(self.error_sq_sum / self.count)
        reference_rms = math.sqrt(self.reference_sq_sum / self.count)
        return {
            "count": self.count,
            "mean_bias": self.error_sum / self.count,
            "rmse": rmse,
            "relative_rmse": rmse / reference_rms if reference_rms else math.inf,
            "max_abs": self.max_abs,
        }


class WindAccumulator:
    def __init__(self) -> None:
        self.count = 0
        self.error_sq_sum = 0.0
        self.reference_speed_sq_sum = 0.0
        self.speed_bias_sum = 0.0
        self.direction_error_sum = 0.0
        self.direction_count = 0
        self.max_time_p99 = 0.0

    def update(
        self,
        u: np.ndarray,
        v: np.ndarray,
        reference_u: np.ndarray,
        reference_v: np.ndarray,
        direction_min_speed: float,
    ) -> None:
        finite = (
            np.isfinite(u) & np.isfinite(v)
            & np.isfinite(reference_u) & np.isfinite(reference_v)
        )
        if not np.any(finite):
            return
        u = np.asarray(u[finite], dtype=np.float64)
        v = np.asarray(v[finite], dtype=np.float64)
        reference_u = np.asarray(reference_u[finite], dtype=np.float64)
        reference_v = np.asarray(reference_v[finite], dtype=np.float64)
        du, dv = u - reference_u, v - reference_v
        error = np.hypot(du, dv)
        speed = np.hypot(u, v)
        reference_speed = np.hypot(reference_u, reference_v)
        self.count += error.size
        self.error_sq_sum += float(np.sum(error * error))
        self.reference_speed_sq_sum += float(np.sum(reference_speed * reference_speed))
        self.speed_bias_sum += float(np.sum(speed - reference_speed))
        self.max_time_p99 = max(self.max_time_p99, float(np.quantile(error, 0.99)))
        directional = (speed >= direction_min_speed) & (
            reference_speed >= direction_min_speed
        )
        if np.any(directional):
            angle = np.degrees(
                np.arctan2(
                    u[directional] * reference_v[directional]
                    - v[directional] * reference_u[directional],
                    u[directional] * reference_u[directional]
                    + v[directional] * reference_v[directional],
                )
            )
            self.direction_error_sum += float(np.sum(np.abs(angle)))
            self.direction_count += angle.size

    def merge(self, other: "WindAccumulator") -> None:
        self.count += other.count
        self.error_sq_sum += other.error_sq_sum
        self.reference_speed_sq_sum += other.reference_speed_sq_sum
        self.speed_bias_sum += other.speed_bias_sum
        self.direction_error_sum += other.direction_error_sum
        self.direction_count += other.direction_count
        self.max_time_p99 = max(self.max_time_p99, other.max_time_p99)

    def result(self) -> dict[str, float | int]:
        if not self.count:
            raise ValueError("wind comparison has no finite samples")
        rmse = math.sqrt(self.error_sq_sum / self.count)
        reference_rms = math.sqrt(self.reference_speed_sq_sum / self.count)
        return {
            "count": self.count,
            "vector_rmse_m_s": rmse,
            "relative_vector_rmse": rmse / reference_rms if reference_rms else math.inf,
            "speed_bias_m_s": self.speed_bias_sum / self.count,
            "direction_mae_degrees": (
                self.direction_error_sum / self.direction_count
                if self.direction_count else math.nan
            ),
            "max_time_vector_error_p99_m_s": self.max_time_p99,
        }


class MetricSet:
    def __init__(self, heights: list[int]):
        self.wind = {height: WindAccumulator() for height in heights}
        self.scalars = {name: ScalarAccumulator() for name in SCALARS}

    def merge(self, other: "MetricSet") -> None:
        for height in self.wind:
            self.wind[height].merge(other.wind[height])
        for name in self.scalars:
            self.scalars[name].merge(other.scalars[name])

    def result(self) -> dict[str, Any]:
        return {
            "wind": {str(height): item.result() for height, item in self.wind.items()},
            "scalars": {name: item.result() for name, item in self.scalars.items()},
        }


def spatial(values: np.ndarray, trim: int, mask: np.ndarray | None) -> np.ndarray:
    values = np.asarray(values)[..., trim:-trim, trim:-trim]
    if mask is None:
        return values
    return values[..., mask[trim:-trim, trim:-trim]]


def read_record(dataset: netCDF4.Dataset, name: str, record: int) -> np.ndarray:
    return np.ma.asarray(dataset.variables[name][record]).filled(np.nan)


def update_metrics(
    metrics: MetricSet,
    left: netCDF4.Dataset,
    right: netCDF4.Dataset,
    left_record: int,
    right_record: int,
    *,
    heights: list[int],
    height_index: dict[int, int],
    trim: int,
    landmask: np.ndarray,
    active_soil: np.ndarray,
    direction_min_speed: float,
) -> None:
    left_u10 = read_record(left, "u10m", left_record)
    left_v10 = read_record(left, "v10m", left_record)
    right_u10 = read_record(right, "u10m", right_record)
    right_v10 = read_record(right, "v10m", right_record)
    metrics.wind[10].update(
        spatial(left_u10, trim, None), spatial(left_v10, trim, None),
        spatial(right_u10, trim, None), spatial(right_v10, trim, None),
        direction_min_speed,
    )
    left_u = read_record(left, "u_agl", left_record)
    left_v = read_record(left, "v_agl", left_record)
    right_u = read_record(right, "u_agl", right_record)
    right_v = read_record(right, "v_agl", right_record)
    for height in heights:
        if height == 10:
            continue
        index = height_index[height]
        metrics.wind[height].update(
            spatial(left_u[index], trim, None), spatial(left_v[index], trim, None),
            spatial(right_u[index], trim, None), spatial(right_v[index], trim, None),
            direction_min_speed,
        )
    for name, accumulator in metrics.scalars.items():
        mask = active_soil if name.startswith("soil_") else landmask
        accumulator.update(
            spatial(read_record(left, name, left_record), trim, mask),
            spatial(read_record(right, name, right_record), trim, mask),
        )


def compare_age_bins(
    chain_report: dict[str, Any],
    reference_report: dict[str, Any],
    origin: datetime,
    *,
    trim: int,
    landmask: np.ndarray,
    active_soil: np.ndarray,
    direction_min_speed: float,
) -> tuple[list[dict[str, Any]], list[MetricSet]]:
    chain = HistoryIndex(chain_report)
    reference = HistoryIndex(reference_report)
    fixed_heights = [int(value) for value in chain.heights()]
    heights = [10, *fixed_heights]
    height_index = {height: index for index, height in enumerate(fixed_heights)}
    accumulators = [MetricSet(heights) for _ in range(END_HOURS // BIN_HOURS)]
    groups: dict[tuple[Path, Path], list[tuple[int, int, int]]] = defaultdict(list)
    for valid in sorted(set(chain.index) & set(reference.index)):
        age_hours = (valid - origin).total_seconds() / 3600.0
        if not (0.0 < age_hours <= END_HOURS):
            continue
        bin_index = min(int(math.ceil(age_hours / BIN_HOURS)) - 1, len(accumulators) - 1)
        left_path, left_record = chain.index[valid]
        right_path, right_record = reference.index[valid]
        groups[(left_path, right_path)].append((left_record, right_record, bin_index))
    for (left_path, right_path), records in groups.items():
        with netCDF4.Dataset(left_path) as left, netCDF4.Dataset(right_path) as right:
            for left_record, right_record, bin_index in records:
                update_metrics(
                    accumulators[bin_index], left, right, left_record, right_record,
                    heights=heights, height_index=height_index, trim=trim,
                    landmask=landmask, active_soil=active_soil,
                    direction_min_speed=direction_min_speed,
                )
    rows = []
    for index, accumulator in enumerate(accumulators):
        rows.append(
            {
                "model_age_start_exclusive_hours": index * BIN_HOURS,
                "model_age_end_inclusive_hours": (index + 1) * BIN_HOURS,
                "metrics": accumulator.result(),
            }
        )
    return rows, accumulators


def family_passes(metrics: dict[str, Any], thresholds: dict[str, Any]) -> dict[str, bool]:
    wind_limits = thresholds["wind"]
    wind = all(
        item["vector_rmse_m_s"] <= wind_limits["vector_rmse_m_s"]
        and item["relative_vector_rmse"] <= wind_limits["relative_vector_rmse"]
        and abs(item["speed_bias_m_s"]) <= wind_limits["absolute_speed_bias_m_s"]
        and item["direction_mae_degrees"] <= wind_limits["direction_mae_degrees"]
        and item["max_time_vector_error_p99_m_s"] <= wind_limits["vector_error_p99_m_s"]
        for item in metrics["wind"].values()
    )
    scalar = metrics["scalars"]
    pbl_limits = thresholds["pbl"]
    pbl = (
        scalar["hpbl"]["relative_rmse"] <= pbl_limits["relative_hpbl_rmse"]
        and abs(scalar["hpbl"]["mean_bias"])
        <= pbl_limits["absolute_hpbl_mean_bias_m"]
    )
    surface_limits = thresholds["surface_reset_materiality"]
    surface = (
        scalar["tsfe"]["rmse"] <= surface_limits["tsfe_rmse_k"]
        and abs(scalar["hfss"]["mean_bias"])
        <= surface_limits["hfss_absolute_mean_bias_w_m2"]
        and abs(scalar["hfls"]["mean_bias"])
        <= surface_limits["hfls_absolute_mean_bias_w_m2"]
    )
    soil_limits = thresholds["slow_state_reset_materiality"]
    soil_snow = (
        scalar["soil_temperature"]["rmse"] <= soil_limits["soil_temperature_rmse_k"]
        and scalar["soil_water_content"]["rmse"] <= soil_limits["soil_water_rmse_m3_m3"]
        and abs(scalar["soil_column_total_water"]["mean_bias"])
        <= soil_limits["soil_column_water_absolute_mean_bias_kg_m2"]
    )
    return {
        "wind": wind,
        "pbl": pbl,
        "surface": surface,
        "soil_snow": soil_snow,
        "all": wind and pbl and surface and soil_snow,
    }


def add_passes(rows: list[dict[str, Any]], thresholds: dict[str, Any]) -> None:
    for row in rows:
        row["threshold_characterization"] = family_passes(row["metrics"], thresholds)


def core_windows(
    accumulators: list[MetricSet], thresholds: dict[str, Any]
) -> list[dict[str, Any]]:
    results = []
    for start in range(0, END_HOURS - 24 + 1, BIN_HOURS):
        merged: MetricSet | None = None
        for source in accumulators[start // BIN_HOURS:(start + 24) // BIN_HOURS]:
            if merged is None:
                heights = [int(value) for value in source.wind]
                merged = MetricSet(heights)
            merged.merge(source)
        assert merged is not None
        metrics = merged.result()
        results.append(
            {
                "warmup_hours": start,
                "core_age_end_hours": start + 24,
                "metrics": metrics,
                "threshold_characterization": family_passes(metrics, thresholds),
            }
        )
    return results


def error_delta(candidate: dict[str, Any], legacy: dict[str, Any]) -> dict[str, Any]:
    def row(new: float, old: float) -> dict[str, float | bool]:
        return {
            "candidate": new,
            "legacy": old,
            "candidate_minus_legacy": new - old,
            "candidate_over_legacy": new / old if old else (0.0 if new == 0 else None),
            "candidate_improved": new < old,
        }

    return {
        "max_wind_vector_rmse_m_s": row(
            max(item["vector_rmse_m_s"] for item in candidate["wind"].values()),
            max(item["vector_rmse_m_s"] for item in legacy["wind"].values()),
        ),
        "hpbl_rmse_m": row(
            candidate["scalars"]["hpbl"]["rmse"], legacy["scalars"]["hpbl"]["rmse"]
        ),
        "tsfe_rmse_k": row(
            candidate["scalars"]["tsfe"]["rmse"], legacy["scalars"]["tsfe"]["rmse"]
        ),
        "soil_temperature_rmse_k": row(
            candidate["scalars"]["soil_temperature"]["rmse"],
            legacy["scalars"]["soil_temperature"]["rmse"],
        ),
        "soil_water_rmse_m3_m3": row(
            candidate["scalars"]["soil_water_content"]["rmse"],
            legacy["scalars"]["soil_water_content"]["rmse"],
        ),
        "soil_column_water_bias_abs_kg_m2": row(
            abs(candidate["scalars"]["soil_column_total_water"]["mean_bias"]),
            abs(legacy["scalars"]["soil_column_total_water"]["mean_bias"]),
        ),
        "snow_height_rmse_m": row(
            candidate["scalars"]["snow_height"]["rmse"],
            legacy["scalars"]["snow_height"]["rmse"],
        ),
    }


def earliest_sustained(rows_by_origin: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    result = {}
    for family in ("wind", "pbl", "surface", "soil_snow", "all"):
        earliest = None
        for index in range(END_HOURS // BIN_HOURS):
            if all(
                all(row["threshold_characterization"][family] for row in rows[index:])
                for rows in rows_by_origin.values()
            ):
                earliest = (index + 1) * BIN_HOURS
                break
        result[family] = {
            "earliest_bin_end_hours": earliest,
            "interpretation": (
                "post-hoc 6-hour-bin characterization in this summer case; not a "
                "predeclared warm-up selection threshold"
            ),
        }
    return result


def earliest_core_pass(cores_by_origin: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    result = {}
    for family in ("wind", "pbl", "surface", "soil_snow", "all"):
        earliest = None
        for warmup in range(0, END_HOURS - 24 + 1, BIN_HOURS):
            if all(
                next(row for row in rows if row["warmup_hours"] == warmup)[
                    "threshold_characterization"
                ][family]
                for rows in cores_by_origin.values()
            ):
                earliest = warmup
                break
        result[family] = {
            "earliest_warmup_hours": earliest,
            "interpretation": (
                "post-hoc 24-hour moving-core characterization in this summer case; "
                "cores at different warm-ups cover different valid times and do not by "
                "themselves prove a production minimum"
            ),
        }
    return result


def publish(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent,
        prefix=f".{path.name}.", delete=False,
    ) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)
    Path(f"{path}.ready").write_text(sha256(path) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-contract", required=True, type=Path)
    parser.add_argument("--baseline-completion", required=True, type=Path)
    parser.add_argument("--candidate-completion", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    if args.report.exists() or Path(f"{args.report}.ready").exists():
        raise SystemExit(f"refusing to overwrite publication: {args.report}")
    contract = require_json(args.baseline_contract)
    baseline = require_json(args.baseline_completion)
    candidate = require_json(args.candidate_completion)
    baseline_chains = {item["chain_id"]: item for item in baseline["chains"]}
    candidate_chains = {item["chain_id"]: item for item in candidate["chains"]}
    reference = baseline_chains[contract["reference"]["chain_id"]]
    trim = int(contract["diagnostics"]["interior_boundary_trim_cells"])
    with netCDF4.Dataset(contract["reference"]["static_file"]) as static:
        landmask = np.asarray(static.variables["landmask"][:]) > 0
        landuse = np.asarray(static.variables["landuse"][:])
    active_soil = landmask & (landuse != 16) & (landuse != 24)
    direction_min = contract["thresholds"]["wind"]["direction_min_speed_m_s"]
    common = {
        "trim": trim,
        "landmask": landmask,
        "active_soil": active_soil,
        "direction_min_speed": direction_min,
    }
    origins: dict[str, Any] = {}
    candidate_rows_by_origin = {}
    candidate_cores_by_origin = {}
    for origin_name in ORIGINS:
        origin = datetime.strptime(origin_name, "%Y%m%d")
        legacy_rows, legacy_accumulators = compare_age_bins(
            baseline_chains[f"origin-{origin_name}"], reference, origin, **common
        )
        candidate_rows, candidate_accumulators = compare_age_bins(
            candidate_chains[f"native-origin-{origin_name}"], reference, origin, **common
        )
        add_passes(legacy_rows, contract["thresholds"])
        add_passes(candidate_rows, contract["thresholds"])
        for old, new in zip(legacy_rows, candidate_rows, strict=True):
            new["candidate_vs_legacy_error"] = error_delta(new["metrics"], old["metrics"])
        legacy_cores = core_windows(legacy_accumulators, contract["thresholds"])
        candidate_cores = core_windows(candidate_accumulators, contract["thresholds"])
        for old, new in zip(legacy_cores, candidate_cores, strict=True):
            new["candidate_vs_legacy_error"] = error_delta(new["metrics"], old["metrics"])
        candidate_rows_by_origin[origin_name] = candidate_rows
        candidate_cores_by_origin[origin_name] = candidate_cores
        origins[origin_name] = {
            "six_hour_bins": {"legacy": legacy_rows, "candidate": candidate_rows},
            "moving_24_hour_cores": {
                "legacy": legacy_cores,
                "candidate": candidate_cores,
            },
        }
    payload = {
        "schema_version": 1,
        "status": "PASS",
        "scope": "post-hoc equilibration characterization; no new model integration",
        "binning": {
            "model_age_bins_hours": "(0,6], (6,12], ..., (66,72]",
            "same_valid_time_reference": "unchanged restart-continuous HICAR trajectory",
            "warmup_inference_limit": (
                "Bins change valid time as model age increases. They characterize decay but "
                "do not alone prove that a shorter production warm-up is sufficient."
            ),
        },
        "earliest_sustained_characterization": earliest_sustained(candidate_rows_by_origin),
        "earliest_24_hour_core_characterization": earliest_core_pass(
            candidate_cores_by_origin
        ),
        "origins": origins,
        "thresholds": contract["thresholds"],
        "sources": {
            "baseline_contract": str(args.baseline_contract.resolve()),
            "baseline_contract_sha256": sha256(args.baseline_contract),
            "baseline_completion": str(args.baseline_completion.resolve()),
            "baseline_completion_sha256": sha256(args.baseline_completion),
            "candidate_completion": str(args.candidate_completion.resolve()),
            "candidate_completion_sha256": sha256(args.candidate_completion),
            "assessor": str(Path(__file__).resolve()),
            "assessor_sha256": sha256(Path(__file__).resolve()),
        },
    }
    publish(args.report, payload)
    print(json.dumps({"status": "PASS", **payload["earliest_sustained_characterization"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
