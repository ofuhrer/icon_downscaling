#!/usr/bin/env python3
"""Compare HICAR, interpolation-only control, and native REA-L wind skill."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import numpy as np


SEASONS = ("DJF", "MAM", "JJA", "SON")
METRICS = {
    "wind_speed_10m_m_s": "root_mean_squared_error",
    "wind_vector": "vector_root_mean_squared_error_m_s",
}


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


def rmse_across_stations(values: list[float]) -> float:
    return float(np.sqrt(np.mean(np.square(values))))


def compare(control: dict, evaluators: dict[str, dict], parity_tolerance: float) -> dict:
    if control.get("schema_version") != 1:
        raise ValueError("interpolation control must use schema_version 1")
    cohort = set.intersection(
        *(
            set(control["seasons"][season]["site_metrics"])
            & set(evaluators[season]["site_metrics"])
            for season in SEASONS
        )
    )
    if not cohort:
        raise ValueError("fixed four-season comparison cohort is empty")

    parity_differences = []
    events = []
    station_event_deltas = {
        metric: {comparison: [] for comparison in ("hicar_minus_control", "control_minus_rea_l")}
        for metric in METRICS
    }
    for season in SEASONS:
        control_sites = control["seasons"][season]["site_metrics"]
        evaluator_sites = evaluators[season]["site_metrics"]
        event = {"season": season, "station_count": len(cohort), "metrics": {}}
        for metric, field in METRICS.items():
            station_values = {source: [] for source in ("hicar", "control", "rea_l")}
            for key in sorted(cohort):
                control_rea = float(control_sites[key]["rea_l"][metric][field])
                evaluator_rea = float(evaluator_sites[key]["rea_l"][metric][field])
                parity_differences.append(abs(control_rea - evaluator_rea))
                values = {
                    "hicar": float(evaluator_sites[key]["hicar"][metric][field]),
                    "control": float(control_sites[key]["control"][metric][field]),
                    "rea_l": evaluator_rea,
                }
                for source, value in values.items():
                    if not math.isfinite(value):
                        raise ValueError(f"{season}/{key}/{source}/{metric}: non-finite RMSE")
                    station_values[source].append(value)
                station_event_deltas[metric]["hicar_minus_control"].append(
                    values["hicar"] - values["control"]
                )
                station_event_deltas[metric]["control_minus_rea_l"].append(
                    values["control"] - values["rea_l"]
                )

            aggregate = {
                source: rmse_across_stations(values)
                for source, values in station_values.items()
            }
            comparisons = {}
            for name, candidate, baseline in (
                ("hicar_minus_control", "hicar", "control"),
                ("control_minus_rea_l", "control", "rea_l"),
                ("hicar_minus_rea_l", "hicar", "rea_l"),
            ):
                delta = aggregate[candidate] - aggregate[baseline]
                classification, threshold = material_classification(delta, aggregate[baseline])
                comparisons[name] = {
                    "delta_rmse_m_s": delta,
                    "material_threshold_m_s": threshold,
                    "classification": classification,
                }
            event["metrics"][metric] = {
                "equal_station_rmse_m_s": aggregate,
                "comparisons": comparisons,
                "median_station_delta_hicar_minus_control_m_s": float(
                    np.median(
                        np.asarray(station_values["hicar"])
                        - np.asarray(station_values["control"])
                    )
                ),
                "median_station_delta_control_minus_rea_l_m_s": float(
                    np.median(
                        np.asarray(station_values["control"])
                        - np.asarray(station_values["rea_l"])
                    )
                ),
            }
        events.append(event)

    maximum_parity_difference = max(parity_differences, default=0.0)
    if maximum_parity_difference > parity_tolerance:
        raise ValueError(
            "control/evaluator REA-L station RMSE parity failed: "
            f"max difference {maximum_parity_difference:.17g} > {parity_tolerance:.17g}"
        )
    return {
        "schema_version": 1,
        "cohort_station_count": len(cohort),
        "cohort_station_keys": sorted(cohort),
        "rea_l_metric_parity": {
            "comparison_count": len(parity_differences),
            "maximum_absolute_difference_m_s": maximum_parity_difference,
            "tolerance_m_s": parity_tolerance,
            "passed": True,
        },
        "event_evidence": events,
        "station_event_medians": {
            metric: {
                name: float(np.median(values))
                for name, values in comparisons.items()
            }
            for metric, comparisons in station_event_deltas.items()
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control-report", required=True, type=Path)
    parser.add_argument("--evaluation-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--parity-tolerance", type=float, default=1.0e-12)
    args = parser.parse_args()
    control = json.loads(args.control_report.read_text(encoding="utf-8"))
    evaluators = {
        season: json.loads(
            (args.evaluation_root / season / "evaluator.json").read_text(encoding="utf-8")
        )
        for season in SEASONS
    }
    result = compare(control, evaluators, args.parity_tolerance)
    atomic_json(args.output, result)
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
