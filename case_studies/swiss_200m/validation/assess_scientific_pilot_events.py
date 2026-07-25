#!/usr/bin/env python3
"""Apply predeclared go/no-go criteria to paired 200 m event pilots."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


CORE_STATION_METRICS = (
    "temperature_2m_height_adjusted_k",
    "relative_humidity_2m_percent",
    "surface_pressure_height_adjusted_pa",
    "wind_speed_10m_m_s",
    "precipitation_interval_kg_m2",
)


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def report_paths(run_dir: Path) -> dict[str, Path]:
    validation = run_dir / "scientific_validation"
    return {
        "model": run_dir / "model_chunk_completion.json",
        "restarts": validation / "restart_checkpoint_diagnostics.json",
        "solver": validation / "solver_log_diagnostics.json",
        "physical": validation / "scientific_event_diagnostics.json",
        "source": validation / "rea_l_source_comparison.json",
        "station": validation / "swissmetnet_comparison.json",
        "ogd_grid": validation / "ogd_grid_comparison.json",
    }


def assess_event(
    name: str,
    run_dir: Path,
    criteria: dict,
    expected_hicar_commit: str,
    expected_times: list[str],
) -> dict:
    paths = report_paths(run_dir)
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        return {
            "event": name,
            "run_dir": str(run_dir.resolve()),
            "complete": False,
            "decision": "INCOMPLETE",
            "missing_publications": missing,
            "screens": [],
            "failed_screens": ["required_publications"],
        }

    reports = {key: load_json(path) for key, path in paths.items()}
    screens: list[dict] = []

    def screen(screen_id: str, passed: bool, evidence: dict) -> None:
        screens.append({"id": screen_id, "passed": bool(passed), **evidence})

    required_status = criteria["required_status"]
    for key, report in reports.items():
        screen(
            f"ready_{key}",
            Path(f"{paths[key]}.ready").is_file(),
            {
                "observed": Path(f"{paths[key]}.ready").is_file(),
                "required": True,
                "path": str(Path(f"{paths[key]}.ready").resolve()),
            },
        )
        screen(
            f"status_{key}",
            report.get("status") == required_status,
            {
                "observed": report.get("status"),
                "required": required_status,
                "path": str(paths[key].resolve()),
            },
        )

    provenance = reports["model"].get("provenance", {})
    screen(
        "model_provenance",
        provenance.get("status") == required_status,
        {
            "observed": provenance.get("status"),
            "required": required_status,
        },
    )
    screen(
        "expected_hicar_source_commit",
        provenance.get("source_commit") == expected_hicar_commit,
        {
            "observed": provenance.get("source_commit"),
            "required": expected_hicar_commit,
        },
    )
    model_chunk_id = reports["model"].get("chunk_id")
    validation_event_names = {
        key: reports[key].get("event_name")
        for key in ("restarts", "physical", "source", "station", "ogd_grid")
    }
    screen(
        "cross_report_event_identity",
        isinstance(model_chunk_id, str)
        and bool(model_chunk_id)
        and all(
            value == model_chunk_id for value in validation_event_names.values()
        ),
        {
            "model_chunk_id": model_chunk_id,
            "validation_event_names": validation_event_names,
        },
    )
    restart_items = reports["restarts"].get("checkpoints", [])
    observed_restart_hours = [
        int(item.get("elapsed_hours"))
        for item in restart_items
        if item.get("elapsed_hours") is not None
    ]
    required_restart_hours = [
        int(value) for value in criteria["required_restart_checkpoint_hours"]
    ]
    screen(
        "restart_checkpoint_boundaries",
        observed_restart_hours == required_restart_hours
        and all(item.get("status") == required_status for item in restart_items),
        {
            "observed_hours": observed_restart_hours,
            "required_hours": required_restart_hours,
            "statuses": [item.get("status") for item in restart_items],
        },
    )

    expected_records = int(criteria["expected_output_records"])
    model_times = reports["model"].get("output", {}).get("times", [])
    screen(
        "output_record_count",
        len(model_times) == expected_records,
        {"observed": len(model_times), "required": expected_records},
    )
    normalized_model_times = [
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        .replace(tzinfo=None)
        .isoformat()
        for value in model_times
    ]
    screen(
        "model_time_axis",
        normalized_model_times == expected_times,
        {
            "observed_start": (
                normalized_model_times[0] if normalized_model_times else None
            ),
            "observed_end": (
                normalized_model_times[-1] if normalized_model_times else None
            ),
            "required_start": expected_times[0],
            "required_end": expected_times[-1],
            "required_interval_seconds": int(criteria["output_interval_seconds"]),
        },
    )
    station_times = reports["station"].get("matched_model_times", [])
    screen(
        "station_matched_time_count",
        len(station_times) == expected_records,
        {"observed": len(station_times), "required": expected_records},
    )
    normalized_station_times = [
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        .replace(tzinfo=None)
        .isoformat()
        for value in station_times
    ]
    screen(
        "station_time_axis",
        normalized_station_times == expected_times,
        {
            "observed_start": (
                normalized_station_times[0] if normalized_station_times else None
            ),
            "observed_end": (
                normalized_station_times[-1] if normalized_station_times else None
            ),
            "required_start": expected_times[0],
            "required_end": expected_times[-1],
        },
    )
    ogd = reports["ogd_grid"]
    expected_datetimes = [
        datetime.fromisoformat(value) for value in expected_times
    ]
    event_start = expected_datetimes[0]
    event_end = expected_datetimes[-1]
    expected_tabsd_days = []
    day = event_start.replace(hour=0, minute=0, second=0, microsecond=0)
    while day + timedelta(days=1) <= event_end:
        expected_tabsd_days.append(day.date().isoformat())
        day += timedelta(days=1)
    expected_rhiresd_windows = []
    day = event_start.replace(hour=0, minute=0, second=0, microsecond=0)
    while day.replace(hour=6) + timedelta(days=1) <= event_end:
        window_start = day.replace(hour=6)
        window_end = window_start + timedelta(days=1)
        expected_rhiresd_windows.append(
            {
                "rhires_day": day.date().isoformat(),
                "window_start": window_start.isoformat(),
                "window_end": window_end.isoformat(),
            }
        )
        day += timedelta(days=1)
    expected_sis_times = expected_times[1:]
    observed_rhiresd_windows = []
    for item in ogd.get("matched_daily_windows", []):
        if not isinstance(item, dict):
            observed_rhiresd_windows.append({"invalid": item})
            continue
        observed_rhiresd_windows.append(
            {
                key: (
                    datetime.fromisoformat(
                        str(item.get(key)).replace("Z", "+00:00")
                    )
                    .replace(tzinfo=None)
                    .isoformat()
                    if key in ("window_start", "window_end")
                    and item.get(key) is not None
                    else item.get(key)
                )
                for key in ("rhires_day", "window_start", "window_end")
            }
        )
    observed_tabsd_days = [
        str(value) for value in ogd.get("matched_temperature_days", [])
    ]
    observed_sis_times = [
        datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        .replace(tzinfo=None)
        .isoformat()
        for value in ogd.get("matched_radiation_times", [])
    ]
    screen(
        "complete_rhiresd_windows",
        observed_rhiresd_windows == expected_rhiresd_windows
        and len(observed_rhiresd_windows)
        >= int(criteria["minimum_complete_rhiresd_windows"]),
        {
            "observed": observed_rhiresd_windows,
            "required": expected_rhiresd_windows,
        },
    )
    screen(
        "complete_tabsd_days",
        observed_tabsd_days == expected_tabsd_days
        and len(observed_tabsd_days)
        >= int(criteria["minimum_complete_tabsd_days"]),
        {
            "observed": observed_tabsd_days,
            "required": expected_tabsd_days,
        },
    )
    screen(
        "matched_sis_times",
        observed_sis_times == expected_sis_times
        and len(observed_sis_times)
        >= int(criteria["minimum_matched_sis_times"]),
        {
            "observed": observed_sis_times,
            "required": expected_sis_times,
        },
    )

    energy_limit = float(
        criteria["maximum_interior_surface_energy_mean_absolute_residual_w_m2"]
    )
    energy = (
        reports["physical"]
        .get("classes", {})
        .get("active_soil_interior", {})
        .get("surface_energy_diagnostic", {})
        .get("mean_absolute_residual_w_m2")
    )
    screen(
        "interior_surface_energy_closure",
        energy is not None and float(energy) <= energy_limit,
        {"observed_w_m2": energy, "maximum_w_m2": energy_limit},
    )

    station_metrics = reports["station"].get("metrics", {})
    hicar = station_metrics.get("hicar", {}).get("all_sites", {})
    rea_l = station_metrics.get("rea_l", {}).get("all_sites", {})
    minimum_pairs = int(criteria["minimum_station_pairs_per_core_metric"])
    for source_name, source_metrics in (("hicar", hicar), ("rea_l", rea_l)):
        for metric in CORE_STATION_METRICS:
            count = source_metrics.get(metric, {}).get("count", 0)
            screen(
                f"station_pairs_{source_name}_{metric}",
                int(count) >= minimum_pairs,
                {
                    "observed": int(count),
                    "minimum": minimum_pairs,
                },
            )

    allowances = criteria["maximum_hicar_rmse_deterioration_relative_to_rea_l"]
    scalar_rules = (
        (
            "temperature_2m_height_adjusted_k",
            float(allowances["temperature_2m_height_adjusted_k_additive"]),
        ),
        (
            "relative_humidity_2m_percent",
            float(allowances["relative_humidity_2m_percent_additive"]),
        ),
        (
            "surface_pressure_height_adjusted_pa",
            float(allowances["surface_pressure_height_adjusted_pa_additive"]),
        ),
    )
    for metric, additive in scalar_rules:
        hicar_rmse = hicar.get(metric, {}).get("root_mean_squared_error")
        rea_l_rmse = rea_l.get(metric, {}).get("root_mean_squared_error")
        threshold = None if rea_l_rmse is None else float(rea_l_rmse) + additive
        screen(
            f"catastrophic_degradation_{metric}",
            hicar_rmse is not None
            and threshold is not None
            and float(hicar_rmse) <= threshold,
            {
                "hicar_rmse": hicar_rmse,
                "rea_l_rmse": rea_l_rmse,
                "maximum_hicar_rmse": threshold,
                "additive_allowance": additive,
            },
        )

    hicar_vector = hicar.get("wind_vector", {}).get(
        "vector_root_mean_squared_error_m_s"
    )
    rea_l_vector = rea_l.get("wind_vector", {}).get(
        "vector_root_mean_squared_error_m_s"
    )
    vector_additive = float(allowances["wind_vector_m_s_additive"])
    vector_threshold = (
        None if rea_l_vector is None else float(rea_l_vector) + vector_additive
    )
    screen(
        "catastrophic_degradation_wind_vector",
        hicar_vector is not None
        and vector_threshold is not None
        and float(hicar_vector) <= vector_threshold,
        {
            "hicar_rmse_m_s": hicar_vector,
            "rea_l_rmse_m_s": rea_l_vector,
            "maximum_hicar_rmse_m_s": vector_threshold,
            "additive_allowance_m_s": vector_additive,
        },
    )

    precipitation = "precipitation_interval_kg_m2"
    hicar_precipitation = hicar.get(precipitation, {}).get("root_mean_squared_error")
    rea_l_precipitation = rea_l.get(precipitation, {}).get("root_mean_squared_error")
    precipitation_threshold = (
        None
        if rea_l_precipitation is None
        else max(
            2.0 * float(rea_l_precipitation),
            float(rea_l_precipitation) + 2.0,
        )
    )
    screen(
        "catastrophic_degradation_precipitation_interval",
        hicar_precipitation is not None
        and precipitation_threshold is not None
        and float(hicar_precipitation) <= precipitation_threshold,
        {
            "hicar_rmse_kg_m2": hicar_precipitation,
            "rea_l_rmse_kg_m2": rea_l_precipitation,
            "maximum_hicar_rmse_kg_m2": precipitation_threshold,
            "rule": allowances["precipitation_interval_kg_m2_rule"],
        },
    )

    ogd_metrics = reports["ogd_grid"].get("metrics", {})
    tabsd = ogd_metrics.get("tabsd", {})
    hicar_tabsd = (
        tabsd.get("hicar", {})
        .get("interior_ge_10km", {})
        .get("root_mean_squared_error")
    )
    rea_l_tabsd = (
        tabsd.get("rea_l", {})
        .get("interior_ge_10km", {})
        .get("root_mean_squared_error")
    )
    tabsd_threshold = (
        None
        if rea_l_tabsd is None
        else float(rea_l_tabsd) + float(allowances["tabsd_temperature_k_additive"])
    )
    screen(
        "catastrophic_degradation_tabsd_temperature",
        hicar_tabsd is not None
        and tabsd_threshold is not None
        and float(hicar_tabsd) <= tabsd_threshold,
        {
            "hicar_rmse_k": hicar_tabsd,
            "rea_l_rmse_k": rea_l_tabsd,
            "maximum_hicar_rmse_k": tabsd_threshold,
            "additive_allowance_k": allowances["tabsd_temperature_k_additive"],
        },
    )

    rhiresd = ogd_metrics.get("rhiresd", {})
    hicar_rhiresd = (
        rhiresd.get("hicar", {})
        .get("interior_ge_10km", {})
        .get("root_mean_squared_error")
    )
    rea_l_rhiresd = (
        rhiresd.get("rea_l", {})
        .get("interior_ge_10km", {})
        .get("root_mean_squared_error")
    )
    rhiresd_threshold = (
        None
        if rea_l_rhiresd is None
        else max(2.0 * float(rea_l_rhiresd), float(rea_l_rhiresd) + 2.0)
    )
    screen(
        "catastrophic_degradation_rhiresd_precipitation",
        hicar_rhiresd is not None
        and rhiresd_threshold is not None
        and float(hicar_rhiresd) <= rhiresd_threshold,
        {
            "hicar_rmse_kg_m2": hicar_rhiresd,
            "rea_l_rmse_kg_m2": rea_l_rhiresd,
            "maximum_hicar_rmse_kg_m2": rhiresd_threshold,
            "rule": allowances["rhiresd_precipitation_rule"],
        },
    )

    failed = [item["id"] for item in screens if not item["passed"]]
    return {
        "event": name,
        "run_dir": str(run_dir.resolve()),
        "complete": True,
        "decision": "PASS" if not failed else "HOLD_AND_DIAGNOSE",
        "screens": screens,
        "failed_screens": failed,
        "report_paths": {key: str(path.resolve()) for key, path in paths.items()},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument(
        "--event",
        action="append",
        required=True,
        metavar="NAME=RUN_DIR",
        help="Paired event name and completed run directory",
    )
    parser.add_argument(
        "--restart-trajectory-report",
        required=True,
        type=Path,
    )
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    plan = load_json(args.plan)
    criteria = plan["promotion_criteria"]["event_to_month"]
    criteria = {
        **criteria,
        "output_interval_seconds": plan["configuration"]["output_interval_seconds"],
    }
    expected_hicar_commit = plan["configuration"]["event_expected_hicar_commit"]
    trajectory_contract = criteria["restart_trajectory_gate"]
    trajectory_path = args.restart_trajectory_report
    trajectory_ready = Path(f"{trajectory_path}.ready")
    trajectory_complete = trajectory_path.is_file() and trajectory_ready.is_file()
    trajectory = json.loads(trajectory_path.read_text()) if trajectory_complete else {}
    expected_trajectory_times = []
    trajectory_start = datetime.fromisoformat(
        trajectory_contract["start_exclusive"]
    )
    trajectory_end = datetime.fromisoformat(trajectory_contract["end_inclusive"])
    trajectory_interval = timedelta(
        seconds=int(criteria["output_interval_seconds"])
    )
    valid = trajectory_start + trajectory_interval
    while valid <= trajectory_end:
        expected_trajectory_times.append(valid.isoformat())
        valid += trajectory_interval
    normalized_trajectory_times = [
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        .replace(tzinfo=None)
        .isoformat()
        for value in trajectory.get("expected_times", [])
    ]
    trajectory_passed = (
        trajectory_complete
        and trajectory.get("status") == "PASS"
        and not trajectory.get("failures")
        and normalized_trajectory_times == expected_trajectory_times
        and len(normalized_trajectory_times)
        == int(trajectory_contract["expected_comparison_records"])
    )
    events = []
    event_names = []
    for value in args.event:
        if "=" not in value:
            raise SystemExit(f"--event must be NAME=RUN_DIR, got {value!r}")
        name, run_dir = value.split("=", 1)
        event_names.append(name)
        period = plan["reference_periods"].get(f"{name}_event")
        if period is None:
            events.append(
                {
                    "event": name,
                    "run_dir": str(Path(run_dir).resolve()),
                    "complete": False,
                    "decision": "INCOMPLETE",
                    "missing_publications": [],
                    "screens": [],
                    "failed_screens": ["event_identity"],
                }
            )
            continue
        start = datetime.fromisoformat(period["start"])
        interval = timedelta(seconds=int(criteria["output_interval_seconds"]))
        expected_times = [
            (start + index * interval).isoformat()
            for index in range(
                int(period["duration_hours"]) * 3600
                // int(criteria["output_interval_seconds"])
                + 1
            )
        ]
        events.append(
            assess_event(
                name,
                Path(run_dir),
                criteria,
                expected_hicar_commit,
                expected_times,
            )
        )

    incomplete = [event["event"] for event in events if not event["complete"]]
    if not trajectory_complete:
        incomplete.append("restart trajectory publication is missing")
    if len(event_names) != 2 or set(event_names) != {"summer", "winter"}:
        incomplete.append(
            "event names must be exactly one summer and one winter event"
        )
    passing = [event for event in events if event["decision"] == "PASS"]
    failed_sets = [
        set(event["failed_screens"]) for event in events if event["complete"]
    ]
    systematic = sorted(set.intersection(*failed_sets)) if failed_sets else []
    if incomplete:
        decision = "INCOMPLETE"
    elif len(events) != 2:
        decision = "INCOMPLETE"
        incomplete.append(f"expected two seasonal events, received {len(events)}")
    elif len(passing) == len(events) and trajectory_passed:
        decision = "GO_MONTH_AND_100M_CAPACITY_GATE"
    elif systematic:
        decision = "STOP_AND_REDESIGN"
    else:
        decision = "HOLD_AND_DIAGNOSE"

    payload = {
        "schema_version": 1,
        "assessment_status": "INCOMPLETE" if incomplete else "COMPLETE",
        "decision": decision,
        "interpretation": criteria["interpretation"],
        "plan": str(args.plan.resolve()),
        "events": events,
        "restart_trajectory": {
            "path": str(trajectory_path.resolve()),
            "complete": trajectory_complete,
            "passed": trajectory_passed,
            "status": trajectory.get("status"),
            "observed_times": normalized_trajectory_times,
            "required_times": expected_trajectory_times,
            "failures": trajectory.get("failures", []),
            "interpretation": trajectory_contract["interpretation"],
        },
        "systematic_failed_screens": systematic,
        "incomplete_reasons": incomplete,
        "authorization": {
            "month_pilot": decision == "GO_MONTH_AND_100M_CAPACITY_GATE",
            "100m_engineering_capacity_gate": (
                decision == "GO_MONTH_AND_100M_CAPACITY_GATE"
            ),
            "annual_cycle": False,
            "twenty_year_200m_production": False,
            "100m_scientific_production": False,
        },
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=args.report.parent, delete=False
    ) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, args.report)
    if incomplete:
        return 1
    Path(f"{args.report}.ready").touch()
    print(f"{decision}: paired 200 m event assessment is complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
