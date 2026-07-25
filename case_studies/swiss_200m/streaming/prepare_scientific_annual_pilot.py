#!/usr/bin/env python3
"""Publish a gate-authorized 200 m annual scientific-pilot plan."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path

from create_chunk_plan import publish
from prepare_scientific_month_pilot import (
    TIME_FORMAT,
    add_output_contract,
    load_json,
    publish_chunk,
    require_publication,
    segment_periods,
    sha256,
)


AUTHORIZED_DECISION = "GO_ANNUAL_CYCLE"
OVERLAP_TARGETS = {
    "SON": "2019-11-15T00:00:00",
    "DJF": "2020-01-15T00:00:00",
    "MAM": "2020-04-15T00:00:00",
    "JJA": "2020-07-15T00:00:00",
}


def require_static_initialization(
    static_file: Path,
    manifest_path: Path,
    label: str,
) -> dict:
    manifest = require_publication(manifest_path, f"{label} initialization")
    if manifest.get("status") != "PASS":
        raise SystemExit(f"{label} initialization is not PASS")
    if not static_file.is_file() or not Path(f"{static_file}.ready").is_file():
        raise SystemExit(f"{label} static file is not published: {static_file}")
    return manifest


def approved_archive(contract: dict) -> bool:
    approval = contract.get("approval", {})
    return contract.get("status") == "APPROVED" and all(
        approval.get(name)
        for name in (
            "destination",
            "owner",
            "quota_bytes",
            "measured_transfer_bytes_per_second",
            "restore_drill_report",
            "approved_by",
        )
    )


def approved_quality(contract: dict) -> bool:
    thresholds = contract.get("annual_acceptance_thresholds", {})
    approval = thresholds.get("approval", {})
    required_families = set(thresholds.get("required_metrics", {}))
    weights = approval.get("metric_weights")
    limits = approval.get("absolute_limits")
    return (
        thresholds.get("status") == "APPROVED"
        and all(
            approval.get(name)
            for name in (
                "application",
                "metric_weights",
                "absolute_limits",
                "approved_by",
                "frozen_at",
            )
        )
        and bool(required_families)
        and isinstance(weights, dict)
        and required_families <= set(weights)
        and isinstance(limits, dict)
        and required_families <= set(limits)
    )


def closest_restart_boundary(
    segments: list[dict],
    target: datetime,
) -> tuple[dict, dict]:
    candidates = list(zip(segments[:-1], segments[1:]))
    if not candidates:
        raise ValueError("annual plan needs at least two segments")
    return min(
        candidates,
        key=lambda pair: abs(
            (
                datetime.fromisoformat(pair[1]["start"]) - target
            ).total_seconds()
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scientific-plan", type=Path, required=True)
    parser.add_argument("--month-assessment", type=Path, required=True)
    parser.add_argument("--annual-root", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--static-file", type=Path, required=True)
    parser.add_argument(
        "--land-initialization-manifest",
        type=Path,
        required=True,
    )
    parser.add_argument("--winter-static-file", type=Path, required=True)
    parser.add_argument(
        "--winter-initialization-manifest",
        type=Path,
        required=True,
    )
    parser.add_argument("--summer-static-file", type=Path, required=True)
    parser.add_argument(
        "--summer-initialization-manifest",
        type=Path,
        required=True,
    )
    parser.add_argument("--report", type=Path)
    parser.add_argument("--producer-concurrency", type=int, default=4)
    args = parser.parse_args()

    if args.producer_concurrency < 1:
        raise SystemExit("--producer-concurrency must be positive")
    scientific = load_json(args.scientific_plan)
    month = require_publication(args.month_assessment, "month assessment")
    if month.get("assessment_status") != "COMPLETE":
        raise SystemExit("month assessment is not COMPLETE")
    if month.get("decision") != AUTHORIZED_DECISION:
        raise SystemExit(
            f"annual pilot is not authorized: decision={month.get('decision')}"
        )
    if month.get("authorization", {}).get("annual_cycle") is not True:
        raise SystemExit("month assessment does not authorize the annual cycle")

    main_initialization = require_static_initialization(
        args.static_file,
        args.land_initialization_manifest,
        "annual-start",
    )
    winter_initialization = require_static_initialization(
        args.winter_static_file,
        args.winter_initialization_manifest,
        "winter-overlap",
    )
    summer_initialization = require_static_initialization(
        args.summer_static_file,
        args.summer_initialization_manifest,
        "summer-overlap",
    )

    config_dir = args.scientific_plan.resolve().parent
    archive_path = config_dir / "production_archive_contract.json"
    quality_path = config_dir / "observational_validation_contract.json"
    archive = load_json(archive_path)
    quality = load_json(quality_path)
    if not approved_archive(archive):
        raise SystemExit("production archive contract is not APPROVED")
    restore = Path(archive["approval"]["restore_drill_report"])
    if not restore.is_file() or not Path(f"{restore}.ready").is_file():
        raise SystemExit("archive restore drill is not published")
    if not approved_quality(quality):
        raise SystemExit("application-quality contract is not APPROVED")

    configuration = scientific["configuration"]
    if not configuration.get("month_expected_hicar_commit"):
        raise SystemExit(
            "annual planning requires the qualified month HICAR child commit"
        )
    period = scientific["reference_periods"]["seasonal_cycle"]
    criteria = scientific["promotion_criteria"][
        "annual_cycle_to_20_year_campaign"
    ]
    start = datetime.fromisoformat(period["start"])
    end = start + timedelta(days=int(period["duration_days"]))
    segment_days = int(configuration["segment_days"])
    output_interval = int(configuration["output_interval_seconds"])
    restart_overlap_days = int(configuration["annual_restart_overlap_days"])
    post_restart_hours = int(
        configuration["annual_post_restart_comparison_hours"]
    )
    initialization_overlap_days = int(
        configuration["annual_initialization_overlap_days"]
    )
    initialization_spinup_days = int(
        configuration["annual_initialization_overlap_spinup_days"]
    )
    retained_overlap_days = (
        initialization_overlap_days - initialization_spinup_days
    )
    if retained_overlap_days < int(
        criteria["minimum_retained_days_per_initialization_overlap"]
    ):
        raise SystemExit("annual initialization overlap retains too few days")

    annual_root = args.annual_root.resolve()
    run_root = args.run_root.resolve()
    shared_restart_dir = run_root / "restart"
    segments = []
    unique_output_records = 0
    for sequence, (segment_start, segment_end) in enumerate(
        segment_periods(start, end, segment_days),
        start=1,
    ):
        chunk_id = (
            f"annual_{sequence:02d}_"
            f"{segment_start:%Y%m%d%H}_{segment_end:%Y%m%d%H}"
        )
        chunk_root = annual_root / "segments" / chunk_id
        segment = publish_chunk(
            chunk_id,
            segment_start,
            segment_end,
            chunk_root,
            args.producer_concurrency,
        )
        segment.update(
            {
                "sequence": sequence,
                "run_dir": str(run_root / "segments" / chunk_id),
                "shared_restart_dir": str(shared_restart_dir),
                "restart_from": (
                    None
                    if sequence == 1
                    else segment_start.strftime(TIME_FORMAT)
                ),
                "rea_l_land_initialization": sequence == 1,
                "output_profile": configuration["output_profile"],
                "output_interval_seconds": output_interval,
            }
        )
        expected = segment["hours"] * 3600 // output_interval
        segment["expected_output_records"] = expected + (
            1 if sequence == 1 else 0
        )
        add_output_contract(
            segment,
            chunk_root,
            Path(segment["run_dir"]),
            args.static_file.resolve(),
        )
        unique_output_records += segment["expected_output_records"]
        segments.append(segment)

    if unique_output_records != int(criteria["expected_unique_output_records"]):
        raise SystemExit(
            "annual segment output count does not match the frozen criterion"
        )
    combined_output_list = annual_root / "annual_output_file_list.txt"
    publish(
        combined_output_list,
        "".join(f"{item['expected_output_file']}\n" for item in segments),
    )

    trajectory_entries = []
    for season, target_value in OVERLAP_TARGETS.items():
        previous, following = closest_restart_boundary(
            segments,
            datetime.fromisoformat(target_value),
        )
        overlap_start = datetime.fromisoformat(previous["start"])
        boundary = datetime.fromisoformat(following["start"])
        overlap_end = overlap_start + timedelta(days=restart_overlap_days)
        if not overlap_start < boundary < overlap_end:
            raise SystemExit(f"{season} overlap does not cross its boundary")
        if int((overlap_end - boundary).total_seconds() // 3600) < (
            post_restart_hours
        ):
            raise SystemExit(f"{season} overlap has too little post-boundary time")
        overlap_id = (
            f"annual_restart_overlap_{season.lower()}_"
            f"{overlap_start:%Y%m%d%H}_{overlap_end:%Y%m%d%H}"
        )
        chunk_root = annual_root / "restart_overlaps" / overlap_id
        overlap = publish_chunk(
            overlap_id,
            overlap_start,
            overlap_end,
            chunk_root,
            args.producer_concurrency,
        )
        overlap.update(
            {
                "season": season,
                "run_dir": str(run_root / "restart_overlaps" / overlap_id),
                "shared_restart_dir": str(shared_restart_dir),
                "restart_from": overlap_start.strftime(TIME_FORMAT),
                "common_restart_with_segment": previous["chunk_id"],
                "crossed_segment_boundary": boundary.strftime(TIME_FORMAT),
                "comparison_start": boundary.strftime(TIME_FORMAT),
                "comparison_end": (
                    boundary + timedelta(hours=post_restart_hours)
                ).strftime(TIME_FORMAT),
                "checkpoint_to_preserve": str(
                    shared_restart_dir
                    / (
                        f"{args.static_file.stem}_"
                        f"{overlap_start:%Y-%m-%d_%H-%M-%S}.nc"
                    )
                ),
                "output_profile": configuration["output_profile"],
                "output_interval_seconds": output_interval,
                "expected_output_records": (
                    int((overlap_end - overlap_start).total_seconds())
                    // output_interval
                ),
            }
        )
        add_output_contract(
            overlap,
            chunk_root,
            Path(overlap["run_dir"]),
            args.static_file.resolve(),
        )
        trajectory_entries.append(
            {
                "season": season,
                "overlap": overlap,
                "report": str(
                    annual_root
                    / "validation"
                    / f"restart_trajectory_{season.lower()}.json"
                ),
            }
        )

    initialization_entries = []
    for season, overlap_start, static_file, manifest, initialization in (
        (
            "DJF",
            datetime.fromisoformat(
                scientific["reference_periods"]["winter_event"]["start"]
            ),
            args.winter_static_file.resolve(),
            args.winter_initialization_manifest.resolve(),
            winter_initialization,
        ),
        (
            "JJA",
            datetime.fromisoformat(
                scientific["reference_periods"]["summer_event"]["start"]
            ),
            args.summer_static_file.resolve(),
            args.summer_initialization_manifest.resolve(),
            summer_initialization,
        ),
    ):
        overlap_end = overlap_start + timedelta(days=initialization_overlap_days)
        overlap_id = (
            f"annual_initialization_overlap_{season.lower()}_"
            f"{overlap_start:%Y%m%d%H}_{overlap_end:%Y%m%d%H}"
        )
        chunk_root = annual_root / "initialization_overlaps" / overlap_id
        overlap = publish_chunk(
            overlap_id,
            overlap_start,
            overlap_end,
            chunk_root,
            args.producer_concurrency,
        )
        overlap.update(
            {
                "season": season,
                "run_dir": str(
                    run_root / "initialization_overlaps" / overlap_id
                ),
                "shared_restart_dir": str(
                    run_root / "initialization_overlaps" / overlap_id / "restart"
                ),
                "restart_from": None,
                "rea_l_land_initialization": True,
                "static_file": str(static_file),
                "land_initialization_manifest": str(manifest),
                "land_initialization_sha256": sha256(manifest),
                "initialization_status": initialization["status"],
                "output_profile": configuration["output_profile"],
                "output_interval_seconds": output_interval,
                "declared_spinup_days": initialization_spinup_days,
                "retained_days": retained_overlap_days,
                "comparison_start": (
                    overlap_start + timedelta(days=initialization_spinup_days)
                ).strftime(TIME_FORMAT),
                "comparison_end": overlap_end.strftime(TIME_FORMAT),
                "continuous_reference_output_list": str(combined_output_list),
                "expected_output_records": (
                    int((overlap_end - overlap_start).total_seconds())
                    // output_interval
                    + 1
                ),
            }
        )
        add_output_contract(
            overlap,
            chunk_root,
            Path(overlap["run_dir"]),
            static_file,
        )
        initialization_entries.append(
            {
                "season": season,
                "overlap": overlap,
                "report": str(
                    annual_root
                    / "validation"
                    / f"initialization_equivalence_{season.lower()}.json"
                ),
            }
        )

    validation_root = annual_root / "validation_sources"
    validation_sources = publish_chunk(
        f"annual_validation_sources_{start:%Y%m%d%H}_{end:%Y%m%d%H}",
        start,
        end,
        validation_root,
        args.producer_concurrency,
    )
    validation_sources.update(
        {
            "expected_reference_record_count": (
                (validation_sources["forcing_record_count"] - 1) // 3 + 1
            ),
            "reference_list": str(
                validation_root / "reference" / "reference_list.txt"
            ),
            "reference_publication": str(
                validation_root / "reference" / "reference_publication.json"
            ),
            "swissmetnet_observations": str(
                validation_root / "observations" / "swissmetnet_hourly.csv"
            ),
            "swissmetnet_manifest": str(
                validation_root
                / "observations"
                / "swissmetnet_hourly.manifest.json"
            ),
            "ogd_period_start": start.date().isoformat(),
            "ogd_period_end_exclusive": end.date().isoformat(),
            "ogd_manifest": str(
                validation_root
                / "observations"
                / "ogd_hydrological_year.json"
            ),
        }
    )

    report = (
        args.report or annual_root / "annual_pilot_plan.json"
    ).resolve()
    validation_dir = annual_root / "validation"
    payload = {
        "schema_version": 1,
        "status": "PLANNED",
        "authorization": {
            "decision": month["decision"],
            "month_assessment": str(args.month_assessment.resolve()),
            "month_assessment_sha256": sha256(args.month_assessment),
        },
        "scientific_plan": str(args.scientific_plan.resolve()),
        "scientific_plan_sha256": sha256(args.scientific_plan),
        "start": start.strftime(TIME_FORMAT),
        "end": end.strftime(TIME_FORMAT),
        "duration_days": int(period["duration_days"]),
        "expected_hicar_commit": configuration["month_expected_hicar_commit"],
        "output_profile": configuration["output_profile"],
        "output_interval_seconds": output_interval,
        "expected_unique_output_records": unique_output_records,
        "static_file": str(args.static_file.resolve()),
        "land_initialization_manifest": str(
            args.land_initialization_manifest.resolve()
        ),
        "land_initialization_sha256": sha256(
            args.land_initialization_manifest
        ),
        "main_initialization_status": main_initialization["status"],
        "shared_restart_dir": str(shared_restart_dir),
        "segments": segments,
        "combined_output_file_list": str(combined_output_list),
        "restart_trajectory_reports": trajectory_entries,
        "initialization_equivalence_reports": initialization_entries,
        "validation_sources": validation_sources,
        "archive_contract": str(archive_path),
        "archive_contract_sha256": sha256(archive_path),
        "observational_validation_contract": str(quality_path),
        "observational_validation_contract_sha256": sha256(quality_path),
        "validation_reports": {
            "physical": str(
                validation_dir / "scientific_annual_diagnostics.json"
            ),
            "rea_l_source": str(
                validation_dir / "rea_l_source_comparison.json"
            ),
            "swissmetnet": str(
                validation_dir / "swissmetnet_comparison.json"
            ),
            "ogd_grid": str(validation_dir / "ogd_grid_comparison.json"),
            "drift_screen": str(
                validation_dir / "seasonal_drift_screen.json"
            ),
            "drift_attribution": str(
                validation_dir / "seasonal_drift_attribution.json"
            ),
            "application_quality": str(
                validation_dir / "absolute_application_quality.json"
            ),
            "failure_recovery": str(
                validation_dir / "failure_recovery_drill.json"
            ),
            "archive_transfer_restore": str(
                validation_dir / "archive_transfer_restore.json"
            ),
            "production_release": str(
                validation_dir / "immutable_production_release.json"
            ),
            "annual_assessment": str(
                validation_dir / "scientific_annual_assessment.json"
            ),
        },
        "execution_policy": {
            "model": (
                "Run all 53 main segments sequentially through exact-end "
                "restarts; only the first reads the annual-start REA-L land "
                "initialization."
            ),
            "restart_equivalence": (
                "Preserve four season-representative main-chain checkpoints "
                "until their eight-day uninterrupted comparisons pass."
            ),
            "initialization_equivalence": (
                "Compare independent winter and summer REA-L land starts "
                "against the continuous annual chain after a declared "
                "seven-day spin-up, retaining 21 days."
            ),
            "promotion": (
                "Planning does not authorize production. Only the published "
                "annual assessor decision GO_20_YEAR_200M_PRODUCTION does."
            ),
        },
    }
    publish(report, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(
        f"annual pilot plan published: segments={len(segments)} "
        f"records={unique_output_records} trajectories={len(trajectory_entries)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
