#!/usr/bin/env python3
"""Apply the frozen annual-to-20-year HICAR qualification criteria."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile


SEASONS = ("DJF", "MAM", "JJA", "SON")
ALLOWED_DRIFT_CLASSIFICATIONS = {
    "forcing_consistent",
    "physically_explained_hicar_bias",
    "unexplained",
}
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA = re.compile(r"^[0-9a-f]{40}$")


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


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verified_artifact(entry: object) -> tuple[bool, dict]:
    if not isinstance(entry, dict):
        return False, {"reason": "entry is not an object"}
    path_value = entry.get("path")
    digest = entry.get("sha256")
    size = entry.get("size_bytes")
    if not isinstance(path_value, str) or not path_value:
        return False, {"reason": "path is missing"}
    path = Path(path_value)
    if (
        not isinstance(digest, str)
        or not HEX_64.fullmatch(digest)
        or not isinstance(size, int)
        or size <= 0
        or not ready(path)
    ):
        return False, {
            "path": str(path),
            "reason": "digest/size/publication metadata is invalid",
        }
    actual_size = path.stat().st_size
    actual_digest = sha256(path)
    return (
        actual_size == size and actual_digest == digest,
        {
            "path": str(path.resolve()),
            "declared_size_bytes": size,
            "actual_size_bytes": actual_size,
            "declared_sha256": digest,
            "actual_sha256": actual_digest,
        },
    )


def path_is_within(path_value: object, root_value: object) -> bool:
    if not isinstance(path_value, str) or not isinstance(root_value, str):
        return False
    try:
        Path(path_value).resolve().relative_to(Path(root_value).resolve())
    except ValueError:
        return False
    return True


def normalized_time(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=None).isoformat()


def normalized_date(value: str) -> str:
    return date.fromisoformat(value).isoformat()


def season_for(value: datetime | date | str) -> str:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    month = value.month
    if month in (12, 1, 2):
        return "DJF"
    if month in (3, 4, 5):
        return "MAM"
    if month in (6, 7, 8):
        return "JJA"
    return "SON"


def expected_times(annual: dict) -> list[str]:
    start = datetime.fromisoformat(annual["start"])
    end = datetime.fromisoformat(annual["end"])
    interval = timedelta(seconds=int(annual["output_interval_seconds"]))
    values = []
    valid = start
    while valid <= end:
        values.append(valid.isoformat())
        valid += interval
    return values


def station_rmse(
    report: dict,
    season: str,
    source: str,
    metric: str,
) -> float | None:
    item = (
        report.get("seasonal_metrics", {})
        .get(season, {})
        .get(source, {})
        .get("all_sites", {})
        .get(metric, {})
    )
    key = (
        "vector_root_mean_squared_error_m_s"
        if metric == "wind_vector"
        else "root_mean_squared_error"
    )
    value = item.get(key)
    return None if value is None else float(value)


def ogd_rmse(
    report: dict,
    season: str,
    product: str,
    source: str,
) -> float | None:
    value = (
        report.get("seasonal_metrics", {})
        .get(season, {})
        .get(product, {})
        .get(source, {})
        .get("interior_ge_10km", {})
        .get("root_mean_squared_error")
    )
    return None if value is None else float(value)


def quality_contract_valid(contract: dict) -> tuple[bool, dict]:
    thresholds = contract.get("annual_acceptance_thresholds", {})
    approval = thresholds.get("approval", {})
    required_fields = (
        "application",
        "metric_weights",
        "absolute_limits",
        "approved_by",
        "frozen_at",
    )
    missing_fields = [name for name in required_fields if not approval.get(name)]
    required_families = set(thresholds.get("required_metrics", {}))
    weights = approval.get("metric_weights")
    limits = approval.get("absolute_limits")
    missing_weights = sorted(
        required_families - (set(weights) if isinstance(weights, dict) else set())
    )
    missing_limits = sorted(
        required_families - (set(limits) if isinstance(limits, dict) else set())
    )
    weights_valid = (
        isinstance(weights, dict)
        and not missing_weights
        and all(
            isinstance(value, (int, float))
            and math.isfinite(float(value))
            and float(value) >= 0.0
            for value in weights.values()
        )
        and sum(float(value) for value in weights.values()) > 0.0
    )
    limits_valid = (
        isinstance(limits, dict)
        and not missing_limits
        and all(
            isinstance(limits[family], dict)
            and bool(limits[family])
            and all(
                isinstance(value, (int, float))
                and math.isfinite(float(value))
                and float(value) >= 0.0
                for value in limits[family].values()
            )
            for family in required_families
        )
    )
    valid = (
        thresholds.get("status") == "APPROVED"
        and bool(required_families)
        and not missing_fields
        and weights_valid
        and limits_valid
    )
    return valid, {
        "status": thresholds.get("status"),
        "missing_approval_fields": missing_fields,
        "missing_weight_families": missing_weights,
        "missing_limit_families": missing_limits,
    }


def assess(annual: dict, scientific: dict) -> tuple[dict, bool]:
    criteria = scientific["promotion_criteria"][
        "annual_cycle_to_20_year_campaign"
    ]
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

    month = publication("month_assessment", annual["month_assessment"])
    segment_times: list[str] = []
    segment_reports = []
    for segment in annual["segments"]:
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
        segment_reports.append((segment, model, solver, compression))
        if model is not None:
            segment_times.extend(
                normalized_time(value)
                for value in model.get("output", {}).get("times", [])
            )

    trajectory_reports = []
    for index, item in enumerate(annual["restart_trajectory_reports"], start=1):
        path = item["report"] if isinstance(item, dict) else item
        trajectory_reports.append(
            publication(f"restart_trajectory_{index:02d}", path)
        )
    initialization_reports = []
    for index, item in enumerate(
        annual["initialization_equivalence_reports"],
        start=1,
    ):
        path = item["report"] if isinstance(item, dict) else item
        initialization_reports.append(
            publication(f"initialization_equivalence_{index:02d}", path)
        )

    validation_paths = annual["validation_reports"]
    physical = publication("physical", validation_paths["physical"])
    source = publication("rea_l_source", validation_paths["rea_l_source"])
    station = publication("swissmetnet", validation_paths["swissmetnet"])
    ogd = publication("ogd_grid", validation_paths["ogd_grid"])
    drift = publication("drift_screen", validation_paths["drift_screen"])
    attribution = publication(
        "drift_attribution",
        validation_paths["drift_attribution"],
    )
    application = publication(
        "application_quality",
        validation_paths["application_quality"],
    )
    recovery = publication(
        "failure_recovery",
        validation_paths["failure_recovery"],
    )
    archive_transfer = publication(
        "archive_transfer_restore",
        validation_paths["archive_transfer_restore"],
    )
    release = publication(
        "production_release",
        validation_paths["production_release"],
    )

    archive_path = Path(annual["archive_contract"])
    quality_path = Path(annual["observational_validation_contract"])
    archive = load_json(archive_path) if archive_path.is_file() else None
    quality = load_json(quality_path) if quality_path.is_file() else None
    if archive is None:
        incomplete.append(f"archive contract is missing: {archive_path}")
    if quality is None:
        incomplete.append(f"quality contract is missing: {quality_path}")

    if incomplete:
        return {
            "schema_version": 1,
            "assessment_status": "INCOMPLETE",
            "decision": "INCOMPLETE",
            "annual_plan": annual.get("_plan_path"),
            "screens": screens,
            "failed_screens": [],
            "incomplete_reasons": incomplete,
            "authorization": {
                "twenty_year_200m_production": False,
                "100m_scientific_production": False,
            },
        }, False

    assert month is not None
    assert physical is not None
    assert source is not None
    assert station is not None
    assert ogd is not None
    assert drift is not None
    assert attribution is not None
    assert application is not None
    assert recovery is not None
    assert archive_transfer is not None
    assert release is not None
    assert archive is not None
    assert quality is not None

    screen(
        "month_authorization",
        month.get("decision") == "GO_ANNUAL_CYCLE"
        and month.get("authorization", {}).get("annual_cycle") is True,
        observed_decision=month.get("decision"),
    )

    expected = expected_times(annual)
    screen(
        "exact_annual_output_coverage",
        segment_times == expected
        and len(segment_times)
        == int(criteria["expected_unique_output_records"]),
        observed_records=len(segment_times),
        expected_records=int(criteria["expected_unique_output_records"]),
    )
    for segment, model, solver, compression in segment_reports:
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
        screen(
            f"segment_{sequence:02d}_reports",
            model.get("status") == "PASS"
            and solver.get("status") == "PASS"
            and compression.get("status") == "PASS",
            model_status=model.get("status"),
            solver_status=solver.get("status"),
            compression_status=compression.get("status"),
        )
        target = Path(compression.get("target", ""))
        planned = Path(segment["compressed_output_file"])
        screen(
            f"segment_{sequence:02d}_compressed_publication",
            target == planned
            and target.is_file()
            and target.stat().st_size > 0
            and Path(f"{target}.ready").is_file(),
            observed_target=str(target),
            planned_target=str(planned),
        )

    model_identities = {
        (
            model.get("provenance", {}).get("source_commit"),
            model.get("provenance", {}).get("executable_sha256"),
            model.get("provenance", {}).get("static_sha256"),
        )
        for _segment, model, _solver, _compression in segment_reports
    }
    consistent_model_identity = (
        len(model_identities) == 1
        and None not in next(iter(model_identities), (None,))
    )
    screen(
        "consistent_model_identity",
        consistent_model_identity,
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
        observed_source_commits == {annual.get("expected_hicar_commit")},
        observed=sorted(observed_source_commits),
        required=annual.get("expected_hicar_commit"),
    )

    required_trajectories = int(criteria["required_restart_equivalence_boundaries"])
    screen(
        "seasonal_restart_equivalence",
        len(trajectory_reports) >= required_trajectories
        and all(item.get("status") == "PASS" for item in trajectory_reports),
        observed_reports=len(trajectory_reports),
        required_reports=required_trajectories,
        statuses=[item.get("status") for item in trajectory_reports],
    )

    minimum_overlaps = int(criteria["minimum_independent_initialization_overlaps"])
    minimum_retained = int(
        criteria["minimum_retained_days_per_initialization_overlap"]
    )
    required_overlap_seasons = set(
        criteria["required_independent_initialization_seasons"]
    )
    observed_overlap_seasons = {
        item.get("season")
        for item in initialization_reports
        if item.get("status") == "PASS"
        and item.get("trajectory_equivalence_after_spinup") == "PASS"
        and int(item.get("retained_days", 0)) >= minimum_retained
    }
    screen(
        "independent_initialization_equivalence",
        len(initialization_reports) >= minimum_overlaps
        and required_overlap_seasons <= observed_overlap_seasons,
        observed_reports=len(initialization_reports),
        required_reports=minimum_overlaps,
        qualified_seasons=sorted(
            season for season in observed_overlap_seasons if season
        ),
        required_seasons=sorted(required_overlap_seasons),
    )

    required_status_reports = {
        "physical": physical,
        "rea_l_source": source,
        "swissmetnet": station,
        "ogd_grid": ogd,
        "drift_screen": drift,
    }
    screen(
        "annual_scientific_report_status",
        all(item.get("status") == "PASS" for item in required_status_reports.values()),
        statuses={
            name: item.get("status")
            for name, item in required_status_reports.items()
        },
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
        "annual_surface_energy_closure",
        energy is not None and float(energy) <= energy_limit,
        observed=energy,
        maximum=energy_limit,
    )

    expected_datetimes = [
        datetime.fromisoformat(value) for value in expected
    ]
    expected_station_by_season = Counter(
        season_for(value) for value in expected_datetimes
    )
    expected_days = []
    day = datetime.fromisoformat(annual["start"]).date()
    end_day = datetime.fromisoformat(annual["end"]).date()
    while day < end_day:
        expected_days.append(day)
        day += timedelta(days=1)
    expected_tabsd_by_season = Counter(season_for(value) for value in expected_days)
    expected_rhires_by_season = Counter(
        season_for(value) for value in expected_days[:-1]
    )
    expected_sis_by_season = Counter(
        season_for(value) for value in expected_datetimes[1:-1]
    )
    expected_station_axis = set(expected)
    expected_tabsd_axis = {value.isoformat() for value in expected_days}
    expected_rhires_axis = {
        value.isoformat() for value in expected_days[:-1]
    }
    expected_sis_axis = set(expected[1:-1])
    observed_station_times = [
        normalized_time(value)
        for value in station.get("matched_model_times", [])
    ]
    observed_tabsd_days = [
        normalized_date(value)
        for value in ogd.get("matched_temperature_days", [])
    ]
    observed_rhires_days = [
        normalized_date(item["rhires_day"])
        for item in ogd.get("matched_daily_windows", [])
    ]
    observed_sis_times = [
        normalized_time(value)
        for value in ogd.get("matched_radiation_times", [])
    ]
    observed_axes = {
        "station": observed_station_times,
        "tabsd": observed_tabsd_days,
        "rhiresd": observed_rhires_days,
        "sis": observed_sis_times,
    }
    expected_axes = {
        "station": expected_station_axis,
        "tabsd": expected_tabsd_axis,
        "rhiresd": expected_rhires_axis,
        "sis": expected_sis_axis,
    }
    axis_checks = {
        name: {
            "observed_count": len(values),
            "unique_count": len(set(values)),
            "expected_axis_count": len(expected_axes[name]),
            "duplicates": len(values) - len(set(values)),
            "outside_expected_axis": sorted(
                set(values) - expected_axes[name]
            ),
            "passed": len(values) == len(set(values))
            and set(values) <= expected_axes[name],
        }
        for name, values in observed_axes.items()
    }
    observed_station_by_season = Counter(
        season_for(value) for value in observed_station_times
    )
    observed_tabsd_by_season = Counter(
        season_for(value) for value in observed_tabsd_days
    )
    observed_rhires_by_season = Counter(
        season_for(value) for value in observed_rhires_days
    )
    observed_sis_by_season = Counter(
        season_for(value) for value in observed_sis_times
    )
    completeness_limit = float(
        criteria["minimum_seasonal_data_completeness_fraction"]
    )
    completeness = {}
    for season in SEASONS:
        completeness[season] = {
            "station": observed_station_by_season[season]
            / expected_station_by_season[season],
            "tabsd": observed_tabsd_by_season[season]
            / expected_tabsd_by_season[season],
            "rhiresd": observed_rhires_by_season[season]
            / expected_rhires_by_season[season],
            "sis": observed_sis_by_season[season]
            / expected_sis_by_season[season],
        }
    observed_totals = {
        "station_model_times": len(station.get("matched_model_times", [])),
        "tabsd_days": len(ogd.get("matched_temperature_days", [])),
        "rhiresd_windows": len(ogd.get("matched_daily_windows", [])),
        "sis_times": len(ogd.get("matched_radiation_times", [])),
    }
    expected_totals = {
        "station_model_times": int(criteria["expected_station_model_times"]),
        "tabsd_days": int(criteria["expected_complete_tabsd_days"]),
        "rhiresd_windows": int(criteria["expected_complete_rhiresd_windows"]),
        "sis_times": int(criteria["expected_matched_sis_times"]),
    }
    expected_axis_totals = {
        "station_model_times": len(expected_station_axis),
        "tabsd_days": len(expected_tabsd_axis),
        "rhiresd_windows": len(expected_rhires_axis),
        "sis_times": len(expected_sis_axis),
    }
    screen(
        "seasonal_observation_completeness",
        expected_axis_totals == expected_totals
        and all(item["passed"] for item in axis_checks.values())
        and all(
            value >= completeness_limit
            for season in completeness.values()
            for value in season.values()
        ),
        axes=axis_checks,
        fractions=completeness,
        minimum=completeness_limit,
        observed_totals=observed_totals,
        expected_totals=expected_totals,
        expected_axis_totals=expected_axis_totals,
    )

    margins = criteria["maximum_hicar_rmse_deterioration_relative_to_rea_l"]
    relative_checks = []
    for season in SEASONS:
        station_contracts = (
            (
                "temperature",
                "temperature_2m_height_adjusted_k",
                "temperature_2m_height_adjusted_k_additive",
            ),
            (
                "humidity",
                "relative_humidity_2m_percent",
                "relative_humidity_2m_percent_additive",
            ),
            (
                "pressure",
                "surface_pressure_height_adjusted_pa",
                "surface_pressure_height_adjusted_pa_additive",
            ),
            ("wind", "wind_vector", "wind_vector_m_s_additive"),
        )
        for label, metric, margin_key in station_contracts:
            hicar = station_rmse(station, season, "hicar", metric)
            rea_l = station_rmse(station, season, "rea_l", metric)
            limit = None if rea_l is None else rea_l + float(margins[margin_key])
            relative_checks.append(
                {
                    "season": season,
                    "metric": label,
                    "hicar_rmse": hicar,
                    "rea_l_rmse": rea_l,
                    "maximum_hicar_rmse": limit,
                    "passed": hicar is not None
                    and limit is not None
                    and hicar <= limit,
                }
            )
        hicar_precip = station_rmse(
            station,
            season,
            "hicar",
            "precipitation_interval_kg_m2",
        )
        rea_l_precip = station_rmse(
            station,
            season,
            "rea_l",
            "precipitation_interval_kg_m2",
        )
        precip_limit = (
            None
            if rea_l_precip is None
            else max(1.25 * rea_l_precip, rea_l_precip + 0.5)
        )
        relative_checks.append(
            {
                "season": season,
                "metric": "station_precipitation",
                "hicar_rmse": hicar_precip,
                "rea_l_rmse": rea_l_precip,
                "maximum_hicar_rmse": precip_limit,
                "passed": hicar_precip is not None
                and precip_limit is not None
                and hicar_precip <= precip_limit,
            }
        )
        for product, key, factor, additive in (
            ("tabsd", "tabsd_temperature_k_additive", 1.0, 0.5),
            ("rhiresd", "rhiresd_precipitation_rule", 1.25, 0.5),
        ):
            hicar = ogd_rmse(ogd, season, product, "hicar")
            rea_l = ogd_rmse(ogd, season, product, "rea_l")
            limit = (
                None
                if rea_l is None
                else (
                    rea_l + float(margins[key])
                    if factor == 1.0
                    else max(factor * rea_l, rea_l + additive)
                )
            )
            relative_checks.append(
                {
                    "season": season,
                    "metric": product,
                    "hicar_rmse": hicar,
                    "rea_l_rmse": rea_l,
                    "maximum_hicar_rmse": limit,
                    "passed": hicar is not None
                    and limit is not None
                    and hicar <= limit,
                }
            )
    screen(
        "seasonal_non_degradation_against_rea_l",
        all(item["passed"] for item in relative_checks),
        checks=relative_checks,
    )

    flags = drift.get("flags", [])
    attribution_items = attribution.get("attributions", [])
    attributions = {
        item.get("flag_id"): item
        for item in attribution_items
        if item.get("flag_id")
    }
    flag_ids = {item.get("flag_id") for item in flags if item.get("flag_id")}
    classifications_valid = (
        attribution.get("signed") is True
        and set(attributions) == flag_ids
        and all(
            item.get("classification") in ALLOWED_DRIFT_CLASSIFICATIONS
            and item.get("rationale")
            and item.get("reviewer")
            for item in attributions.values()
        )
    )
    unexplained = sorted(
        flag_id
        for flag_id, item in attributions.items()
        if item.get("classification") == "unexplained"
    )
    screen(
        "annual_drift_attribution",
        classifications_valid and len(unexplained)
        <= int(criteria["maximum_unexplained_drift_flags"]),
        flag_count=len(flags),
        unexplained_flag_ids=unexplained,
    )

    archive_approval = archive.get("approval", {})
    restore_path = archive_approval.get("restore_drill_report")
    archive_manifest_valid, archive_manifest_evidence = verified_artifact(
        archive_transfer.get("manifest")
    )
    archive_approved = (
        archive.get("status") == criteria["required_archive_contract_status"]
        and all(
            archive_approval.get(name)
            for name in (
                "destination",
                "owner",
                "quota_bytes",
                "measured_transfer_bytes_per_second",
                "restore_drill_report",
                "approved_by",
            )
        )
        and bool(restore_path)
        and ready(Path(restore_path))
        and archive_transfer.get("status") == "PASS"
        and archive_transfer.get("restore_verified") is True
        and archive_transfer.get("sha256_match") is True
        and int(archive_transfer.get("bytes_transferred", 0)) > 0
        and archive_transfer.get("destination")
        == archive_approval.get("destination")
        and archive_manifest_valid
        and path_is_within(
            archive_transfer.get("manifest", {}).get("path"),
            archive_approval.get("destination"),
        )
    )
    screen(
        "production_archive_and_restore",
        archive_approved,
        contract_status=archive.get("status"),
        transfer_status=archive_transfer.get("status"),
        manifest=archive_manifest_evidence,
    )

    quality_valid, quality_evidence = quality_contract_valid(quality)
    required_families = set(
        quality["annual_acceptance_thresholds"]["required_metrics"]
    )
    required_strata = set(
        quality["annual_acceptance_thresholds"]["required_strata"]
    )
    application_valid = (
        quality_valid
        and application.get("status") == "PASS"
        and application.get("contract_sha256") == sha256(quality_path)
        and required_families
        <= set(application.get("evaluated_metric_families", []))
        and required_strata <= set(application.get("evaluated_strata", []))
        and not application.get("failed_metrics")
    )
    screen(
        "absolute_application_quality",
        application_valid,
        contract=quality_evidence,
        application_status=application.get("status"),
        failed_metrics=application.get("failed_metrics", []),
    )

    screen(
        "failure_recovery_drill",
        recovery.get("status") == "PASS"
        and int(recovery.get("drills_completed", 0))
        >= int(criteria["required_failure_recovery_drills"])
        and recovery.get("restart_hash_match") is True
        and recovery.get("output_hash_match") is True,
        status=recovery.get("status"),
        drills_completed=recovery.get("drills_completed"),
    )

    release_required = {
        "source_commit": GIT_SHA,
        "executable_sha256": HEX_64,
        "static_sha256": HEX_64,
        "configuration_sha256": HEX_64,
        "annual_plan_sha256": HEX_64,
    }
    release_fields_valid = all(
        isinstance(release.get(name), str)
        and pattern.fullmatch(release[name])
        for name, pattern in release_required.items()
    )
    release_artifact_results = {
        name: verified_artifact(release.get("artifacts", {}).get(name))
        for name in (
            "source_archive",
            "executable",
            "static_domain",
            "configuration_archive",
            "annual_plan",
        )
    }
    release_artifacts_valid = all(
        valid for valid, _evidence in release_artifact_results.values()
    )
    release_artifacts = release.get("artifacts", {})
    current_annual_plan_sha256 = sha256(Path(annual["_plan_path"]))
    release_artifacts_consistent = (
        release_artifacts_valid
        and all(
            path_is_within(
                entry["path"],
                archive_approval.get("destination"),
            )
            for entry in release_artifacts.values()
        )
        and release_artifacts["executable"]["sha256"]
        == release.get("executable_sha256")
        and release_artifacts["static_domain"]["sha256"]
        == release.get("static_sha256")
        and release_artifacts["configuration_archive"]["sha256"]
        == release.get("configuration_sha256")
        and release_artifacts["annual_plan"]["sha256"]
        == release.get("annual_plan_sha256")
        == current_annual_plan_sha256
        and consistent_model_identity
        and release.get("source_commit")
        == next(iter(model_identities))[0]
        and release.get("executable_sha256")
        == next(iter(model_identities))[1]
        and release.get("static_sha256")
        == next(iter(model_identities))[2]
    )
    screen(
        "immutable_production_release",
        release.get("status") == "PASS"
        and release.get("immutable") is True
        and release_fields_valid
        and release_artifacts_consistent
        and release.get("compute_allocation")
        and release.get("archive_destination")
        == archive_approval.get("destination"),
        status=release.get("status"),
        immutable=release.get("immutable"),
        required_hash_fields_valid=release_fields_valid,
        artifacts_valid=release_artifacts_valid,
        artifacts={
            name: evidence
            for name, (_valid, evidence) in release_artifact_results.items()
        },
    )

    failed = [item["id"] for item in screens if not item["passed"]]
    if not failed:
        decision = "GO_20_YEAR_200M_PRODUCTION"
    elif unexplained:
        decision = "STOP_AND_REDESIGN"
    elif set(failed) <= {
        "production_archive_and_restore",
        "absolute_application_quality",
        "immutable_production_release",
    }:
        decision = "HOLD_PRODUCTION_CONTRACTS"
    else:
        decision = "HOLD_AND_DIAGNOSE"

    payload = {
        "schema_version": 1,
        "assessment_status": "COMPLETE",
        "decision": decision,
        "interpretation": criteria["decision"],
        "annual_plan": annual.get("_plan_path"),
        "scientific_plan": annual["scientific_plan"],
        "screens": screens,
        "failed_screens": failed,
        "incomplete_reasons": [],
        "authorization": {
            "twenty_year_200m_production": (
                decision == "GO_20_YEAR_200M_PRODUCTION"
            ),
            "100m_scientific_production": False,
        },
    }
    return payload, True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annual-plan", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    if not ready(args.annual_plan):
        raise SystemExit(f"annual plan is not published: {args.annual_plan}")
    annual = load_json(args.annual_plan)
    if annual.get("status") != "PLANNED":
        raise SystemExit("annual plan is not PLANNED")
    scientific_path = Path(annual["scientific_plan"])
    if not scientific_path.is_file():
        raise SystemExit(f"scientific plan is missing: {scientific_path}")
    scientific = load_json(scientific_path)
    annual["_plan_path"] = str(args.annual_plan.resolve())
    report = (
        args.report
        or Path(annual["validation_reports"]["annual_assessment"])
    ).resolve()

    payload, complete = assess(annual, scientific)
    write_json_atomic(report, payload)
    if not complete:
        for reason in payload["incomplete_reasons"]:
            print(f"INCOMPLETE: {reason}")
        return 1
    Path(f"{report}.ready").touch()
    print(f"{payload['decision']}: annual-to-production assessment is complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
