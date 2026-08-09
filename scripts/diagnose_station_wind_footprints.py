#!/usr/bin/env python3
"""Diagnose location sensitivity of HICAR winds at exposed stations.

This is deliberately a companion to ``compare_hicar_rea_l_to_smn.py`` rather
than another station evaluator.  It uses that evaluator's accepted station
mapping and site scores, then streams HICAR 10 m wind fields to quantify how
strongly a point comparison changes within small, fixed spatial footprints.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import netCDF4
import numpy as np


FOOTPRINT_RADII_KM = (0.4, 1.0)
RIDGE_THRESHOLD_M = 150.0
HIGH_ELEVATION_THRESHOLD_M = 2_000.0
MINIMUM_FOOTPRINT_COVERAGE = 0.8
MINIMUM_FOOTPRINT_CELLS = 3
STATIC_ELEVATION_TOLERANCE_M = 1.0e-3
COORDINATE_TOLERANCE_DEGREES = 5.0e-6


def finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def parse_time(value: str) -> datetime:
    stripped = value.strip()
    for form in ("%Y%m%d%H%M%S", "%Y%m%d%H%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(stripped, form).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"unrecognized timestamp {value!r}") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def canonical_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    rounded = value.replace(microsecond=0)
    if abs((value - rounded).total_seconds()) <= 1.0:
        return rounded
    return value


def decoded_times(dataset: netCDF4.Dataset) -> list[datetime]:
    variable = dataset.variables["time"]
    values = netCDF4.num2date(
        variable[:],
        units=variable.units,
        calendar=getattr(variable, "calendar", "standard"),
        only_use_cftime_datetimes=False,
        only_use_python_datetimes=True,
    )
    return [canonical_time(value) for value in values]


def read_float_values(variable: netCDF4.Variable, index: Any) -> np.ndarray:
    values = np.ma.asarray(variable[index])
    if np.ma.count_masked(values):
        values = values.filled(np.nan)
    return np.asarray(values, dtype=np.float64)


def wind_vector(speed: float, direction_degrees: float) -> tuple[float, float]:
    angle = math.radians(direction_degrees)
    return -speed * math.sin(angle), -speed * math.cos(angle)


def read_wind_observations(path: Path) -> tuple[dict, dict]:
    records: dict[tuple[datetime, str], tuple[float, float]] = {}
    row_count = 0
    rejected_count = 0
    with path.open(encoding="utf-8", errors="strict", newline="") as stream:
        reader = csv.reader(stream, delimiter=";")
        header = next(reader)
        lower = [name.strip().lower() for name in header]
        required = ("meas_site", "termin", "nat_abbr", "fkl010h0", "dkl010h0")
        missing = [name for name in required if name not in lower]
        if missing:
            raise ValueError(f"observation CSV is missing columns: {missing}")
        positions = {name: lower.index(name) for name in required}
        for parameter in ("fkl010h0", "dkl010h0"):
            quality_index = positions[parameter] + 3
            if quality_index >= len(lower) or lower[quality_index] != "dq":
                raise ValueError(
                    f"observation column {parameter!r} is not followed by the expected dq field"
                )
        for row in reader:
            if not row or len(row) < len(header):
                continue
            row_count += 1
            speed = finite_float(row[positions["fkl010h0"]])
            direction = finite_float(row[positions["dkl010h0"]])
            speed_quality = finite_float(row[positions["fkl010h0"] + 3])
            direction_quality = finite_float(row[positions["dkl010h0"] + 3])
            if (
                speed is None
                or direction is None
                or speed_quality is None
                or direction_quality is None
                or speed_quality < 4.0
                or direction_quality < 4.0
            ):
                rejected_count += 1
                continue
            if speed < 0.0:
                raise ValueError(f"negative observed wind speed in row {row_count + 1}")
            if not 0.0 <= direction <= 360.0:
                raise ValueError(f"invalid observed wind direction in row {row_count + 1}")
            site_key = (
                f"{row[positions['nat_abbr']].strip()}:"
                f"{row[positions['meas_site']].strip()}"
            )
            valid = canonical_time(parse_time(row[positions["termin"]]))
            key = (valid, site_key)
            if key in records:
                raise ValueError(
                    f"duplicate QC-valid wind observation for {valid.isoformat()}/{site_key}"
                )
            records[key] = wind_vector(speed, direction % 360.0)
    return records, {
        "row_count": row_count,
        "qc_valid_wind_pair_count": len(records),
        "rejected_or_incomplete_wind_row_count": rejected_count,
    }


def wind_vector_metric(values: dict) -> tuple[int, float] | None:
    metric = values.get("wind_vector", {})
    count = metric.get("count")
    rmse = finite_float(metric.get("vector_root_mean_squared_error_m_s"))
    if not isinstance(count, int) or count <= 0 or rmse is None:
        return None
    return count, rmse


def load_evaluator_report(path: Path, worst_site_count: int) -> tuple[dict, list[dict], dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("issues"):
        raise ValueError(f"evaluator report contains issues: {payload['issues']}")
    mapping = payload.get("station_mapping", {})
    sites = mapping.get("sites")
    metrics = payload.get("site_metrics")
    matched_times = payload.get("matched_model_times")
    if not isinstance(sites, list) or not isinstance(metrics, dict):
        raise ValueError("evaluator report lacks station_mapping.sites or site_metrics")
    if not isinstance(matched_times, list) or not matched_times:
        raise ValueError("evaluator report has no matched_model_times")

    by_key: dict[str, dict] = {}
    for site in sites:
        key = site.get("key")
        if not isinstance(key, str) or not key or key in by_key:
            raise ValueError("evaluator station mapping has a missing or duplicate key")
        by_key[key] = site
    if set(metrics) != set(by_key):
        raise ValueError("evaluator site_metrics keys do not match station mapping")

    excluded_unequal_counts: list[str] = []
    ranked: list[tuple[float, str]] = []
    score_details: dict[str, dict] = {}
    for key, sources in metrics.items():
        hicar = wind_vector_metric(sources.get("hicar", {}))
        rea_l = wind_vector_metric(sources.get("rea_l", {}))
        if hicar is None or rea_l is None:
            continue
        if hicar[0] != rea_l[0]:
            excluded_unequal_counts.append(key)
            continue
        difference = hicar[1] - rea_l[1]
        ranked.append((difference, key))
        score_details[key] = {
            "pair_count": hicar[0],
            "hicar_vector_rmse_m_s": hicar[1],
            "rea_l_vector_rmse_m_s": rea_l[1],
            "hicar_minus_rea_l_vector_rmse_m_s": difference,
        }
    ranked.sort(key=lambda item: (-item[0], item[1]))
    worst_keys = {key for _, key in ranked[:worst_site_count]}

    selected: list[dict] = []
    for key, site in sorted(by_key.items()):
        elevation = finite_float(site.get("station_elevation_m"))
        relative = finite_float(site.get("terrain_relative_elevation_m"))
        reasons = []
        if relative is not None and relative > RIDGE_THRESHOLD_M:
            reasons.append("terrain_ridge_relative_gt_150m")
        if elevation is not None and elevation >= HIGH_ELEVATION_THRESHOLD_M:
            reasons.append("station_elevation_ge_2000m")
        if key in worst_keys:
            reasons.append(f"worst_{worst_site_count}_hicar_minus_rea_l_vector_rmse")
        if reasons:
            selected.append(
                {
                    **site,
                    "selection_reasons": reasons,
                    "evaluator_wind_vector": score_details.get(key),
                }
            )
    if not selected:
        raise ValueError("selection produced no stations")
    return payload, selected, {
        "ranked_wind_site_count": len(ranked),
        "worst_site_count_requested": worst_site_count,
        "worst_site_keys": [key for _, key in ranked[:worst_site_count]],
        "worst_site_candidates_excluded_unequal_pair_counts": sorted(
            excluded_unequal_counts
        ),
    }


@dataclass(frozen=True)
class FootprintSpec:
    radius_km: float
    y_slice: slice
    x_slice: slice
    mask: np.ndarray
    dy_cells: np.ndarray
    dx_cells: np.ndarray
    expected_cell_count: int
    actual_cell_count: int
    coverage_fraction: float
    clipped_by_domain: bool
    too_small: bool
    terrain_min_m: float
    terrain_max_m: float
    terrain_std_m: float


@dataclass
class FootprintAccumulator:
    spec: FootprintSpec
    fixed_squared_error_sum: np.ndarray = field(init=False)
    fixed_pair_count: np.ndarray = field(init=False)
    valid_observation_count: int = 0
    nearest_squared_error_sum: float = 0.0
    nearest_pair_count: int = 0
    mean_squared_error_sum: float = 0.0
    mean_pair_count: int = 0
    spatial_spreads: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.fixed_squared_error_sum = np.zeros(self.spec.actual_cell_count)
        self.fixed_pair_count = np.zeros(self.spec.actual_cell_count, dtype=np.int32)

    def add(
        self,
        u_field: np.ndarray,
        v_field: np.ndarray,
        nearest_y: int,
        nearest_x: int,
        observed_u: float,
        observed_v: float,
    ) -> None:
        self.valid_observation_count += 1
        nearest_u = float(u_field[nearest_y, nearest_x])
        nearest_v = float(v_field[nearest_y, nearest_x])
        if math.isfinite(nearest_u) and math.isfinite(nearest_v):
            self.nearest_squared_error_sum += (
                (nearest_u - observed_u) ** 2 + (nearest_v - observed_v) ** 2
            )
            self.nearest_pair_count += 1

        u_values = np.asarray(
            u_field[self.spec.y_slice, self.spec.x_slice][self.spec.mask],
            dtype=np.float64,
        )
        v_values = np.asarray(
            v_field[self.spec.y_slice, self.spec.x_slice][self.spec.mask],
            dtype=np.float64,
        )
        finite = np.isfinite(u_values) & np.isfinite(v_values)
        if not np.any(finite):
            return
        errors = (u_values[finite] - observed_u) ** 2 + (
            v_values[finite] - observed_v
        ) ** 2
        self.fixed_squared_error_sum[finite] += errors
        self.fixed_pair_count[finite] += 1
        mean_u = float(np.mean(u_values[finite]))
        mean_v = float(np.mean(v_values[finite]))
        self.mean_squared_error_sum += (
            (mean_u - observed_u) ** 2 + (mean_v - observed_v) ** 2
        )
        self.mean_pair_count += 1
        spread = math.sqrt(
            float(
                np.mean(
                    (u_values[finite] - mean_u) ** 2
                    + (v_values[finite] - mean_v) ** 2
                )
            )
        )
        self.spatial_spreads.append(spread)

    def result(self, include_optimistic_best_cell: bool) -> dict:
        complete = self.fixed_pair_count == self.valid_observation_count
        if self.valid_observation_count == 0:
            complete[:] = False
        fixed_rmse = np.sqrt(
            self.fixed_squared_error_sum[complete]
            / self.fixed_pair_count[complete]
        ) if np.any(complete) else np.asarray([], dtype=np.float64)
        result = {
            "radius_km": self.spec.radius_km,
            "geometry": {
                "expected_cell_count": self.spec.expected_cell_count,
                "actual_cell_count": self.spec.actual_cell_count,
                "coverage_fraction": self.spec.coverage_fraction,
                "clipped_by_domain": self.spec.clipped_by_domain,
                "too_small": self.spec.too_small,
            },
            "terrain_m": {
                "minimum": self.spec.terrain_min_m,
                "maximum": self.spec.terrain_max_m,
                "standard_deviation": self.spec.terrain_std_m,
            },
            "valid_observation_count": self.valid_observation_count,
            "nearest_cell": {
                "pair_count": self.nearest_pair_count,
                "vector_rmse_m_s": math.sqrt(
                    self.nearest_squared_error_sum / self.nearest_pair_count
                ) if self.nearest_pair_count else None,
            },
            "footprint_mean_vector": {
                "pair_count": self.mean_pair_count,
                "vector_rmse_m_s": math.sqrt(
                    self.mean_squared_error_sum / self.mean_pair_count
                ) if self.mean_pair_count else None,
            },
            "fixed_cell_vector_rmse_distribution_m_s": {
                "complete_cell_count": int(np.count_nonzero(complete)),
                "median": float(np.median(fixed_rmse)) if fixed_rmse.size else None,
                "p10": float(np.quantile(fixed_rmse, 0.10)) if fixed_rmse.size else None,
                "p90": float(np.quantile(fixed_rmse, 0.90)) if fixed_rmse.size else None,
            },
            "hourly_spatial_vector_spread_m_s": {
                "count": len(self.spatial_spreads),
                "mean": float(np.mean(self.spatial_spreads))
                if self.spatial_spreads else None,
                "median": float(np.median(self.spatial_spreads))
                if self.spatial_spreads else None,
                "p90": float(np.quantile(self.spatial_spreads, 0.90))
                if self.spatial_spreads else None,
                "maximum": max(self.spatial_spreads) if self.spatial_spreads else None,
            },
        }
        if include_optimistic_best_cell:
            if fixed_rmse.size:
                complete_indices = np.flatnonzero(complete)
                local_index = int(np.argmin(fixed_rmse))
                cell_index = int(complete_indices[local_index])
                result["optimistic_post_hoc_best_fixed_cell"] = {
                    "interpretation": (
                        "Retrospectively selected from observations; optimistic "
                        "diagnostic lower bound, not a primary skill score."
                    ),
                    "vector_rmse_m_s": float(fixed_rmse[local_index]),
                    "offset_y_cells": int(self.spec.dy_cells[cell_index]),
                    "offset_x_cells": int(self.spec.dx_cells[cell_index]),
                }
            else:
                result["optimistic_post_hoc_best_fixed_cell"] = None
        return result


def build_footprint_spec(
    terrain_variable: netCDF4.Variable,
    y_index: int,
    x_index: int,
    radius_km: float,
    dx_m: float,
) -> FootprintSpec:
    ny, nx = terrain_variable.shape
    radius_m = radius_km * 1_000.0
    radius_cells = int(math.ceil(radius_m / dx_m))
    full_offsets = np.arange(-radius_cells, radius_cells + 1)
    full_dy, full_dx = np.meshgrid(full_offsets, full_offsets, indexing="ij")
    expected_mask = np.hypot(full_dy * dx_m, full_dx * dx_m) <= radius_m + 1.0e-9
    expected_count = int(np.count_nonzero(expected_mask))

    y0 = max(0, y_index - radius_cells)
    y1 = min(ny, y_index + radius_cells + 1)
    x0 = max(0, x_index - radius_cells)
    x1 = min(nx, x_index + radius_cells + 1)
    dy, dx = np.meshgrid(
        np.arange(y0, y1) - y_index,
        np.arange(x0, x1) - x_index,
        indexing="ij",
    )
    mask = np.hypot(dy * dx_m, dx * dx_m) <= radius_m + 1.0e-9
    terrain = read_float_values(
        terrain_variable, (slice(y0, y1), slice(x0, x1))
    )[mask]
    if terrain.size == 0 or not np.all(np.isfinite(terrain)):
        raise ValueError(
            f"footprint at y={y_index}, x={x_index}, radius={radius_km} km has invalid terrain"
        )
    actual_count = int(np.count_nonzero(mask))
    coverage = actual_count / expected_count
    return FootprintSpec(
        radius_km=radius_km,
        y_slice=slice(y0, y1),
        x_slice=slice(x0, x1),
        mask=mask,
        dy_cells=np.asarray(dy[mask], dtype=np.int32),
        dx_cells=np.asarray(dx[mask], dtype=np.int32),
        expected_cell_count=expected_count,
        actual_cell_count=actual_count,
        coverage_fraction=coverage,
        clipped_by_domain=actual_count < expected_count,
        too_small=(
            actual_count < MINIMUM_FOOTPRINT_CELLS
            or coverage < MINIMUM_FOOTPRINT_COVERAGE
        ),
        terrain_min_m=float(np.min(terrain)),
        terrain_max_m=float(np.max(terrain)),
        terrain_std_m=float(np.std(terrain)),
    )


def validate_static_and_build_accumulators(
    static_path: Path,
    selected_sites: list[dict],
) -> tuple[tuple[int, int], dict[str, dict], dict]:
    site_runtime: dict[str, dict] = {}
    with netCDF4.Dataset(static_path) as dataset:
        for name in ("lat", "lon", "topo"):
            if name not in dataset.variables:
                raise ValueError(f"static file lacks {name!r}")
        terrain = dataset.variables["topo"]
        latitude = dataset.variables["lat"]
        longitude = dataset.variables["lon"]
        if terrain.ndim != 2 or latitude.shape != terrain.shape or longitude.shape != terrain.shape:
            raise ValueError("static lat/lon/topo do not share one two-dimensional grid")
        ny, nx = terrain.shape
        dx_m = finite_float(getattr(dataset, "hicar_dx_m", None))
        if dx_m is None or dx_m <= 0.0:
            raise ValueError("static file lacks a positive hicar_dx_m")
        latitude_mean = float(np.nanmean(latitude[:]))
        longitude_scale = 111.32 * math.cos(math.radians(latitude_mean))

        for site in selected_sites:
            key = site["key"]
            y_index = int(site["hicar_y_index"])
            x_index = int(site["hicar_x_index"])
            if not (0 <= y_index < ny and 0 <= x_index < nx):
                raise ValueError(f"mapped cell for {key} is outside the static grid")
            cell_elevation = float(terrain[y_index, x_index])
            report_elevation = float(site["hicar_elevation_m"])
            if abs(cell_elevation - report_elevation) > STATIC_ELEVATION_TOLERANCE_M:
                raise ValueError(
                    f"static/report elevation mismatch for {key}: "
                    f"{cell_elevation} versus {report_elevation} m"
                )
            cell_latitude = float(latitude[y_index, x_index])
            cell_longitude = float(longitude[y_index, x_index])
            distance_km = math.hypot(
                (cell_latitude - float(site["latitude"])) * 110.57,
                (cell_longitude - float(site["longitude"])) * longitude_scale,
            )
            report_distance = float(site["nearest_cell_distance_km"])
            distance_tolerance = max(0.02, 0.05 * max(report_distance, 1.0e-6))
            if abs(distance_km - report_distance) > distance_tolerance:
                raise ValueError(
                    f"static/report mapped-coordinate distance mismatch for {key}: "
                    f"{distance_km} versus {report_distance} km"
                )
            footprints = {
                radius: FootprintAccumulator(
                    build_footprint_spec(
                        terrain, y_index, x_index, radius, dx_m
                    )
                )
                for radius in FOOTPRINT_RADII_KM
            }
            site_runtime[key] = {
                "site": site,
                "y_index": y_index,
                "x_index": x_index,
                "cell_latitude": cell_latitude,
                "cell_longitude": cell_longitude,
                "footprints": footprints,
            }
    return (ny, nx), site_runtime, {
        "shape": [ny, nx],
        "dx_m": dx_m,
        "static_file": str(static_path.resolve()),
        "mapping_identity_verified": True,
    }


def inventory_outputs(
    paths: list[Path],
    grid_shape: tuple[int, int],
    site_runtime: dict[str, dict],
) -> tuple[list[tuple[Path, int, datetime]], list[dict]]:
    records: list[tuple[Path, int, datetime]] = []
    inventory = []
    seen: dict[datetime, Path] = {}
    for path in paths:
        with netCDF4.Dataset(path) as dataset:
            for name in ("time", "u10m", "v10m", "lat", "lon"):
                if name not in dataset.variables:
                    raise ValueError(f"HICAR output {path} lacks {name!r}")
            for name in ("u10m", "v10m"):
                if dataset.variables[name].shape[1:] != grid_shape:
                    raise ValueError(
                        f"HICAR output {path} {name} grid does not match static"
                    )
            if (
                dataset.variables["lat"].shape != grid_shape
                or dataset.variables["lon"].shape != grid_shape
            ):
                raise ValueError(f"HICAR output {path} coordinates do not match static shape")
            for key, runtime in site_runtime.items():
                y_index = runtime["y_index"]
                x_index = runtime["x_index"]
                output_latitude = float(dataset.variables["lat"][y_index, x_index])
                output_longitude = float(dataset.variables["lon"][y_index, x_index])
                if (
                    abs(output_latitude - runtime["cell_latitude"])
                    > COORDINATE_TOLERANCE_DEGREES
                    or abs(output_longitude - runtime["cell_longitude"])
                    > COORDINATE_TOLERANCE_DEGREES
                ):
                    raise ValueError(
                        f"HICAR output/static mapped-coordinate mismatch for {key} in {path}"
                    )
            times = decoded_times(dataset)
            if (
                dataset.variables["u10m"].shape[0] != len(times)
                or dataset.variables["v10m"].shape[0] != len(times)
            ):
                raise ValueError(f"HICAR output {path} time dimension is inconsistent")
            for index, valid in enumerate(times):
                if valid in seen:
                    raise ValueError(
                        f"duplicate HICAR time {valid.isoformat()} in {seen[valid]} and {path}"
                    )
                seen[valid] = path
                records.append((path, index, valid))
            inventory.append(
                {
                    "path": str(path.resolve()),
                    "time_count": len(times),
                    "first_time": times[0].isoformat() if times else None,
                    "last_time": times[-1].isoformat() if times else None,
                }
            )
    records.sort(key=lambda value: value[2])
    return records, inventory


def process_outputs(
    paths: list[Path],
    report_times: list[datetime],
    observations: dict,
    grid_shape: tuple[int, int],
    site_runtime: dict[str, dict],
) -> tuple[list[dict], dict]:
    records, inventory = inventory_outputs(paths, grid_shape, site_runtime)
    model_times = [value[2] for value in records]
    if model_times != report_times:
        missing = sorted(set(report_times) - set(model_times))
        extra = sorted(set(model_times) - set(report_times))
        raise ValueError(
            "HICAR output times do not exactly equal evaluator matched times; "
            f"missing={[value.isoformat() for value in missing]}, "
            f"extra={[value.isoformat() for value in extra]}"
        )

    by_file: dict[Path, list[tuple[int, datetime]]] = {}
    for path, index, valid in records:
        by_file.setdefault(path, []).append((index, valid))
    missing_observations: dict[str, list[str]] = {
        key: [] for key in site_runtime
    }
    for path in paths:
        with netCDF4.Dataset(path) as dataset:
            for index, valid in by_file.get(path, []):
                u_field = read_float_values(dataset.variables["u10m"], index)
                v_field = read_float_values(dataset.variables["v10m"], index)
                for key, runtime in site_runtime.items():
                    observed = observations.get((valid, key))
                    if observed is None:
                        missing_observations[key].append(valid.isoformat())
                        continue
                    for accumulator in runtime["footprints"].values():
                        accumulator.add(
                            u_field,
                            v_field,
                            runtime["y_index"],
                            runtime["x_index"],
                            observed[0],
                            observed[1],
                        )
    return inventory, {
        "model_times_exactly_match_evaluator": True,
        "matched_time_count": len(report_times),
        "missing_qc_observation_times_by_selected_site": {
            key: values for key, values in missing_observations.items() if values
        },
    }


def atomic_json_dump(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            json.dump(payload, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
            temporary = Path(stream.name)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluator-report", type=Path, required=True)
    parser.add_argument("--static-file", type=Path, required=True)
    parser.add_argument("--output-file", type=Path, action="append", required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--worst-site-count", type=int, default=5)
    parser.add_argument(
        "--include-optimistic-best-cell",
        action="store_true",
        help="include a clearly labelled post-hoc lower bound",
    )
    args = parser.parse_args(argv)
    if args.worst_site_count < 0:
        raise ValueError("--worst-site-count must be nonnegative")

    evaluator, selected_sites, selection_quality = load_evaluator_report(
        args.evaluator_report, args.worst_site_count
    )
    report_times = [canonical_time(parse_time(value)) for value in evaluator["matched_model_times"]]
    if report_times != sorted(set(report_times)):
        raise ValueError("evaluator matched_model_times are duplicate or unordered")
    observations, observation_inventory = read_wind_observations(args.observations)
    grid_shape, site_runtime, static_identity = validate_static_and_build_accumulators(
        args.static_file, selected_sites
    )
    output_inventory, pairing_quality = process_outputs(
        args.output_file,
        report_times,
        observations,
        grid_shape,
        site_runtime,
    )

    site_results = []
    too_small = []
    incomplete_fixed_cells = []
    for key, runtime in sorted(site_runtime.items()):
        footprint_results = {}
        for radius, accumulator in sorted(runtime["footprints"].items()):
            result = accumulator.result(args.include_optimistic_best_cell)
            radius_key = f"{radius:g}"
            footprint_results[radius_key] = result
            if result["geometry"]["too_small"]:
                too_small.append({"site_key": key, "radius_km": radius})
            if (
                result["fixed_cell_vector_rmse_distribution_m_s"][
                    "complete_cell_count"
                ]
                < result["geometry"]["actual_cell_count"]
            ):
                incomplete_fixed_cells.append({"site_key": key, "radius_km": radius})
        site = runtime["site"]
        site_results.append(
            {
                "site_key": key,
                "selection_reasons": site["selection_reasons"],
                "station_elevation_m": site["station_elevation_m"],
                "hicar_elevation_m": site["hicar_elevation_m"],
                "terrain_relative_elevation_m": site[
                    "terrain_relative_elevation_m"
                ],
                "nearest_cell_distance_km": site["nearest_cell_distance_km"],
                "hicar_y_index": runtime["y_index"],
                "hicar_x_index": runtime["x_index"],
                "evaluator_wind_vector": site["evaluator_wind_vector"],
                "footprints": footprint_results,
            }
        )

    payload = {
        "schema_version": 1,
        "interpretation": (
            "Point-to-grid representativeness diagnostic. Nearest-cell values "
            "remain the primary station score; footprint means and fixed-cell "
            "distributions quantify location sensitivity and are not an "
            "alternative observational truth."
        ),
        "limitations": [
            "HICAR winds are instantaneous while SwissMetNet winds are hourly aggregates.",
            "Native REA-L station samples have no equivalent fine-grid footprint.",
            "Any optional best cell is selected after seeing observations and is optimistic.",
        ],
        "sources": {
            "evaluator_report": str(args.evaluator_report.resolve()),
            "observations": str(args.observations.resolve()),
            "static": static_identity,
            "hicar_outputs": output_inventory,
        },
        "selection": {
            "ridge_threshold_m": RIDGE_THRESHOLD_M,
            "high_elevation_threshold_m": HIGH_ELEVATION_THRESHOLD_M,
            "selected_site_count": len(site_results),
            **selection_quality,
        },
        "data_quality": {
            "observation_inventory": observation_inventory,
            **pairing_quality,
            "minimum_footprint_coverage_fraction": MINIMUM_FOOTPRINT_COVERAGE,
            "too_small_footprints": too_small,
            "footprints_with_incomplete_fixed_cell_time_coverage": (
                incomplete_fixed_cells
            ),
        },
        "sites": site_results,
    }
    atomic_json_dump(args.report, payload)
    print(
        f"Diagnosed {len(site_results)} stations at {len(report_times)} times; "
        f"wrote {args.report}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
