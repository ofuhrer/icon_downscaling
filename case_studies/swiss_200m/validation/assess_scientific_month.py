#!/usr/bin/env python3
"""Apply the frozen month-to-annual HICAR qualification criteria."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from month_source_contract import validate_month_source_qualification


CORE_STATION_METRICS = (
    "temperature_2m_height_adjusted_k",
    "relative_humidity_2m_percent",
    "surface_pressure_height_adjusted_pa",
    "wind_speed_10m_m_s",
    "precipitation_interval_kg_m2",
    "wind_vector",
)
ALLOWED_DRIFT_CLASSIFICATIONS = {
    "forcing_consistent",
    "physically_explained_hicar_bias",
    "unexplained",
}


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def ready(path: Path) -> bool:
    return path.is_file() and Path(f"{path}.ready").is_file()


def expected_times(month: dict) -> list[str]:
    start = datetime.fromisoformat(month["start"])
    end = datetime.fromisoformat(month["end"])
    interval = timedelta(seconds=int(month["output_interval_seconds"]))
    values = []
    valid = start
    while valid <= end:
        values.append(valid.isoformat())
        valid += interval
    return values


def normalized_time(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=None).isoformat()


def station_metric(report: dict, source: str, metric: str) -> dict:
    return (
        report.get("metrics", {})
        .get(source, {})
        .get("all_sites", {})
        .get(metric, {})
    )


def ogd_rmse(report: dict, product: str, source: str) -> float | None:
    value = (
        report.get("metrics", {})
        .get(product, {})
        .get(source, {})
        .get("interior_ge_10km", {})
        .get("root_mean_squared_error")
    )
    return None if value is None else float(value)


def entry_count(value) -> int:
    if isinstance(value, list):
        return len(value)
    return int(value)


def assess(month: dict, scientific: dict) -> tuple[dict, bool]:
    criteria = scientific["promotion_criteria"]["month_to_annual_cycle"]
    screens: list[dict] = []
    incomplete: list[str] = []
    reports: dict[str, dict] = {}

    def screen(screen_id: str, passed: bool, **evidence) -> None:
        screens.append({"id": screen_id, "passed": bool(passed), **evidence})

    def publication(
        key: str,
        path_value: str,
        require_ready_marker: bool = True,
    ) -> dict | None:
        path = Path(path_value)
        if not path.is_file() or (
            require_ready_marker and not Path(f"{path}.ready").is_file()
        ):
            incomplete.append(f"{key} is not published: {path}")
            return None
        payload = load_json(path)
        reports[key] = payload
        return payload

    segment_reports = []
    restart_retirements = []
    segment_times: list[str] = []
    for segment in month["segments"]:
        sequence = int(segment["sequence"])
        model = publication(
            f"segment_{sequence:02d}_model",
            segment["model_completion_report"],
        )
        solver = publication(
            f"segment_{sequence:02d}_solver",
            segment["solver_report"],
        )
        compression = publication(
            f"segment_{sequence:02d}_compression",
            segment["compression_report"],
            require_ready_marker=False,
        )
        retirement = publication(
            f"segment_{sequence:02d}_forcing_retirement",
            segment["forcing_retirement_report"],
        )
        segment_reports.append(
            (segment, model, solver, compression, retirement)
        )
        if model is not None:
            segment_times.extend(
                normalized_time(value)
                for value in model.get("output", {}).get("times", [])
            )
    for previous, successor in zip(
        month["segments"][:-1], month["segments"][1:]
    ):
        sequence = int(previous["sequence"])
        retirement = publication(
            f"segment_{sequence:02d}_restart_retirement",
            previous["restart_retirement_report"],
        )
        restart_retirements.append((previous, successor, retirement))

    overlap = month["uninterrupted_restart_overlap"]
    overlap_model = publication(
        "restart_overlap_model",
        overlap["model_completion_report"],
    )
    publication(
        "restart_overlap_solver",
        overlap["solver_report"],
    )
    overlap_compression = publication(
        "restart_overlap_compression",
        overlap["compression_report"],
        require_ready_marker=False,
    )
    overlap_retirement = publication(
        "restart_overlap_forcing_retirement",
        overlap["forcing_retirement_report"],
    )
    trajectory = publication(
        "restart_trajectory",
        month["restart_trajectory_report"],
    )
    validation_paths = month["validation_reports"]
    physical = publication("physical", validation_paths["physical"])
    publication("rea_l_source", validation_paths["rea_l_source"])
    station = publication("swissmetnet", validation_paths["swissmetnet"])
    ogd = publication("ogd_grid", validation_paths["ogd_grid"])
    drift = publication("drift_screen", validation_paths["drift_screen"])
    source_qualification = publication(
        "month_source_qualification",
        month["source_qualification_report"],
    )

    archive_path = Path(month["archive_contract"])
    if not archive_path.is_file():
        incomplete.append(f"archive contract is missing: {archive_path}")
        archive = None
    else:
        archive = load_json(archive_path)
    quality_path = Path(month["observational_validation_contract"])
    if not quality_path.is_file():
        incomplete.append(
            f"observational validation contract is missing: {quality_path}"
        )
        quality = None
    else:
        quality = load_json(quality_path)

    if incomplete:
        return (
            {
                "schema_version": 1,
                "assessment_status": "INCOMPLETE",
                "decision": "INCOMPLETE",
                "month_plan": month.get("_plan_path"),
                "incomplete_reasons": incomplete,
                "screens": screens,
                "failed_screens": ["required_publications"],
                "authorization": {
                    "annual_cycle": False,
                    "twenty_year_200m_production": False,
                    "100m_scientific_production": False,
                },
            },
            False,
        )

    required_status = criteria["required_report_status"]
    for name, report in reports.items():
        screen(
            f"status_{name}",
            report.get("status") == required_status,
            observed=report.get("status"),
            required=required_status,
        )

    source_qualification_failures = validate_month_source_qualification(
        source_qualification,
        expected_child_commit=month.get("expected_hicar_commit"),
        required_parent_commit=month.get("required_parent_hicar_commit"),
    )
    source_qualification_path = Path(month["source_qualification_report"])
    observed_source_qualification_sha256 = None
    if source_qualification_path.is_file():
        import hashlib

        observed_source_qualification_sha256 = hashlib.sha256(
            source_qualification_path.read_bytes()
        ).hexdigest()
    screen(
        "month_source_output_diagnostic_only",
        not source_qualification_failures
        and observed_source_qualification_sha256
        == month.get("source_qualification_sha256"),
        failures=source_qualification_failures,
        observed_sha256=observed_source_qualification_sha256,
        required_sha256=month.get("source_qualification_sha256"),
        child_commit=source_qualification.get("child_commit"),
        parent_commit=source_qualification.get("parent_commit"),
    )

    planned_times = expected_times(month)
    screen(
        "unique_monotonic_month_output_times",
        segment_times == planned_times
        and len(set(segment_times)) == len(planned_times),
        observed_count=len(segment_times),
        unique_count=len(set(segment_times)),
        required_count=len(planned_times),
        observed_start=segment_times[0] if segment_times else None,
        observed_end=segment_times[-1] if segment_times else None,
        required_start=planned_times[0],
        required_end=planned_times[-1],
    )
    screen(
        "frozen_month_output_record_count",
        len(planned_times) == int(criteria["expected_unique_output_records"]),
        observed=len(planned_times),
        required=int(criteria["expected_unique_output_records"]),
    )
    station_times = [
        normalized_time(value)
        for value in station.get("matched_model_times", [])
    ]
    screen(
        "station_time_axis",
        station_times == planned_times,
        observed_count=len(station_times),
        unique_count=len(set(station_times)),
        required_count=len(planned_times),
        observed_start=station_times[0] if station_times else None,
        observed_end=station_times[-1] if station_times else None,
        required_start=planned_times[0],
        required_end=planned_times[-1],
    )

    for segment, model, solver, compression, retirement in segment_reports:
        sequence = int(segment["sequence"])
        provenance = model.get("provenance", {})
        screen(
            f"segment_{sequence:02d}_production_provenance",
            provenance.get("status") == "PASS",
            observed=provenance.get("status"),
            required="PASS",
            source_commit=provenance.get("source_commit"),
            executable_sha256=provenance.get("executable_sha256"),
            static_sha256=provenance.get("static_sha256"),
            forcing_publication_sha256=provenance.get(
                "forcing_publication_sha256"
            ),
        )
        model_count = len(model.get("output", {}).get("times", []))
        screen(
            f"segment_{sequence:02d}_record_count",
            model_count == int(segment["expected_output_records"]),
            observed=model_count,
            required=int(segment["expected_output_records"]),
        )
        screen(
            f"segment_{sequence:02d}_forcing_retired",
            retirement.get("action") == "RETIRED"
            and retirement.get("execute") is True
            and retirement.get("forcing_publication_ready_withdrawn") is True
            and int(retirement.get("payload_count", -1))
            == int(segment["forcing_record_count"]),
            action=retirement.get("action"),
            execute=retirement.get("execute"),
            forcing_publication_ready_withdrawn=retirement.get(
                "forcing_publication_ready_withdrawn"
            ),
            observed_payload_count=retirement.get("payload_count"),
            required_payload_count=int(segment["forcing_record_count"]),
            payload_bytes=retirement.get("payload_bytes"),
        )
        target = Path(compression.get("target", ""))
        planned_target = Path(segment["compressed_output_file"])
        screen(
            f"segment_{sequence:02d}_compressed_publication",
            target == planned_target
            and target.is_file()
            and target.stat().st_size > 0
            and Path(f"{target}.ready").is_file(),
            observed_target=str(target),
            planned_target=str(planned_target),
            target_bytes=compression.get("target_bytes"),
        )

    for previous, successor, retirement in restart_retirements:
        sequence = int(previous["sequence"])
        screen(
            f"segment_{sequence:02d}_restart_retired",
            retirement.get("action") == "RETIRED"
            and retirement.get("execute") is True
            and retirement.get("previous_end") == previous["end"]
            and retirement.get("next_end") == successor["end"],
            action=retirement.get("action"),
            execute=retirement.get("execute"),
            observed_previous_end=retirement.get("previous_end"),
            required_previous_end=previous["end"],
            observed_next_end=retirement.get("next_end"),
            required_next_end=successor["end"],
            previous_restart_bytes=retirement.get("previous_restart_bytes"),
        )

    overlap_provenance = overlap_model.get("provenance", {})
    screen(
        "restart_overlap_production_provenance",
        overlap_provenance.get("status") == "PASS",
        observed=overlap_provenance.get("status"),
        required="PASS",
        source_commit=overlap_provenance.get("source_commit"),
        executable_sha256=overlap_provenance.get("executable_sha256"),
        static_sha256=overlap_provenance.get("static_sha256"),
        forcing_publication_sha256=overlap_provenance.get(
            "forcing_publication_sha256"
        ),
    )
    model_identities = {
        (
            report.get("provenance", {}).get("source_commit"),
            report.get("provenance", {}).get("executable_sha256"),
            report.get("provenance", {}).get("static_sha256"),
        )
        for report in (
            *(
                model
                for _segment, model, _solver, _compression, _retirement
                in segment_reports
            ),
            overlap_model,
        )
    }
    screen(
        "consistent_model_identity",
        len(model_identities) == 1
        and None not in next(iter(model_identities), (None,)),
        observed_identities=[
            {
                "source_commit": source_commit,
                "executable_sha256": executable_sha256,
                "static_sha256": static_sha256,
            }
            for source_commit, executable_sha256, static_sha256 in sorted(
                model_identities,
                key=lambda item: tuple("" if value is None else value for value in item),
            )
        ],
        required="one source commit, executable, and static-domain identity",
    )
    observed_source_commits = {
        identity[0] for identity in model_identities if identity[0] is not None
    }
    screen(
        "frozen_hicar_source_commit",
        observed_source_commits == {month.get("expected_hicar_commit")},
        observed=sorted(observed_source_commits),
        required=month.get("expected_hicar_commit"),
    )
    required_restart_water_fields = {
        "precipitation",
        "runoff_surface_cumulative",
        "runoff_subsurface_cumulative",
        "evaporation_net_cumulative",
    }
    trajectory_metrics = trajectory.get("metrics", {})
    screen(
        "cumulative_water_restart_continuity",
        required_restart_water_fields <= set(trajectory_metrics)
        and all(
            trajectory_metrics[name].get("outside_tolerance_count") == 0
            for name in required_restart_water_fields
            if name in trajectory_metrics
        ),
        required_fields=sorted(required_restart_water_fields),
        observed_fields=sorted(
            required_restart_water_fields & set(trajectory_metrics)
        ),
    )
    overlap_count = len(overlap_model.get("output", {}).get("times", []))
    screen(
        "restart_overlap_record_count",
        overlap_count == int(overlap["expected_output_records"]),
        observed=overlap_count,
        required=int(overlap["expected_output_records"]),
    )
    screen(
        "restart_overlap_forcing_retired",
        overlap_retirement.get("action") == "RETIRED"
        and overlap_retirement.get("execute") is True
        and overlap_retirement.get("forcing_publication_ready_withdrawn") is True
        and int(overlap_retirement.get("payload_count", -1))
        == int(overlap["forcing_record_count"]),
        action=overlap_retirement.get("action"),
        execute=overlap_retirement.get("execute"),
        forcing_publication_ready_withdrawn=overlap_retirement.get(
            "forcing_publication_ready_withdrawn"
        ),
        observed_payload_count=overlap_retirement.get("payload_count"),
        required_payload_count=int(overlap["forcing_record_count"]),
        payload_bytes=overlap_retirement.get("payload_bytes"),
    )
    overlap_target = Path(overlap_compression.get("target", ""))
    planned_overlap_target = Path(overlap["compressed_output_file"])
    screen(
        "restart_overlap_compressed_publication",
        overlap_target == planned_overlap_target
        and overlap_target.is_file()
        and overlap_target.stat().st_size > 0
        and Path(f"{overlap_target}.ready").is_file(),
        observed_target=str(overlap_target),
        planned_target=str(planned_overlap_target),
        target_bytes=overlap_compression.get("target_bytes"),
    )

    energy = (
        physical.get("classes", {})
        .get("active_soil_interior", {})
        .get("surface_energy_diagnostic", {})
        .get("mean_absolute_residual_w_m2")
    )
    energy_limit = float(
        criteria["maximum_interior_surface_energy_mean_absolute_residual_w_m2"]
    )
    screen(
        "interior_surface_energy_closure",
        energy is not None and float(energy) <= energy_limit,
        observed_w_m2=energy,
        maximum_w_m2=energy_limit,
    )
    water_contract = physical.get("water_budget_contract", {})
    screen(
        "production_water_budget_observables",
        water_contract.get("mode") == "production_cumulative"
        and water_contract.get("production_eligible") is True
        and water_contract.get("representativeness_limited") is False,
        observed_mode=water_contract.get("mode"),
        production_eligible=water_contract.get("production_eligible"),
        representativeness_limited=water_contract.get(
            "representativeness_limited"
        ),
        required="exact cumulative interval observables",
    )

    minimum_pairs = int(criteria["minimum_station_pairs_per_core_metric"])
    for source_name in ("hicar", "rea_l"):
        for metric in CORE_STATION_METRICS:
            count = station_metric(station, source_name, metric).get("count", 0)
            screen(
                f"station_pairs_{source_name}_{metric}",
                int(count) >= minimum_pairs,
                observed=int(count),
                minimum=minimum_pairs,
            )

    allowances = criteria["maximum_hicar_rmse_deterioration_relative_to_rea_l"]
    scalar_rules = (
        (
            "temperature_2m_height_adjusted_k",
            "temperature_2m_height_adjusted_k_additive",
        ),
        (
            "relative_humidity_2m_percent",
            "relative_humidity_2m_percent_additive",
        ),
        (
            "surface_pressure_height_adjusted_pa",
            "surface_pressure_height_adjusted_pa_additive",
        ),
    )
    for metric, allowance_name in scalar_rules:
        hicar_rmse = station_metric(station, "hicar", metric).get(
            "root_mean_squared_error"
        )
        rea_l_rmse = station_metric(station, "rea_l", metric).get(
            "root_mean_squared_error"
        )
        additive = float(allowances[allowance_name])
        maximum = None if rea_l_rmse is None else float(rea_l_rmse) + additive
        screen(
            f"hicar_degradation_{metric}",
            hicar_rmse is not None
            and maximum is not None
            and float(hicar_rmse) <= maximum,
            hicar_rmse=hicar_rmse,
            rea_l_rmse=rea_l_rmse,
            maximum_hicar_rmse=maximum,
            additive_allowance=additive,
        )

    hicar_vector = station_metric(station, "hicar", "wind_vector").get(
        "vector_root_mean_squared_error_m_s"
    )
    rea_l_vector = station_metric(station, "rea_l", "wind_vector").get(
        "vector_root_mean_squared_error_m_s"
    )
    vector_additive = float(allowances["wind_vector_m_s_additive"])
    vector_maximum = (
        None if rea_l_vector is None else float(rea_l_vector) + vector_additive
    )
    screen(
        "hicar_degradation_wind_vector",
        hicar_vector is not None
        and vector_maximum is not None
        and float(hicar_vector) <= vector_maximum,
        hicar_rmse_m_s=hicar_vector,
        rea_l_rmse_m_s=rea_l_vector,
        maximum_hicar_rmse_m_s=vector_maximum,
    )

    precipitation = "precipitation_interval_kg_m2"
    hicar_precipitation = station_metric(
        station, "hicar", precipitation
    ).get("root_mean_squared_error")
    rea_l_precipitation = station_metric(
        station, "rea_l", precipitation
    ).get("root_mean_squared_error")
    precipitation_maximum = (
        None
        if rea_l_precipitation is None
        else max(
            1.5 * float(rea_l_precipitation),
            float(rea_l_precipitation) + 1.0,
        )
    )
    screen(
        "hicar_degradation_precipitation",
        hicar_precipitation is not None
        and precipitation_maximum is not None
        and float(hicar_precipitation) <= precipitation_maximum,
        hicar_rmse_kg_m2=hicar_precipitation,
        rea_l_rmse_kg_m2=rea_l_precipitation,
        maximum_hicar_rmse_kg_m2=precipitation_maximum,
        rule=allowances["precipitation_interval_kg_m2_rule"],
    )

    matched_temperature_days = ogd.get("matched_temperature_days", [])
    tabsd_values = (
        [str(value) for value in matched_temperature_days]
        if isinstance(matched_temperature_days, list)
        else []
    )
    matched_daily_windows = ogd.get("matched_daily_windows", [])
    rhiresd_values = (
        [str(item.get("rhires_day")) for item in matched_daily_windows]
        if isinstance(matched_daily_windows, list)
        and all(isinstance(item, dict) for item in matched_daily_windows)
        else []
    )
    matched_radiation_times = ogd.get("matched_radiation_times", [])
    sis_values = (
        [normalized_time(value) for value in matched_radiation_times]
        if isinstance(matched_radiation_times, list)
        else []
    )
    tabsd_days = entry_count(matched_temperature_days)
    rhiresd_windows = entry_count(matched_daily_windows)
    sis_times = entry_count(matched_radiation_times)
    screen(
        "tabsd_complete_days",
        tabsd_days == int(criteria["expected_complete_tabsd_days"])
        and len(tabsd_values) == tabsd_days
        and len(set(tabsd_values)) == tabsd_days,
        observed=tabsd_days,
        unique=len(set(tabsd_values)),
        required=int(criteria["expected_complete_tabsd_days"]),
    )
    screen(
        "rhiresd_complete_windows",
        rhiresd_windows == int(criteria["expected_complete_rhiresd_windows"])
        and len(rhiresd_values) == rhiresd_windows
        and len(set(rhiresd_values)) == rhiresd_windows,
        observed=rhiresd_windows,
        unique=len(set(rhiresd_values)),
        required=int(criteria["expected_complete_rhiresd_windows"]),
    )
    screen(
        "sis_matched_time_axis",
        sis_times == int(criteria["expected_matched_sis_times"])
        and len(sis_values) == sis_times
        and len(set(sis_values)) == sis_times
        and set(sis_values) <= set(planned_times),
        observed=sis_times,
        unique=len(set(sis_values)),
        required=int(criteria["expected_matched_sis_times"]),
    )

    hicar_tabsd = ogd_rmse(ogd, "tabsd", "hicar")
    rea_l_tabsd = ogd_rmse(ogd, "tabsd", "rea_l")
    tabsd_maximum = (
        None
        if rea_l_tabsd is None
        else float(rea_l_tabsd)
        + float(allowances["tabsd_temperature_k_additive"])
    )
    screen(
        "hicar_degradation_tabsd",
        hicar_tabsd is not None
        and tabsd_maximum is not None
        and hicar_tabsd <= tabsd_maximum,
        hicar_rmse_k=hicar_tabsd,
        rea_l_rmse_k=rea_l_tabsd,
        maximum_hicar_rmse_k=tabsd_maximum,
    )

    hicar_rhiresd = ogd_rmse(ogd, "rhiresd", "hicar")
    rea_l_rhiresd = ogd_rmse(ogd, "rhiresd", "rea_l")
    rhiresd_maximum = (
        None
        if rea_l_rhiresd is None
        else max(1.5 * rea_l_rhiresd, rea_l_rhiresd + 1.0)
    )
    screen(
        "hicar_degradation_rhiresd",
        hicar_rhiresd is not None
        and rhiresd_maximum is not None
        and hicar_rhiresd <= rhiresd_maximum,
        hicar_rmse_kg_m2=hicar_rhiresd,
        rea_l_rmse_kg_m2=rea_l_rhiresd,
        maximum_hicar_rmse_kg_m2=rhiresd_maximum,
        rule=allowances["rhiresd_precipitation_rule"],
    )

    flags = drift.get("flags", [])
    flag_ids = {item.get("id") for item in flags}
    attribution_path = Path(validation_paths["drift_attribution"])
    attributions: dict[str, dict] = {}
    attribution_signed = False
    if flags and ready(attribution_path):
        attribution = load_json(attribution_path)
        attribution_signed = bool(
            attribution.get("status") == "PASS"
            and attribution.get("reviewer")
            and attribution.get("reviewed_at")
        )
        items = attribution.get("attributions", [])
        if all(isinstance(item, dict) and item.get("flag_id") for item in items):
            attributions = {item["flag_id"]: item for item in items}
    classification_ids = set(attributions)
    classifications_valid = (
        attribution_signed
        and classification_ids == flag_ids
        and all(
            item.get("classification") in ALLOWED_DRIFT_CLASSIFICATIONS
            and item.get("rationale")
            for item in attributions.values()
        )
    )
    if not flags:
        screen(
            "postspinup_drift_attribution",
            True,
            flag_count=0,
            observed="NO_DRIFT_FLAGS",
        )
    else:
        screen(
            "postspinup_drift_attribution",
            classifications_valid,
            flag_count=len(flags),
            attributed_flag_ids=sorted(classification_ids),
            required_flag_ids=sorted(flag_ids),
            signed=attribution_signed,
        )
    unexplained = sorted(
        flag_id
        for flag_id, item in attributions.items()
        if item.get("classification") == "unexplained"
    )
    screen(
        "no_unexplained_postspinup_drift",
        classifications_valid and not unexplained if flags else True,
        unexplained_flag_ids=unexplained,
        maximum=0,
    )

    archive_status = archive.get("status")
    archive_approval = archive.get("approval", {})
    required_approval_fields = (
        "destination",
        "owner",
        "quota_bytes",
        "measured_transfer_bytes_per_second",
        "restore_drill_report",
        "approved_by",
    )
    missing_archive_fields = [
        name for name in required_approval_fields if not archive_approval.get(name)
    ]
    restore = archive_approval.get("restore_drill_report")
    archive_approved = (
        archive_status == "APPROVED"
        and not missing_archive_fields
        and ready(Path(restore))
    )
    screen(
        "production_archive_contract",
        archive_approved,
        observed_status=archive_status,
        required_status="APPROVED",
        missing_approval_fields=missing_archive_fields,
        restore_drill_published=bool(restore and ready(Path(restore))),
    )

    quality_thresholds = quality.get("annual_acceptance_thresholds", {})
    quality_approval = quality_thresholds.get("approval", {})
    required_quality_fields = (
        "application",
        "metric_weights",
        "absolute_limits",
        "approved_by",
        "frozen_at",
    )
    missing_quality_fields = [
        name for name in required_quality_fields if not quality_approval.get(name)
    ]
    required_metric_families = set(
        quality_thresholds.get("required_metrics", {})
    )
    metric_weights = quality_approval.get("metric_weights")
    absolute_limits = quality_approval.get("absolute_limits")
    missing_weight_families = sorted(
        required_metric_families
        - (set(metric_weights) if isinstance(metric_weights, dict) else set())
    )
    missing_limit_families = sorted(
        required_metric_families
        - (set(absolute_limits) if isinstance(absolute_limits, dict) else set())
    )
    weights_valid = (
        isinstance(metric_weights, dict)
        and not missing_weight_families
        and all(
            isinstance(value, (int, float)) and float(value) >= 0.0
            for value in metric_weights.values()
        )
        and sum(float(value) for value in metric_weights.values()) > 0.0
    )
    limits_valid = (
        isinstance(absolute_limits, dict)
        and not missing_limit_families
        and all(
            isinstance(absolute_limits[family], dict)
            and bool(absolute_limits[family])
            for family in required_metric_families
        )
    )
    quality_approved = (
        quality_thresholds.get("status") == "APPROVED"
        and not missing_quality_fields
        and bool(required_metric_families)
        and weights_valid
        and limits_valid
    )
    screen(
        "application_quality_contract",
        quality_approved,
        observed_status=quality_thresholds.get("status"),
        required_status="APPROVED",
        missing_approval_fields=missing_quality_fields,
        required_metric_families=sorted(required_metric_families),
        missing_weight_families=missing_weight_families,
        missing_limit_families=missing_limit_families,
    )

    failed = [item["id"] for item in screens if not item["passed"]]
    archive_only = failed == ["production_archive_contract"]
    quality_only = failed == ["application_quality_contract"]
    unresolved_contracts = {
        "production_archive_contract",
        "application_quality_contract",
    }
    if not failed:
        decision = "GO_ANNUAL_CYCLE"
    elif unexplained:
        decision = "STOP_AND_REDESIGN"
    elif archive_only:
        decision = "HOLD_ARCHIVE_CONTRACT"
    elif quality_only:
        decision = "HOLD_APPLICATION_QUALITY_CONTRACT"
    elif set(failed) == unresolved_contracts:
        decision = "HOLD_QUALIFICATION_CONTRACTS"
    else:
        decision = "HOLD_AND_DIAGNOSE"

    compression_bytes = sum(
        int(report.get("target_bytes", 0))
        for name, report in reports.items()
        if name.endswith("_compression")
    )
    payload = {
        "schema_version": 1,
        "assessment_status": "COMPLETE",
        "decision": decision,
        "interpretation": criteria["decision"],
        "month_plan": month.get("_plan_path"),
        "scientific_plan": month["scientific_plan"],
        "screens": screens,
        "failed_screens": failed,
        "incomplete_reasons": [],
        "postspinup_drift": {
            "flag_count": len(flags),
            "unexplained_flag_ids": unexplained,
            "attribution_required": bool(flags),
        },
        "measured_publication": {
            "month_output_records": len(segment_times),
            "compressed_output_bytes_including_overlap": compression_bytes,
        },
        "archive_contract": str(archive_path.resolve()),
        "observational_validation_contract": str(quality_path.resolve()),
        "authorization": {
            "annual_cycle": decision == "GO_ANNUAL_CYCLE",
            "twenty_year_200m_production": False,
            "100m_scientific_production": False,
        },
    }
    return payload, True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month-plan", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    if not ready(args.month_plan):
        raise SystemExit(f"month plan is not published: {args.month_plan}")
    month = load_json(args.month_plan)
    if month.get("status") != "PLANNED":
        raise SystemExit("month plan is not PLANNED")
    scientific_path = Path(month["scientific_plan"])
    if not scientific_path.is_file():
        raise SystemExit(f"scientific plan is missing: {scientific_path}")
    scientific = load_json(scientific_path)
    month["_plan_path"] = str(args.month_plan.resolve())
    report = (
        args.report
        or Path(month["validation_reports"]["month_assessment"])
    ).resolve()

    payload, complete = assess(month, scientific)
    write_json_atomic(report, payload)
    if not complete:
        for reason in payload["incomplete_reasons"]:
            print(f"INCOMPLETE: {reason}")
        return 1
    Path(f"{report}.ready").touch()
    print(f"{payload['decision']}: month-to-annual assessment is complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
