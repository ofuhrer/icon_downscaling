#!/usr/bin/env python3
"""Evaluate prepared target-grid wind without HICAR integration or projection.

The control samples hourly hicarprep earth-relative U/V on the same HICAR land
cells used by a completed evaluator report.  It interpolates the two lowest
mass levels linearly to 10 m AGL, forms civil-hour means from adjacent hourly
endpoints, and compares exact common finite triplets with SwissMetNet and the
native REA-L reference.  This isolates what the target-grid interpolation and
vertical reconstruction already provide before HICAR dynamics and physics.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

import netCDF4
import numpy as np


SEASONS = ("DJF", "MAM", "JJA", "SON")


def parse_time(value: str) -> datetime:
    stripped = value.strip()
    if len(stripped) == 14 and stripped.isdigit():
        parsed = datetime.strptime(stripped, "%Y%m%d%H%M%S").replace(
            tzinfo=timezone.utc
        )
    else:
        parsed = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def finite_float(value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return math.nan
    return parsed if math.isfinite(parsed) else math.nan


def read_observed_wind(path: Path) -> tuple[dict[datetime, dict[str, tuple[float, float]]], dict]:
    records: dict[datetime, dict[str, tuple[float, float]]] = {}
    rejected_quality_values = 0
    with path.open(encoding="utf-8", errors="strict", newline="") as stream:
        reader = csv.reader(stream, delimiter=";")
        header = next(reader)
        lower = [name.strip().lower() for name in header]
        required = {"meas_site", "termin", "nat_abbr", "fkl010h0", "dkl010h0"}
        missing = sorted(required - set(lower))
        if missing:
            raise ValueError(f"{path}: observation columns missing: {missing}")
        position = {name: lower.index(name) for name in required}
        for row in reader:
            if not row or len(row) < len(header):
                continue
            key = f"{row[position['nat_abbr']].strip()}:{row[position['meas_site']].strip()}"
            valid = parse_time(row[position["termin"]].strip())
            speed_index = position["fkl010h0"]
            direction_index = position["dkl010h0"]
            speed = finite_float(row[speed_index])
            direction = finite_float(row[direction_index])
            speed_quality = finite_float(row[speed_index + 3])
            direction_quality = finite_float(row[direction_index + 3])
            if all(
                math.isfinite(value)
                for value in (speed, direction, speed_quality, direction_quality)
            ) and min(speed_quality, direction_quality) >= 4.0:
                angle = math.radians(direction)
                records.setdefault(valid, {})[key] = (
                    -speed * math.sin(angle),
                    -speed * math.cos(angle),
                )
            elif math.isfinite(speed) or math.isfinite(direction):
                rejected_quality_values += 1
    return records, {"rejected_quality_values": rejected_quality_values}


def read_reference_wind(path: Path) -> dict[datetime, dict[str, tuple[float, float]]]:
    records: dict[datetime, dict[str, tuple[float, float]]] = {}
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"valid_time", "station_key", "u10m_ref", "v10m_ref"}
        missing = sorted(required - set(reader.fieldnames or ()))
        if missing:
            raise ValueError(f"{path}: native reference columns missing: {missing}")
        for row in reader:
            valid = parse_time(row["valid_time"])
            key = row["station_key"]
            if key in records.setdefault(valid, {}):
                raise ValueError(f"{path}: duplicate native reference row {valid}/{key}")
            records[valid][key] = (
                finite_float(row["u10m_ref"]),
                finite_float(row["v10m_ref"]),
            )
    return records


def forcing_time(dataset: netCDF4.Dataset) -> datetime:
    variable = dataset.variables["time"]
    values = netCDF4.num2date(
        variable[:],
        units=variable.units,
        calendar=getattr(variable, "calendar", "standard"),
        only_use_cftime_datetimes=False,
        only_use_python_datetimes=True,
    )
    if len(values) != 1:
        raise ValueError("interpolation-only forcing record must contain one time")
    value = values[0]
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0)


def paired_horizontal_samples(values: np.ndarray, station_count: int) -> np.ndarray:
    """Select paired (y, x) points from netCDF4 orthogonal-indexing output."""
    array = np.asarray(values, dtype=np.float64)
    expected_horizontal_shape = (station_count, station_count)
    if array.shape[-2:] != expected_horizontal_shape:
        raise ValueError(
            "unexpected orthogonal station-sampling shape "
            f"{array.shape}; expected trailing dimensions {expected_horizontal_shape}"
        )
    indices = np.arange(station_count)
    return array[..., indices, indices]


def sample_prepared_wind_10m(
    path: Path, y_indices: np.ndarray, x_indices: np.ndarray
) -> tuple[datetime, np.ndarray, np.ndarray]:
    with netCDF4.Dataset(path) as dataset:
        valid = forcing_time(dataset)
        required = ("U", "V", "HFL", "HSURF")
        missing = [name for name in required if name not in dataset.variables]
        if missing:
            raise ValueError(f"{path}: forcing variables missing: {missing}")
        if getattr(dataset, "wind_representation", "") != (
            "earth-relative U/V and terrain-adjusted W on exact target HFL mass levels; "
            "HICAR performs final grid rotation and variational projection"
        ):
            raise ValueError(f"{path}: unexpected wind representation")
        station_count = len(y_indices)
        u = paired_horizontal_samples(
            dataset.variables["U"][0, :2, y_indices, x_indices], station_count
        )
        v = paired_horizontal_samples(
            dataset.variables["V"][0, :2, y_indices, x_indices], station_count
        )
        hfl = paired_horizontal_samples(
            dataset.variables["HFL"][:2, y_indices, x_indices], station_count
        )
        hsurf = paired_horizontal_samples(
            dataset.variables["HSURF"][y_indices, x_indices], station_count
        )
    height = hfl - hsurf[None, :]
    denominator = height[1] - height[0]
    if not np.all(np.isfinite(height)) or np.any(denominator <= 0.0):
        raise ValueError(f"{path}: invalid lowest-level height geometry")
    weight = np.clip((10.0 - height[0]) / denominator, 0.0, 1.0)
    return valid, u[0] + weight * (u[1] - u[0]), v[0] + weight * (v[1] - v[0])


def scalar_metrics(model: np.ndarray, observation: np.ndarray) -> dict:
    difference = model - observation
    return {
        "count": int(model.size),
        "root_mean_squared_error": float(np.sqrt(np.mean(difference**2))),
        "bias": float(np.mean(difference)),
        "mean_absolute_error": float(np.mean(np.abs(difference))),
        "model_mean": float(np.mean(model)),
        "observation_mean": float(np.mean(observation)),
        "model_standard_deviation": float(np.std(model)),
        "observation_standard_deviation": float(np.std(observation)),
        "correlation": (
            float(np.corrcoef(model, observation)[0, 1])
            if model.size > 1 and np.std(model) > 0.0 and np.std(observation) > 0.0
            else None
        ),
    }


def vector_rmse(
    model_u: np.ndarray, model_v: np.ndarray, observation_u: np.ndarray, observation_v: np.ndarray
) -> float:
    return float(
        np.sqrt(np.mean((model_u - observation_u) ** 2 + (model_v - observation_v) ** 2))
    )


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def evaluate_season(
    label: str,
    evaluator_report: Path,
    observation_path: Path,
    reference_path: Path,
    forcing_dir: Path,
    forcing_prefix: str,
) -> dict:
    evaluator = json.loads(evaluator_report.read_text(encoding="utf-8"))
    sampling = evaluator["sampling"]
    evaluation_start = parse_time(sampling["evaluation_start_inclusive"])
    evaluation_end = parse_time(sampling["evaluation_end_inclusive"])
    expected_times = [evaluation_start + timedelta(hours=hour) for hour in range(25)]
    if [parse_time(value) for value in evaluator["matched_model_times"]] != expected_times:
        raise ValueError(f"{label}: evaluator matched times differ from the control window")
    sites = evaluator["station_mapping"]["sites"]
    keys = [site["key"] for site in sites]
    y_indices = np.asarray([site["hicar_y_index"] for site in sites], dtype=np.int64)
    x_indices = np.asarray([site["hicar_x_index"] for site in sites], dtype=np.int64)
    observations, observation_inventory = read_observed_wind(observation_path)
    reference = read_reference_wind(reference_path)
    control_endpoints: dict[datetime, tuple[np.ndarray, np.ndarray]] = {}
    input_records = []
    for valid in expected_times:
        path = forcing_dir / f"{forcing_prefix}{valid:%Y%m%d_%H%M}.nc"
        if not path.is_file() or not Path(f"{path}.ready").is_file():
            raise ValueError(f"{label}: forcing payload/ready pair missing: {path}")
        decoded, u, v = sample_prepared_wind_10m(path, y_indices, x_indices)
        if decoded != valid:
            raise ValueError(f"{path}: valid time {decoded} differs from {valid}")
        control_endpoints[valid] = (u, v)
        input_records.append({"path": str(path), "size_bytes": path.stat().st_size})

    station = {key: {source: {"u": [], "v": [], "speed": []} for source in ("control", "rea_l", "observation")} for key in keys}
    accepted = Counter()
    for valid in expected_times[1:]:
        previous = valid - timedelta(hours=1)
        control_previous_u, control_previous_v = control_endpoints[previous]
        control_current_u, control_current_v = control_endpoints[valid]
        reference_previous = reference.get(previous, {})
        reference_current = reference.get(valid, {})
        observed = observations.get(valid, {})
        for index, key in enumerate(keys):
            if key not in reference_previous or key not in reference_current or key not in observed:
                continue
            control_mean_u = 0.5 * (control_previous_u[index] + control_current_u[index])
            control_mean_v = 0.5 * (control_previous_v[index] + control_current_v[index])
            control_speed = 0.5 * (
                math.hypot(control_previous_u[index], control_previous_v[index])
                + math.hypot(control_current_u[index], control_current_v[index])
            )
            control_direction_speed = math.hypot(control_mean_u, control_mean_v)
            if control_direction_speed > 0.0:
                control_u = control_mean_u * control_speed / control_direction_speed
                control_v = control_mean_v * control_speed / control_direction_speed
            else:
                control_u = control_v = 0.0
            reference_previous_u, reference_previous_v = reference_previous[key]
            reference_current_u, reference_current_v = reference_current[key]
            reference_mean_u = 0.5 * (reference_previous_u + reference_current_u)
            reference_mean_v = 0.5 * (reference_previous_v + reference_current_v)
            reference_speed = 0.5 * (
                math.hypot(reference_previous_u, reference_previous_v)
                + math.hypot(reference_current_u, reference_current_v)
            )
            reference_direction_speed = math.hypot(reference_mean_u, reference_mean_v)
            if reference_direction_speed > 0.0:
                reference_u = reference_mean_u * reference_speed / reference_direction_speed
                reference_v = reference_mean_v * reference_speed / reference_direction_speed
            else:
                reference_u = reference_v = 0.0
            observation_u, observation_v = observed[key]
            observation_speed = math.hypot(observation_u, observation_v)
            values = (
                control_u, control_v, control_speed,
                reference_u, reference_v, reference_speed,
                observation_u, observation_v, observation_speed,
            )
            if not all(math.isfinite(value) for value in values):
                continue
            for source, u, v, speed in (
                ("control", control_u, control_v, control_speed),
                ("rea_l", reference_u, reference_v, reference_speed),
                ("observation", observation_u, observation_v, observation_speed),
            ):
                station[key][source]["u"].append(u)
                station[key][source]["v"].append(v)
                station[key][source]["speed"].append(speed)
            accepted[key] += 1

    site_metrics = {}
    for key in keys:
        if accepted[key] != 24:
            continue
        values = station[key]
        obs_u = np.asarray(values["observation"]["u"])
        obs_v = np.asarray(values["observation"]["v"])
        obs_speed = np.asarray(values["observation"]["speed"])
        site_metrics[key] = {}
        for source in ("control", "rea_l"):
            model_u = np.asarray(values[source]["u"])
            model_v = np.asarray(values[source]["v"])
            model_speed = np.asarray(values[source]["speed"])
            site_metrics[key][source] = {
                "wind_speed_10m_m_s": scalar_metrics(model_speed, obs_speed),
                "wind_vector": {
                    "count": 24,
                    "vector_root_mean_squared_error_m_s": vector_rmse(
                        model_u, model_v, obs_u, obs_v
                    ),
                },
            }
    return {
        "season": label,
        "evaluation_start_inclusive": evaluation_start.isoformat(),
        "evaluation_end_inclusive": evaluation_end.isoformat(),
        "matched_endpoint_count": len(expected_times),
        "scored_interval_count": 24,
        "mapped_station_count": len(keys),
        "complete_station_count": len(site_metrics),
        "observation_inventory": observation_inventory,
        "input_records": input_records,
        "site_metrics": site_metrics,
    }


def combined_decision(seasons: dict[str, dict]) -> dict:
    cohort = set.intersection(*(set(value["site_metrics"]) for value in seasons.values()))
    events = []
    for label in SEASONS:
        evidence = {"season": label, "station_count": len(cohort), "metrics": {}}
        for metric in ("wind_speed_10m_m_s", "wind_vector"):
            control = []
            reference = []
            for key in sorted(cohort):
                if metric == "wind_vector":
                    field = "vector_root_mean_squared_error_m_s"
                else:
                    field = "root_mean_squared_error"
                control.append(seasons[label]["site_metrics"][key]["control"][metric][field])
                reference.append(seasons[label]["site_metrics"][key]["rea_l"][metric][field])
            control_rmse = float(np.sqrt(np.mean(np.square(control))))
            reference_rmse = float(np.sqrt(np.mean(np.square(reference))))
            threshold = max(0.1, 0.05 * reference_rmse)
            delta = control_rmse - reference_rmse
            classification = (
                "material_improvement" if delta < -threshold
                else "material_degradation" if delta > threshold
                else "neutral"
            )
            evidence["metrics"][metric] = {
                "control_rmse_m_s": control_rmse,
                "rea_l_rmse_m_s": reference_rmse,
                "delta_control_minus_rea_l_m_s": delta,
                "material_threshold_m_s": threshold,
                "classification": classification,
                "median_station_delta_m_s": float(np.median(np.asarray(control) - np.asarray(reference))),
            }
        events.append(evidence)
    return {
        "cohort_station_count": len(cohort),
        "cohort_station_keys": sorted(cohort),
        "event_evidence": events,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluation-root", required=True, type=Path)
    parser.add_argument("--observation-reference-root", required=True, type=Path)
    parser.add_argument("--forcing-root", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    definitions = {
        "DJF": ("winter", "rea_l_hicar_"),
        "MAM": ("spring", "rea_l_hicar_"),
        "JJA": ("summer", "rea_l_hicar_"),
        "SON": ("autumn", "rea_l_hicar_"),
    }
    seasons = {}
    for label, (name, prefix) in definitions.items():
        seasons[label] = evaluate_season(
            label,
            args.evaluation_root / label / "evaluator.json",
            args.observation_reference_root / "observations" / f"{name}.csv",
            args.observation_reference_root / "reference" / f"{name}.csv",
            args.forcing_root / name,
            prefix,
        )
    report = {
        "schema_version": 1,
        "method": {
            "control": "hicarprep target-grid earth-relative U/V, no HICAR integration or projection",
            "vertical_sampling": "linear interpolation of the two lowest HFL mass levels to 10 m AGL, clipped to their bracket",
            "temporal_sampling": "adjacent hourly endpoints; scalar speed mean plus mean-vector direction",
            "pairing": "exact finite control/REA-L/SwissMetNet triplets",
        },
        "seasons": seasons,
        "decision_readout": combined_decision(seasons),
    }
    atomic_json(args.report, report)
    print(f"Wrote {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
