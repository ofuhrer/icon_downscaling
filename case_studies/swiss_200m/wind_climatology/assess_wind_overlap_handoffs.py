#!/usr/bin/env python3
"""Assess hard handoffs between overlapping short coupled HICAR windows.

The candidate schedule launches one independent 24-hour simulation every
12 hours.  Each member owns only its second 12 hours.  The simultaneous first
12 hours of the next member are used to quantify initialization uncertainty;
the fields are never blended.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from assess_wind_spinup_convergence import (
    HEIGHTS,
    WindHistory,
    check_physicality,
    compare_height,
    parse_time,
    require_published,
    sha256,
    write_json_atomic,
)


WINDOW_HOURS = 24
STRIDE_HOURS = 12
CORE_START_AGE_HOURS = 12
REQUIRED_SPINUP_HOURS = (0, 12, 24)


def inclusive_times(
    start: datetime,
    end: datetime,
    cadence: timedelta,
) -> list[datetime]:
    if cadence.total_seconds() <= 0:
        raise ValueError("output cadence must be positive")
    values: list[datetime] = []
    cursor = start
    while cursor <= end:
        values.append(cursor)
        cursor += cadence
    if not values or values[-1] != end:
        raise ValueError("time interval is off cadence")
    return values


def half_open_core_times(
    start_exclusive: datetime,
    end_inclusive: datetime,
    cadence: timedelta,
) -> list[datetime]:
    return inclusive_times(start_exclusive + cadence, end_inclusive, cadence)


def rms(values: np.ndarray) -> float:
    return math.sqrt(float(np.mean(np.square(values))))


def percentile(values: np.ndarray, quantile: float) -> float:
    return float(np.quantile(values, quantile, method="higher"))


def vector_change_metrics(
    first_u: np.ndarray,
    first_v: np.ndarray,
    second_u: np.ndarray,
    second_v: np.ndarray,
) -> dict[str, Any]:
    if not (first_u.shape == first_v.shape == second_u.shape == second_v.shape):
        raise ValueError("vector-change grid shapes differ")
    finite = (
        np.isfinite(first_u)
        & np.isfinite(first_v)
        & np.isfinite(second_u)
        & np.isfinite(second_v)
    )
    missing = int(finite.size - np.count_nonzero(finite))
    if not np.any(finite):
        raise ValueError("vector-change comparison has no finite samples")
    magnitude = np.hypot(
        second_u[finite] - first_u[finite],
        second_v[finite] - first_v[finite],
    )
    return {
        "sample_count": int(magnitude.size),
        "missing_count": missing,
        "rmse_m_s": rms(magnitude),
        "p99_m_s": percentile(magnitude, 0.99),
        "maximum_m_s": float(np.max(magnitude)),
    }


def seam_metrics(
    outgoing: WindHistory,
    incoming: WindHistory,
    boundary: datetime,
    cadence: timedelta,
    height: int,
) -> dict[str, Any]:
    following = boundary + cadence
    outgoing_u = outgoing.field("u", height, boundary)
    outgoing_v = outgoing.field("v", height, boundary)
    outgoing_next_u = outgoing.field("u", height, following)
    outgoing_next_v = outgoing.field("v", height, following)
    incoming_u = incoming.field("u", height, boundary)
    incoming_v = incoming.field("v", height, boundary)
    incoming_next_u = incoming.field("u", height, following)
    incoming_next_v = incoming.field("v", height, following)
    return {
        "height_agl_m": height,
        "boundary_member_difference": vector_change_metrics(
            outgoing_u,
            outgoing_v,
            incoming_u,
            incoming_v,
        ),
        "next_record_member_difference": vector_change_metrics(
            outgoing_next_u,
            outgoing_next_v,
            incoming_next_u,
            incoming_next_v,
        ),
        "stitched_record_change": vector_change_metrics(
            outgoing_u,
            outgoing_v,
            incoming_next_u,
            incoming_next_v,
        ),
        "outgoing_natural_record_change": vector_change_metrics(
            outgoing_u,
            outgoing_v,
            outgoing_next_u,
            outgoing_next_v,
        ),
        "incoming_natural_record_change": vector_change_metrics(
            incoming_u,
            incoming_v,
            incoming_next_u,
            incoming_next_v,
        ),
    }


def check_core_winds(
    history: WindHistory,
    times: list[datetime],
    maximum_speed_m_s: float,
) -> dict[str, Any]:
    diagnostics: list[dict[str, Any]] = []
    for height in HEIGHTS:
        missing = 0
        count = 0
        maximum = -math.inf
        for valid_time in times:
            u = history.field("u", height, valid_time)
            v = history.field("v", height, valid_time)
            if u.shape != v.shape:
                raise ValueError(f"u/v grid shape mismatch at {height} m")
            finite = np.isfinite(u) & np.isfinite(v)
            missing += int(finite.size - np.count_nonzero(finite))
            if np.any(finite):
                speed = np.hypot(u[finite], v[finite])
                count += int(speed.size)
                maximum = max(maximum, float(np.max(speed)))
        checks = {
            "has_samples": count > 0,
            "no_missing_values": missing == 0,
            "maximum_speed": maximum <= maximum_speed_m_s,
        }
        diagnostics.append(
            {
                "height_agl_m": height,
                "status": "PASS" if all(checks.values()) else "FAIL",
                "checks": checks,
                "sample_count": count,
                "missing_count": missing,
                "maximum_speed_m_s": maximum,
                "accepted_maximum_speed_m_s": maximum_speed_m_s,
            }
        )
    return {
        "status": (
            "PASS" if all(item["status"] == "PASS" for item in diagnostics) else "FAIL"
        ),
        "diagnostics": diagnostics,
    }


def load_bound_inputs(
    experiment_path: Path,
    results_path: Path,
    convergence_path: Path,
) -> tuple[dict[str, Any], dict[str, list[Path]], dict[str, Any]]:
    require_published(experiment_path, "experiment manifest")
    require_published(results_path, "results manifest")
    require_published(convergence_path, "convergence decision")
    experiment = json.loads(experiment_path.read_text())
    results = json.loads(results_path.read_text())
    convergence = json.loads(convergence_path.read_text())
    if results.get("schema_version") != 1:
        raise ValueError("results manifest schema_version must be 1")
    if results.get("experiment_sha256") != sha256(experiment_path):
        raise ValueError("results manifest is not bound to the experiment")
    if convergence.get("experiment_sha256") != sha256(experiment_path):
        raise ValueError("convergence decision is not bound to the experiment")
    if convergence.get("results_sha256") != sha256(results_path):
        raise ValueError("convergence decision is not bound to the results")
    if convergence.get("decision") != "MINIMUM_SPINUP_NOT_BRACKETED":
        raise ValueError("overlap assessment requires the failed convergence decision")
    completion_value = results.get("campaign_completion")
    completion_sha256 = results.get("campaign_completion_sha256")
    if completion_value is not None:
        completion_path = Path(completion_value)
        require_published(completion_path, "campaign completion")
        if sha256(completion_path) != completion_sha256:
            raise ValueError("results campaign-completion checksum mismatch")
        if json.loads(completion_path.read_text()).get("status") != "PASS":
            raise ValueError("results campaign completion is not PASS")
    paths: dict[str, list[Path]] = {}
    for item in results["runs"]:
        values = item.get("history_files")
        if values is None:
            values = [item["history_file"]]
        if not isinstance(values, list) or not values:
            raise ValueError(f"run {item['run_id']} has no history files")
        paths[item["run_id"]] = [Path(value).resolve() for value in values]
    run_ids = {run["run_id"] for run in experiment["runs"]}
    if set(paths) != run_ids:
        raise ValueError("results do not cover exactly the planned runs")
    return experiment, paths, convergence


def assess(
    *,
    experiment_path: Path,
    results_path: Path,
    convergence_path: Path,
    report_path: Path,
) -> dict[str, Any]:
    experiment, paths, convergence = load_bound_inputs(
        experiment_path,
        results_path,
        convergence_path,
    )
    candidates = {int(value) for value in experiment["candidate_spinup_hours"]}
    missing_candidates = sorted(set(REQUIRED_SPINUP_HOURS) - candidates)
    if missing_candidates:
        raise ValueError(f"experiment lacks required spin-up hours: {missing_candidates}")
    thresholds = experiment["thresholds"]
    maximum_speed_m_s = float(thresholds.get("maximum_wind_speed_m_s", 100.0))
    by_case_and_hours = {
        (run["case_id"], int(run["spinup_hours"])): run for run in experiment["runs"]
    }
    case_ids = list(dict.fromkeys(run["case_id"] for run in experiment["runs"]))
    case_reports: list[dict[str, Any]] = []
    for case_id in case_ids:
        selected_runs = {
            hours: by_case_and_hours[(case_id, hours)] for hours in REQUIRED_SPINUP_HOURS
        }
        target_start = parse_time(selected_runs[0]["retained_start_exclusive"])
        cadence_seconds = int(selected_runs[0]["output_interval_seconds"])
        if any(
            int(run["output_interval_seconds"]) != cadence_seconds
            for run in selected_runs.values()
        ):
            raise ValueError(f"case {case_id} has inconsistent output cadence")
        cadence = timedelta(seconds=cadence_seconds)
        handoff_specs = (
            (24, 12, target_start),
            (12, 0, target_start + timedelta(hours=STRIDE_HOURS)),
        )
        handoffs: list[dict[str, Any]] = []
        for outgoing_hours, incoming_hours, boundary in handoff_specs:
            outgoing_run = selected_runs[outgoing_hours]
            incoming_run = selected_runs[incoming_hours]
            outgoing = WindHistory(paths[outgoing_run["run_id"]])
            incoming = WindHistory(paths[incoming_run["run_id"]])
            try:
                overlap_times = inclusive_times(
                    boundary - timedelta(hours=STRIDE_HOURS),
                    boundary,
                    cadence,
                )
                overlap_by_height = [
                    compare_height(incoming, outgoing, overlap_times, height, thresholds)
                    for height in HEIGHTS
                ]
                boundary_by_height = [
                    compare_height(incoming, outgoing, [boundary], height, thresholds)
                    for height in HEIGHTS
                ]
                seams = [
                    seam_metrics(outgoing, incoming, boundary, cadence, height)
                    for height in HEIGHTS
                ]
                status = (
                    "PASS"
                    if all(item["status"] == "PASS" for item in overlap_by_height)
                    and all(item["status"] == "PASS" for item in boundary_by_height)
                    else "FAIL"
                )
                handoffs.append(
                    {
                        "status": status,
                        "boundary": boundary.strftime("%Y-%m-%dT%H:%M:%S"),
                        "outgoing_run_id": outgoing_run["run_id"],
                        "incoming_run_id": incoming_run["run_id"],
                        "outgoing_owned_age_hours": [12, 24],
                        "incoming_shadow_age_hours": [0, 12],
                        "overlap_record_count": len(overlap_times),
                        "overlap_by_height": overlap_by_height,
                        "boundary_by_height": boundary_by_height,
                        "record_change_diagnostics": seams,
                    }
                )
            finally:
                outgoing.close()
                incoming.close()

        core_specs = (
            (24, target_start - timedelta(hours=12), target_start),
            (12, target_start, target_start + timedelta(hours=12)),
            (0, target_start + timedelta(hours=12), target_start + timedelta(hours=24)),
        )
        cores: list[dict[str, Any]] = []
        for hours, start, end in core_specs:
            run = selected_runs[hours]
            history = WindHistory(paths[run["run_id"]])
            try:
                times = half_open_core_times(start, end, cadence)
                wind_physicality = check_core_winds(history, times, maximum_speed_m_s)
                diagnostic_physicality = check_physicality(history, times)
                cores.append(
                    {
                        "status": (
                            "PASS"
                            if wind_physicality["status"] == "PASS"
                            and diagnostic_physicality["status"] == "PASS"
                            else "FAIL"
                        ),
                        "run_id": run["run_id"],
                        "spinup_hours": hours,
                        "owned_start_exclusive": start.strftime("%Y-%m-%dT%H:%M:%S"),
                        "owned_end_inclusive": end.strftime("%Y-%m-%dT%H:%M:%S"),
                        "owned_record_count": len(times),
                        "wind_physicality": wind_physicality,
                        "diagnostic_physicality": diagnostic_physicality,
                    }
                )
            finally:
                history.close()
        case_status = (
            "PASS"
            if all(item["status"] == "PASS" for item in handoffs)
            and all(item["status"] == "PASS" for item in cores)
            else "FAIL"
        )
        case_reports.append(
            {
                "case_id": case_id,
                "status": case_status,
                "target_start": target_start.strftime("%Y-%m-%dT%H:%M:%S"),
                "handoffs": handoffs,
                "owned_cores": cores,
            }
        )

    passed = all(item["status"] == "PASS" for item in case_reports)
    payload = {
        "schema_version": 1,
        "status": "PASS" if passed else "HOLD",
        "decision": (
            "READY_FOR_OBSERVATIONAL_SKILL_GATE"
            if passed
            else "SHORT_WINDOW_HANDOFF_DISCONTINUITY"
        ),
        "experiment": str(experiment_path),
        "experiment_sha256": sha256(experiment_path),
        "results": str(results_path),
        "results_sha256": sha256(results_path),
        "source_convergence_decision": str(convergence_path),
        "source_convergence_decision_sha256": sha256(convergence_path),
        "source_convergence_status": convergence["status"],
        "source_convergence_decision_name": convergence["decision"],
        "method": {
            "simulation_window_hours": WINDOW_HOURS,
            "launch_stride_hours": STRIDE_HOURS,
            "owned_model_age_start_exclusive_hours": CORE_START_AGE_HOURS,
            "owned_model_age_end_inclusive_hours": WINDOW_HOURS,
            "field_blending": False,
            "ownership": "Each run owns (age 12 h, age 24 h].",
            "overlap_use": "handoff consistency and initialization uncertainty only",
        },
        "thresholds": thresholds,
        "cases": case_reports,
        "scope": (
            "Hard-handoff consistency and physicality only. PASS does not establish "
            "observational skill or authorize a Swiss, 100 m, or production campaign."
        ),
    }
    if report_path.exists() or Path(f"{report_path}.ready").exists():
        raise ValueError(f"refusing to replace existing assessment: {report_path}")
    write_json_atomic(report_path, payload)
    Path(f"{report_path}.ready").touch()
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--convergence-decision", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    payload = assess(
        experiment_path=args.experiment.resolve(),
        results_path=args.results.resolve(),
        convergence_path=args.convergence_decision.resolve(),
        report_path=args.report.resolve(),
    )
    print(f"wind overlap handoff assessment: {payload['status']} {payload['decision']}")
    return 0 if payload["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
