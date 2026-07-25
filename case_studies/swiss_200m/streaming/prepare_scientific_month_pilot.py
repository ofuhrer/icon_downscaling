#!/usr/bin/env python3
"""Publish gate-authorized plans for the 31-day HICAR scientific pilot."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path

from create_chunk_plan import publish, records_for_period


TIME_FORMAT = "%Y-%m-%dT%H:%M:%S"
AUTHORIZED_DECISION = "GO_MONTH_AND_100M_CAPACITY_GATE"


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_publication(path: Path, label: str) -> dict:
    if not path.is_file():
        raise ValueError(f"{label} is missing: {path}")
    ready = Path(f"{path}.ready")
    if not ready.is_file():
        raise ValueError(f"{label} lacks ready marker: {ready}")
    return load_json(path)


def segment_periods(
    start: datetime, end: datetime, segment_days: int
) -> list[tuple[datetime, datetime]]:
    if segment_days < 1:
        raise ValueError("segment_days must be positive")
    periods = []
    current = start
    while current < end:
        following = min(current + timedelta(days=segment_days), end)
        periods.append((current, following))
        current = following
    return periods


def chunk_payload(
    chunk_id: str,
    start: datetime,
    end: datetime,
    chunk_root: Path,
    producer_concurrency: int,
) -> tuple[dict, str]:
    forcing_dir = chunk_root / "forcing"
    records = records_for_period(start, end, forcing_dir)
    forcing_list = chunk_root / "forcing_list.txt"
    payload = {
        "schema_version": 1,
        "status": "PLANNED",
        "chunk_id": chunk_id,
        "start": start.strftime(TIME_FORMAT),
        "end": end.strftime(TIME_FORMAT),
        "hours": int((end - start).total_seconds() // 3600),
        "record_count": len(records),
        "producer_concurrency": producer_concurrency,
        "cycle_policy": (
            "For each valid hour use that UTC date's 00 UTC cycle and step "
            "equal to the valid hour; use next-cycle step 0 at midnight and "
            "never previous-cycle step 24."
        ),
        "transient_policy": (
            "Native GRIB and converter work are job-local; forcing NetCDF is "
            "retired only after validated model output and restart publication."
        ),
        "chunk_root": str(chunk_root),
        "forcing_list": str(forcing_list),
        "records": records,
    }
    listed = "".join(f'"{record["forcing_file"]}"\n' for record in records)
    return payload, listed


def publish_chunk(
    chunk_id: str,
    start: datetime,
    end: datetime,
    chunk_root: Path,
    producer_concurrency: int,
) -> dict:
    payload, forcing_list = chunk_payload(
        chunk_id,
        start,
        end,
        chunk_root,
        producer_concurrency,
    )
    plan_path = chunk_root / "chunk_plan.json"
    forcing_list_path = chunk_root / "forcing_list.txt"
    publish(plan_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    publish(forcing_list_path, forcing_list)
    return {
        "chunk_id": chunk_id,
        "start": payload["start"],
        "end": payload["end"],
        "hours": payload["hours"],
        "forcing_record_count": payload["record_count"],
        "chunk_root": str(chunk_root),
        "chunk_plan": str(plan_path),
        "forcing_list": str(forcing_list_path),
    }


def add_output_contract(
    item: dict,
    chunk_root: Path,
    run_dir: Path,
    static_file: Path,
) -> None:
    start_file = item["start"].replace("T", "_").replace(":", "-")
    output = run_dir / "output" / f"{static_file.stem}_{start_file}.nc"
    output_list = chunk_root / "output_file_list.txt"
    publish(output_list, f"{output}\n")
    item["expected_output_file"] = str(output)
    item["output_file_list"] = str(output_list)
    compressed_output_dir = run_dir / "compressed_output"
    compressed_output = compressed_output_dir / output.name
    item["compressed_output_dir"] = str(compressed_output_dir)
    item["compressed_output_file"] = str(compressed_output)
    item["compression_report"] = str(
        Path(f"{compressed_output}.compression.json")
    )
    item["model_completion_report"] = str(
        run_dir / "model_chunk_completion.json"
    )
    item["forcing_retirement_report"] = str(
        run_dir / "forcing_retirement.json"
    )
    item["restart_retirement_report"] = str(
        run_dir / "restart_retirement.json"
    )
    item["solver_report"] = str(
        run_dir / "scientific_validation" / "solver_log_diagnostics.json"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scientific-plan", type=Path, required=True)
    parser.add_argument("--event-assessment", type=Path, required=True)
    parser.add_argument("--month-root", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--static-file", type=Path, required=True)
    parser.add_argument(
        "--land-initialization-manifest",
        type=Path,
        required=True,
    )
    parser.add_argument("--report", type=Path)
    parser.add_argument("--producer-concurrency", type=int, default=4)
    args = parser.parse_args()

    if args.producer_concurrency < 1:
        raise SystemExit("--producer-concurrency must be positive")
    scientific_plan = load_json(args.scientific_plan)
    assessment = require_publication(args.event_assessment, "event assessment")
    if assessment.get("assessment_status") != "COMPLETE":
        raise SystemExit("event assessment is not COMPLETE")
    if assessment.get("decision") != AUTHORIZED_DECISION:
        raise SystemExit(
            f"month pilot is not authorized: decision={assessment.get('decision')}"
        )
    if not assessment.get("authorization", {}).get("month_pilot"):
        raise SystemExit("event assessment does not authorize the month pilot")
    initialization = require_publication(
        args.land_initialization_manifest,
        "land initialization manifest",
    )
    if initialization.get("status") != "PASS":
        raise SystemExit("land initialization manifest is not PASS")
    if (
        not args.static_file.is_file()
        or not Path(f"{args.static_file}.ready").is_file()
    ):
        raise SystemExit("REA-L-initialized static file is not published")

    configuration = scientific_plan["configuration"]
    expected_month_commit = configuration.get("month_expected_hicar_commit")
    required_parent_commit = configuration.get(
        "month_required_parent_hicar_commit"
    )
    source_qualification = Path(
        configuration["month_source_qualification_report"]
    )
    if not source_qualification.is_absolute():
        source_qualification = (
            args.scientific_plan.resolve().parent / source_qualification
        ).resolve()
    source_qualification_sha256 = (
        sha256(source_qualification)
        if source_qualification.is_file()
        and Path(f"{source_qualification}.ready").is_file()
        else None
    )
    month = scientific_plan["reference_periods"]["month"]
    criteria = scientific_plan["promotion_criteria"]["month_to_annual_cycle"]
    start = datetime.fromisoformat(month["start"])
    end = start + timedelta(days=int(month["duration_days"]))
    segment_days = int(configuration["segment_days"])
    spinup_days = int(configuration["month_spinup_days"])
    overlap_days = int(configuration["month_restart_overlap_days"])
    comparison_hours = int(configuration["month_post_restart_comparison_hours"])
    output_interval = int(configuration["output_interval_seconds"])
    if spinup_days != int(criteria["declared_spinup_days"]):
        raise SystemExit("month spin-up declaration differs between plan sections")
    retained_days = int(month["duration_days"]) - spinup_days
    if retained_days < int(criteria["minimum_retained_days_after_declared_spinup"]):
        raise SystemExit("declared month spin-up leaves too few retained days")

    month_root = args.month_root.resolve()
    run_root = args.run_root.resolve()
    shared_restart_dir = run_root / "restart"
    periods = segment_periods(start, end, segment_days)
    segments = []
    unique_output_records = 0
    for index, (segment_start, segment_end) in enumerate(periods, start=1):
        chunk_id = f"month_{index:02d}_{segment_start:%Y%m%d%H}_{segment_end:%Y%m%d%H}"
        chunk_root = month_root / "segments" / chunk_id
        segment = publish_chunk(
            chunk_id,
            segment_start,
            segment_end,
            chunk_root,
            args.producer_concurrency,
        )
        segment["sequence"] = index
        segment_run_dir = run_root / "segments" / chunk_id
        segment["run_dir"] = str(segment_run_dir)
        segment["shared_restart_dir"] = str(shared_restart_dir)
        segment["restart_from"] = (
            None if index == 1 else segment_start.strftime(TIME_FORMAT)
        )
        segment["rea_l_land_initialization"] = index == 1
        segment["output_profile"] = configuration["output_profile"]
        segment["output_interval_seconds"] = output_interval
        expected = segment["hours"] * 3600 // output_interval
        segment["expected_output_records"] = expected + (1 if index == 1 else 0)
        add_output_contract(
            segment,
            chunk_root,
            segment_run_dir,
            args.static_file.resolve(),
        )
        unique_output_records += segment["expected_output_records"]
        segments.append(segment)

    if unique_output_records != int(criteria["expected_unique_output_records"]):
        raise SystemExit(
            "month segment output count does not match the frozen criterion"
        )
    if len(segments) < 3:
        raise SystemExit("month plan needs at least three segments for overlap testing")

    overlap_start = datetime.fromisoformat(segments[1]["start"])
    overlap_end = overlap_start + timedelta(days=overlap_days)
    crossed_boundary = datetime.fromisoformat(segments[2]["start"])
    if not (overlap_start < crossed_boundary < overlap_end):
        raise SystemExit("uninterrupted overlap does not cross a segment boundary")
    if int((overlap_end - crossed_boundary).total_seconds() // 3600) < comparison_hours:
        raise SystemExit("uninterrupted overlap has too little post-boundary coverage")
    overlap_id = (
        f"month_restart_overlap_{overlap_start:%Y%m%d%H}_{overlap_end:%Y%m%d%H}"
    )
    overlap = publish_chunk(
        overlap_id,
        overlap_start,
        overlap_end,
        month_root / "restart_overlap" / overlap_id,
        args.producer_concurrency,
    )
    overlap.update(
        {
            "run_dir": str(run_root / "restart_overlap" / overlap_id),
            "shared_restart_dir": str(shared_restart_dir),
            "restart_from": overlap_start.strftime(TIME_FORMAT),
            "common_restart_with_segment": segments[1]["chunk_id"],
            "crossed_segment_boundary": crossed_boundary.strftime(TIME_FORMAT),
            "comparison_start": crossed_boundary.strftime(TIME_FORMAT),
            "comparison_end": (
                crossed_boundary + timedelta(hours=comparison_hours)
            ).strftime(TIME_FORMAT),
            "checkpoint_to_preserve": (
                shared_restart_dir
                / f"{args.static_file.stem}_{overlap_start:%Y-%m-%d_%H-%M-%S}.nc"
            ).as_posix(),
            "output_profile": configuration["output_profile"],
            "output_interval_seconds": output_interval,
            "expected_output_records": (
                int((overlap_end - overlap_start).total_seconds()) // output_interval
            ),
        }
    )
    add_output_contract(
        overlap,
        month_root / "restart_overlap" / overlap_id,
        Path(overlap["run_dir"]),
        args.static_file.resolve(),
    )

    validation_id = f"month_validation_sources_{start:%Y%m%d%H}_{end:%Y%m%d%H}"
    validation_root = month_root / "validation_sources"
    validation_sources = publish_chunk(
        validation_id,
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
            "reference_list": str(validation_root / "reference" / "reference_list.txt"),
            "reference_publication": str(
                validation_root / "reference" / "reference_publication.json"
            ),
            "swissmetnet_observations": str(
                validation_root / "observations" / "swissmetnet_hourly.csv"
            ),
            "swissmetnet_manifest": str(
                validation_root / "observations" / "swissmetnet_hourly.manifest.json"
            ),
        }
    )

    report = (args.report or month_root / "month_pilot_plan.json").resolve()
    validation_dir = month_root / "validation"
    payload = {
        "schema_version": 1,
        "status": "PLANNED",
        "authorization": {
            "decision": assessment["decision"],
            "event_assessment": str(args.event_assessment.resolve()),
            "event_assessment_sha256": sha256(args.event_assessment),
        },
        "scientific_plan": str(args.scientific_plan.resolve()),
        "scientific_plan_sha256": sha256(args.scientific_plan),
        "start": start.strftime(TIME_FORMAT),
        "end": end.strftime(TIME_FORMAT),
        "duration_days": int(month["duration_days"]),
        "declared_spinup_days": spinup_days,
        "retained_days": retained_days,
        "expected_hicar_commit": expected_month_commit,
        "required_parent_hicar_commit": required_parent_commit,
        "source_qualification_report": str(source_qualification),
        "source_qualification_sha256": source_qualification_sha256,
        "output_profile": configuration["output_profile"],
        "output_interval_seconds": output_interval,
        "expected_unique_output_records": unique_output_records,
        "static_file": str(args.static_file.resolve()),
        "land_initialization_manifest": str(
            args.land_initialization_manifest.resolve()
        ),
        "shared_restart_dir": str(shared_restart_dir),
        "segments": segments,
        "uninterrupted_restart_overlap": overlap,
        "validation_sources": validation_sources,
        "restart_trajectory_report": str(
            month_root / "restart_trajectory_comparison.json"
        ),
        "validation_reports": {
            "physical": str(
                validation_dir / "scientific_month_diagnostics.json"
            ),
            "rea_l_source": str(
                validation_dir / "rea_l_source_comparison.json"
            ),
            "swissmetnet": str(
                validation_dir / "swissmetnet_comparison.json"
            ),
            "ogd_grid": str(
                validation_dir / "ogd_grid_comparison.json"
            ),
            "drift_screen": str(
                validation_dir / "postspinup_drift_screen.json"
            ),
            "drift_attribution": str(
                validation_dir / "postspinup_drift_attribution.json"
            ),
            "month_assessment": str(
                validation_dir / "scientific_month_assessment.json"
            ),
        },
        "archive_contract": str(
            args.scientific_plan.resolve().parent
            / "production_archive_contract.json"
        ),
        "observational_validation_contract": str(
            args.scientific_plan.resolve().parent
            / "observational_validation_contract.json"
        ),
        "execution_policy": {
            "forcing": (
                "Produce each segment in a bounded four-worker array; begin the "
                "next segment while the current GPU segment runs."
            ),
            "model": (
                "Run segments sequentially through exact-end restarts. Only the "
                "first segment reads REA-L land initialization."
            ),
            "restart_retention": (
                "Keep the current and previous boundary plus the overlap start "
                "checkpoint until trajectory equivalence passes."
            ),
            "promotion": (
                "This publication authorizes planning only. Annual execution "
                "still requires every frozen month_to_annual_cycle criterion."
            ),
        },
        "required_month_evidence": [
            "unique monotonic three-hourly output across all five segments",
            "all per-segment model and solver reports PASS",
            "at least 24 hours of post-boundary segmented-versus-uninterrupted trajectory equivalence",
            "retained-period water, energy, soil, snow, canopy, and near-surface trend diagnostics",
            "production cumulative water observables and exact restart continuity",
            "output-diagnostic-only child-source qualification against the preserved event parent",
            "side-by-side HICAR and REA-L SwissMetNet metrics",
            "31 TabsD days, 30 complete RhiresD windows, and July SIS diagnostics",
            "measured forcing overlap, restart, output, compression, and archive costs",
        ],
    }
    publish(report, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(
        f"month pilot plan published: segments={len(segments)} "
        f"records={unique_output_records} overlap_hours={overlap['hours']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
