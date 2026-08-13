#!/usr/bin/env python3
"""Compare a bounded HICAR wind sensitivity with its exact baseline report."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import numpy as np


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def material_classification(delta: float, baseline_rmse: float) -> tuple[str, float]:
    threshold = max(0.1, 0.05 * baseline_rmse)
    if delta < -threshold:
        return "material_improvement", threshold
    if delta > threshold:
        return "material_degradation", threshold
    return "neutral", threshold


def rms(values: list[float]) -> float:
    return float(np.sqrt(np.mean(np.square(values))))


def station_value(report: dict, key: str, metric: str, field: str) -> float:
    value = float(report["site_metrics"][key]["hicar"][metric][field])
    if not math.isfinite(value):
        raise ValueError(f"{key}/{metric}/{field}: non-finite value")
    return value


def compare(baseline: dict, sensitivity: dict, cohort_keys: list[str]) -> dict:
    if baseline.get("schema_version") != 2 or sensitivity.get("schema_version") != 2:
        raise ValueError("both evaluator reports must use schema_version 2")
    if baseline["matched_model_times"] != sensitivity["matched_model_times"]:
        raise ValueError("baseline and sensitivity matched times differ")
    if len(baseline["matched_model_times"]) != 2:
        raise ValueError("bounded sensitivity must contain baseline plus one scored hour")

    baseline_sites = baseline["site_metrics"]
    sensitivity_sites = sensitivity["site_metrics"]
    cohort = sorted(set(cohort_keys) & set(baseline_sites) & set(sensitivity_sites))
    if cohort != sorted(cohort_keys):
        missing = sorted(set(cohort_keys) - set(cohort))
        raise ValueError(f"fixed cohort is incomplete: {missing}")

    mapping = {site["key"]: site for site in baseline["station_mapping"]["sites"]}
    if set(cohort) - set(mapping):
        raise ValueError("baseline station mapping does not cover fixed cohort")
    strata = {
        "all_stations": cohort,
        "station_elevation_ge_1500m": [
            key for key in cohort if float(mapping[key]["station_elevation_m"]) >= 1500.0
        ],
        "terrain_ridge_relative_gt_150m": [
            key
            for key in cohort
            if float(mapping[key]["terrain_relative_elevation_m"]) > 150.0
        ],
        "terrain_valley_relative_lt_minus_150m": [
            key
            for key in cohort
            if float(mapping[key]["terrain_relative_elevation_m"]) < -150.0
        ],
    }

    rea_parity_differences = []
    for key in cohort:
        for metric, field in (
            ("wind_speed_10m_m_s", "root_mean_squared_error"),
            ("wind_vector", "vector_root_mean_squared_error_m_s"),
        ):
            rea_parity_differences.append(
                abs(
                    float(baseline_sites[key]["rea_l"][metric][field])
                    - float(sensitivity_sites[key]["rea_l"][metric][field])
                )
            )
    maximum_rea_parity_difference = max(rea_parity_differences, default=0.0)
    if maximum_rea_parity_difference > 1.0e-12:
        raise ValueError(
            "baseline/sensitivity REA-L parity failed: "
            f"{maximum_rea_parity_difference:.17g}"
        )

    stratum_results = {}
    for stratum, keys in strata.items():
        if not keys:
            stratum_results[stratum] = {"station_count": 0, "metrics": {}}
            continue
        metrics = {}
        for metric, field in (
            ("wind_speed_10m_m_s", "root_mean_squared_error"),
            ("wind_vector", "vector_root_mean_squared_error_m_s"),
        ):
            baseline_values = [station_value(baseline, key, metric, field) for key in keys]
            sensitivity_values = [
                station_value(sensitivity, key, metric, field) for key in keys
            ]
            baseline_rmse = rms(baseline_values)
            sensitivity_rmse = rms(sensitivity_values)
            delta = sensitivity_rmse - baseline_rmse
            classification, threshold = material_classification(delta, baseline_rmse)
            metrics[metric] = {
                "baseline_equal_station_rmse_m_s": baseline_rmse,
                "sensitivity_equal_station_rmse_m_s": sensitivity_rmse,
                "delta_sensitivity_minus_baseline_m_s": delta,
                "material_threshold_m_s": threshold,
                "classification": classification,
                "median_station_delta_m_s": float(
                    np.median(np.asarray(sensitivity_values) - np.asarray(baseline_values))
                ),
            }
        stratum_results[stratum] = {"station_count": len(keys), "metrics": metrics}

    speed_changes = []
    for key in cohort:
        baseline_speed = station_value(
            baseline, key, "wind_speed_10m_m_s", "model_mean"
        )
        sensitivity_speed = station_value(
            sensitivity, key, "wind_speed_10m_m_s", "model_mean"
        )
        speed_changes.append(
            {
                "station_key": key,
                "station_elevation_m": float(mapping[key]["station_elevation_m"]),
                "terrain_relative_elevation_m": float(
                    mapping[key]["terrain_relative_elevation_m"]
                ),
                "baseline_speed_m_s": baseline_speed,
                "sensitivity_speed_m_s": sensitivity_speed,
                "delta_sensitivity_minus_baseline_m_s": (
                    sensitivity_speed - baseline_speed
                ),
            }
        )
    speed_changes.sort(
        key=lambda value: abs(value["delta_sensitivity_minus_baseline_m_s"]),
        reverse=True,
    )
    return {
        "schema_version": 1,
        "matched_model_times": baseline["matched_model_times"],
        "cohort_station_count": len(cohort),
        "rea_l_metric_parity": {
            "comparison_count": len(rea_parity_differences),
            "maximum_absolute_difference_m_s": maximum_rea_parity_difference,
            "passed": True,
        },
        "strata": stratum_results,
        "largest_absolute_station_speed_changes": speed_changes[:20],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-report", required=True, type=Path)
    parser.add_argument("--sensitivity-report", required=True, type=Path)
    parser.add_argument("--cohort-report", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    baseline = json.loads(args.baseline_report.read_text(encoding="utf-8"))
    sensitivity = json.loads(args.sensitivity_report.read_text(encoding="utf-8"))
    cohort = json.loads(args.cohort_report.read_text(encoding="utf-8"))[
        "cohort_station_keys"
    ]
    result = compare(baseline, sensitivity, cohort)
    atomic_json(args.output, result)
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
