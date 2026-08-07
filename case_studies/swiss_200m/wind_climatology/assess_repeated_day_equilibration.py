#!/usr/bin/env python3
"""Assess statistical equilibration across coupled repeated-day HICAR cycles."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import netCDF4
import numpy as np

try:
    from .assess_wind_spinup_mechanism import (
        compare_restarts,
        read_float,
        scalar_metrics,
    )
except ImportError:  # Direct script execution on Balfrin.
    from assess_wind_spinup_mechanism import (
        compare_restarts,
        read_float,
        scalar_metrics,
    )


SLOW_WATER_STORES = {
    "soil_column_total_water": "soil_column_total_water",
    "canopy_water": "canopy_water",
    "snow_water_equivalent": "swet",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_published(path: Path) -> dict[str, Any]:
    if not path.is_file() or not Path(f"{path}.ready").is_file():
        raise ValueError(f"publication is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("status") != "PASS":
        raise ValueError(f"publication is not PASS: {path}")
    return payload


def dataset_times(dataset: netCDF4.Dataset) -> list[datetime]:
    variable = dataset.variables["time"]
    decoded = netCDF4.num2date(
        variable[:],
        units=variable.units,
        calendar=getattr(variable, "calendar", "standard"),
        only_use_cftime_datetimes=False,
        only_use_python_datetimes=True,
    )
    return [
        datetime(
            value.year,
            value.month,
            value.day,
            value.hour,
            value.minute,
            value.second,
            value.microsecond,
        )
        for value in np.atleast_1d(decoded)
    ]


def history_index(
    paths: list[Path], start: datetime, end: datetime, cadence_seconds: int
) -> dict[datetime, tuple[Path, int]]:
    records: dict[datetime, tuple[Path, int]] = {}
    for path in paths:
        with netCDF4.Dataset(path) as dataset:
            for index, valid in enumerate(dataset_times(dataset)):
                elapsed = (valid - start).total_seconds()
                step = round(elapsed / cadence_seconds)
                snapped = start + timedelta(seconds=step * cadence_seconds)
                offset = abs((valid - snapped).total_seconds())
                if offset > 1.0:
                    raise ValueError(
                        f"history time is not within one second of cadence: {valid}"
                    )
                if start < snapped <= end:
                    if snapped in records:
                        raise ValueError(f"duplicate history time: {snapped}")
                    records[snapped] = (path, index)
    expected = []
    valid = start + timedelta(seconds=cadence_seconds)
    while valid <= end:
        expected.append(valid)
        valid += timedelta(seconds=cadence_seconds)
    if sorted(records) != expected:
        missing = sorted(set(expected) - set(records))
        extra = sorted(set(records) - set(expected))
        raise ValueError(f"history time coverage mismatch: missing={missing} extra={extra}")
    return records


def compare_wind_histories(
    candidate: dict[datetime, tuple[Path, int]],
    reference: dict[datetime, tuple[Path, int]],
) -> dict[str, Any]:
    if set(candidate) != set(reference):
        raise ValueError("cycle history times differ")
    accumulators: dict[str, dict[str, Any]] = {}
    handles: dict[Path, netCDF4.Dataset] = {}
    try:
        for valid in sorted(candidate):
            candidate_path, candidate_index = candidate[valid]
            reference_path, reference_index = reference[valid]
            for path in (candidate_path, reference_path):
                if path not in handles:
                    handles[path] = netCDF4.Dataset(path)
            candidate_ds = handles[candidate_path]
            reference_ds = handles[reference_path]
            heights = [10.0] + [
                float(value) for value in candidate_ds.variables["height_agl"][:]
            ]
            for height_index, height in enumerate(heights):
                key = f"{height:g}m"
                item = accumulators.setdefault(
                    key,
                    {
                        "sum_squared_error": 0.0,
                        "sum_reference_speed_squared": 0.0,
                        "sum_speed_difference": 0.0,
                        "count": 0,
                        "phase_mean_squared_error": [],
                        "candidate_first_u": None,
                        "candidate_first_v": None,
                        "reference_first_u": None,
                        "reference_first_v": None,
                        "candidate_temporally_constant": True,
                        "reference_temporally_constant": True,
                    },
                )
                if height_index == 0:
                    names = ("u10m", "v10m")
                    candidate_u = np.asarray(
                        candidate_ds.variables[names[0]][candidate_index], dtype=np.float64
                    )
                    candidate_v = np.asarray(
                        candidate_ds.variables[names[1]][candidate_index], dtype=np.float64
                    )
                    reference_u = np.asarray(
                        reference_ds.variables[names[0]][reference_index], dtype=np.float64
                    )
                    reference_v = np.asarray(
                        reference_ds.variables[names[1]][reference_index], dtype=np.float64
                    )
                else:
                    level = height_index - 1
                    candidate_u = np.asarray(
                        candidate_ds.variables["u_agl"][candidate_index, level],
                        dtype=np.float64,
                    )
                    candidate_v = np.asarray(
                        candidate_ds.variables["v_agl"][candidate_index, level],
                        dtype=np.float64,
                    )
                    reference_u = np.asarray(
                        reference_ds.variables["u_agl"][reference_index, level],
                        dtype=np.float64,
                    )
                    reference_v = np.asarray(
                        reference_ds.variables["v_agl"][reference_index, level],
                        dtype=np.float64,
                    )
                finite = (
                    np.isfinite(candidate_u)
                    & np.isfinite(candidate_v)
                    & np.isfinite(reference_u)
                    & np.isfinite(reference_v)
                )
                if not np.all(finite):
                    raise ValueError(f"non-finite wind at {valid} and {key}")
                du = candidate_u - reference_u
                dv = candidate_v - reference_v
                reference_speed = np.hypot(reference_u, reference_v)
                candidate_speed = np.hypot(candidate_u, candidate_v)
                item["sum_squared_error"] += float(np.sum(du * du + dv * dv))
                item["sum_reference_speed_squared"] += float(
                    np.sum(reference_speed * reference_speed)
                )
                item["sum_speed_difference"] += float(
                    np.sum(candidate_speed - reference_speed)
                )
                item["count"] += int(du.size)
                item["phase_mean_squared_error"].append(
                    float(np.mean(du)) ** 2 + float(np.mean(dv)) ** 2
                )
                if item["candidate_first_u"] is None:
                    item["candidate_first_u"] = candidate_u.copy()
                    item["candidate_first_v"] = candidate_v.copy()
                    item["reference_first_u"] = reference_u.copy()
                    item["reference_first_v"] = reference_v.copy()
                else:
                    item["candidate_temporally_constant"] = item[
                        "candidate_temporally_constant"
                    ] and np.array_equal(candidate_u, item["candidate_first_u"])
                    item["candidate_temporally_constant"] = item[
                        "candidate_temporally_constant"
                    ] and np.array_equal(candidate_v, item["candidate_first_v"])
                    item["reference_temporally_constant"] = item[
                        "reference_temporally_constant"
                    ] and np.array_equal(reference_u, item["reference_first_u"])
                    item["reference_temporally_constant"] = item[
                        "reference_temporally_constant"
                    ] and np.array_equal(reference_v, item["reference_first_v"])
    finally:
        for dataset in handles.values():
            dataset.close()

    result = {}
    for key, item in accumulators.items():
        count = item["count"]
        vector_rmse = math.sqrt(item["sum_squared_error"] / count)
        reference_rms = math.sqrt(item["sum_reference_speed_squared"] / count)
        result[key] = {
            "full_field_vector_rmse_m_s": vector_rmse,
            "relative_full_field_vector_rmse": vector_rmse / reference_rms,
            "mean_speed_change_m_s": item["sum_speed_difference"] / count,
            "phase_mean_vector_rmse_m_s": math.sqrt(
                float(np.mean(item["phase_mean_squared_error"]))
            ),
            "candidate_temporally_constant": bool(
                item["candidate_temporally_constant"]
            ),
            "reference_temporally_constant": bool(
                item["reference_temporally_constant"]
            ),
            "samples": count,
        }
    return result


def compare_slow_water_stores(
    candidate_path: Path, reference_path: Path
) -> dict[str, Any]:
    """Compare restart-persistent water stores at the repeated-day boundary."""
    candidate_total: np.ndarray | None = None
    reference_total: np.ndarray | None = None
    result: dict[str, Any] = {}
    with netCDF4.Dataset(candidate_path) as candidate, netCDF4.Dataset(
        reference_path
    ) as reference:
        for output_name, restart_name in SLOW_WATER_STORES.items():
            candidate_field = read_float(candidate, restart_name)
            reference_field = read_float(reference, restart_name)
            result[output_name] = scalar_metrics(candidate_field, reference_field)
            candidate_total = (
                candidate_field
                if candidate_total is None
                else candidate_total + candidate_field
            )
            reference_total = (
                reference_field
                if reference_total is None
                else reference_total + reference_field
            )
    if candidate_total is None or reference_total is None:  # pragma: no cover
        raise AssertionError("slow-water-store list is empty")
    result["combined_water_store"] = scalar_metrics(
        candidate_total, reference_total
    )
    return result


def assess_final_monotonic_trends(
    transitions: list[dict[str, Any]],
    final_transition_count: int,
    absolute_threshold: float,
) -> dict[str, Any]:
    """Flag material same-sign drift over the final repeated-day transitions."""
    selected = transitions[-final_transition_count:]
    stores = {}
    for name in (*SLOW_WATER_STORES, "combined_water_store"):
        biases = [
            float(item["metrics"]["slow_water_stores"][name]["mean_bias"])
            for item in selected
        ]
        same_sign = bool(biases) and (
            all(value > 0.0 for value in biases)
            or all(value < 0.0 for value in biases)
        )
        cumulative_change = float(sum(biases))
        material = same_sign and abs(cumulative_change) > absolute_threshold
        stores[name] = {
            "daily_mean_changes": biases,
            "cumulative_mean_change": cumulative_change,
            "same_sign": same_sign,
            "material_monotonic_drift": material,
        }
    return {
        "transitions_assessed": len(selected),
        "required_transitions": final_transition_count,
        "absolute_cumulative_threshold_kg_m2": absolute_threshold,
        "passes": len(selected) == final_transition_count
        and not any(item["material_monotonic_drift"] for item in stores.values()),
        "stores": stores,
    }


def transition_passes(metrics: dict[str, Any], thresholds: dict[str, Any]) -> bool:
    wind_pass = all(
        not wind["candidate_temporally_constant"]
        and not wind["reference_temporally_constant"]
        and wind["phase_mean_vector_rmse_m_s"]
        <= thresholds["wind_phase_mean_vector_rmse_m_s"]
        and abs(wind["mean_speed_change_m_s"])
        <= thresholds["wind_daily_mean_speed_change_m_s"]
        for wind in metrics["wind"].values()
    )
    restart = metrics["restart"]
    soil = restart["soil_state"]
    land_pass = (
        soil["soil_temperature"]["rmse"]
        <= thresholds["soil_temperature_boundary_rmse_K"]
        and soil["soil_water_content"]["rmse"]
        <= thresholds["soil_water_boundary_rmse_m3_m3"]
        and abs(
            metrics["slow_water_stores"]["soil_column_total_water"]["mean_bias"]
        )
        <= thresholds["soil_column_water_mean_change_kg_m2"]
    )
    return wind_pass and land_pass


def select_equilibrium(
    transitions: list[dict[str, Any]], consecutive_required: int
) -> int | None:
    run = 0
    for transition in transitions:
        run = run + 1 if transition["passes"] else 0
        if run >= consecutive_required:
            return int(transition["to_cycle"])
    return None


def assess(manifest_path: Path, output_path: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    start = datetime.fromisoformat(manifest["model_interval"]["start"])
    end = datetime.fromisoformat(manifest["model_interval"]["end"])
    cadence = int(manifest["output_interval_seconds"])
    cycles = sorted(manifest["cycles"], key=lambda item: int(item["cycle"]))
    if len(cycles) < 2:
        raise ValueError("at least two cycles are required")
    resolved = []
    for cycle in cycles:
        completion_path = Path(cycle["completion"])
        completion = require_published(completion_path)
        restart = Path(completion["restart"]["path"])
        if sha256(restart) != completion["restart"]["sha256"]:
            raise ValueError(f"restart checksum changed: {restart}")
        histories = [Path(path) for path in cycle["history_files"]]
        resolved.append(
            {
                "cycle": int(cycle["cycle"]),
                "completion": completion_path,
                "restart": restart,
                "history": history_index(histories, start, end, cadence),
            }
        )

    transitions = []
    thresholds = manifest["equilibration_rule"]
    for previous, current in zip(resolved, resolved[1:]):
        metrics = {
            "wind": compare_wind_histories(current["history"], previous["history"]),
            "restart": compare_restarts(
                current["restart"], previous["restart"], boundary_cells=20, sample_stride=4
            ),
            "slow_water_stores": compare_slow_water_stores(
                current["restart"], previous["restart"]
            ),
        }
        transition = {
            "from_cycle": previous["cycle"],
            "to_cycle": current["cycle"],
            "metrics": metrics,
        }
        transition["passes"] = transition_passes(metrics, thresholds)
        transitions.append(transition)

    selected = select_equilibrium(
        transitions, int(thresholds["consecutive_passing_transitions"])
    )
    final_trend = assess_final_monotonic_trends(
        transitions,
        final_transition_count=3,
        absolute_threshold=float(
            thresholds["soil_column_water_mean_change_kg_m2"]
        ),
    )
    if not final_trend["passes"]:
        selected = None
    decision = (
        f"STATISTICALLY_EQUILIBRATED_BY_CYCLE_{selected}"
        if selected is not None
        else (
            "SLOW_STORE_DRIFT_DETECTED"
            if final_trend["transitions_assessed"] == 3
            and not final_trend["passes"]
            else "EQUILIBRATION_NOT_BRACKETED"
        )
    )
    payload = {
        "schema_version": 1,
        "status": "PASS",
        "decision": decision,
        "selected_cycle": selected,
        "equilibration_time": (
            {
                "upper_bound_simulated_hours_from_initial_cycle_start": 24
                * selected,
                "repeated_exposure_hours_after_first_cycle": 24 * (selected - 1),
            }
            if selected is not None
            else None
        ),
        "cycles_assessed": len(cycles),
        "manifest": str(manifest_path.resolve()),
        "manifest_sha256": sha256(manifest_path),
        "transitions": transitions,
        "final_slow_store_trend": final_trend,
        "diagnostic_validity": {
            "fixed_height_wind_phase_diagnostic_valid": all(
                not metrics["candidate_temporally_constant"]
                and not metrics["reference_temporally_constant"]
                for transition in transitions
                for height, metrics in transition["metrics"]["wind"].items()
                if height != "10m"
            ),
            "wind_equilibration_gate_scope": (
                "All published 10 m and fixed-height AGL phase histories plus "
                "daily-boundary native-model wind. A temporally constant wind "
                "history is a fatal diagnostic defect."
            ),
        },
        "interpretation": (
            "Phase-conditioned means and slow stores determine statistical "
            "equilibration. Full-field RMSE is diagnostic because turbulent "
            "small-scale circulations need not become pixelwise periodic."
        ),
    }
    if output_path.exists() or Path(f"{output_path}.ready").exists():
        raise ValueError(f"refusing to replace assessment: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=output_path.parent, delete=False
    ) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, output_path)
    Path(f"{output_path}.ready").touch()
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = assess(args.manifest.resolve(), args.output.resolve())
    print(
        f"repeated-day assessment: {payload['decision']} "
        f"cycles={payload['cycles_assessed']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
