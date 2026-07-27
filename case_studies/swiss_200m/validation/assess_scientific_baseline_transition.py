#!/usr/bin/env python3
"""Assess whether a scientifically new HICAR baseline may enter the month gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def published_json(path: Path) -> dict:
    if not path.is_file():
        raise ValueError(f"missing publication: {path}")
    ready = Path(f"{path}.ready")
    if not ready.is_file():
        raise ValueError(f"publication lacks ready marker: {ready}")
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def report_path(run_dir: Path, relative: str) -> Path:
    return run_dir / relative


def assess_event(
    name: str,
    run_dir: Path,
    contract: dict,
) -> tuple[dict, list[str]]:
    failures: list[str] = []
    reports: dict[str, dict] = {}
    for relative in contract["required_event_reports"]:
        path = report_path(run_dir, relative)
        try:
            reports[relative] = published_json(path)
        except ValueError as error:
            failures.append(str(error))

    model = reports.get("model_chunk_completion.json", {})
    provenance = model.get("provenance", {})
    if model.get("status") != "PASS":
        failures.append(f"{name} model completion is not PASS")
    if provenance.get("status") != "PASS":
        failures.append(f"{name} model provenance is not PASS")
    if provenance.get("source_commit") != contract["candidate_commit"]:
        failures.append(f"{name} source commit does not match candidate")
    if (
        provenance.get("executable_sha256")
        != contract["candidate_executable_sha256"]
    ):
        failures.append(f"{name} executable checksum does not match candidate")

    frozen = contract["frozen_inputs"]
    required_static = frozen[f"{name}_static_sha256"]
    if provenance.get("static_sha256") != required_static:
        failures.append(f"{name} static checksum does not match frozen input")

    physical = reports.get(
        "scientific_validation/scientific_event_diagnostics.json", {}
    )
    water = physical.get("water_budget_contract", {})
    water_gate = contract["water_budget_gate"]
    if water.get("mode") != water_gate["mode"]:
        failures.append(f"{name} water budget is not production cumulative")
    if water.get("production_eligible") is not water_gate["production_eligible"]:
        failures.append(f"{name} water budget is not production eligible")
    for field in water_gate["required_zero_decrease_counts"]:
        if physical.get(field) != 0:
            failures.append(f"{name} {field} is not zero")

    storage = water.get("storage", {})
    summed = set(storage.get("summed_for_closure", []))
    required_stores = set(water_gate["required_closure_stores"])
    if not required_stores <= summed:
        failures.append(f"{name} water closure omits required stores")
    diagnostic_only = {
        item.get("field")
        for item in storage.get("diagnostic_not_summed", [])
        if isinstance(item, dict)
    }
    if water_gate["diagnostic_not_summed"] not in diagnostic_only:
        failures.append(f"{name} storage_gw role is not explicit")

    gate_class = water_gate["gate_class"]
    diagnostic = (
        physical.get("classes", {})
        .get(gate_class, {})
        .get("water_diagnostic_kg_m2", {})
    )
    residual = diagnostic.get("residual")
    maximum = float(water_gate["maximum_absolute_residual_kg_m2_per_event"])
    if residual is None or abs(float(residual)) > maximum:
        failures.append(
            f"{name} {gate_class} water residual exceeds {maximum} kg m-2"
        )
    runoff = diagnostic.get("runoff")
    if runoff is None or float(runoff) < 0:
        failures.append(f"{name} runoff diagnostic is missing or negative")

    return (
        {
            "event": name,
            "run_dir": str(run_dir.resolve()),
            "status": "PASS" if not failures else "FAIL",
            "source_commit": provenance.get("source_commit"),
            "executable_sha256": provenance.get("executable_sha256"),
            "static_sha256": provenance.get("static_sha256"),
            "water_budget": {
                "mode": water.get("mode"),
                "production_eligible": water.get("production_eligible"),
                "gate_class": gate_class,
                "residual_kg_m2": residual,
                "maximum_absolute_residual_kg_m2": maximum,
                "runoff_kg_m2": runoff,
            },
        },
        failures,
    )


def assess(
    *,
    contract: dict,
    transition_plan_path: Path,
    candidate_plan_path: Path,
    runtime_manifest_path: Path,
    paired_assessment: dict,
    summer_run: Path,
    winter_run: Path,
    restart_overlap_completion: dict,
    restart_trajectory: dict,
    source_bundle_path: Path | None = None,
) -> dict:
    failures: list[str] = []
    frozen = contract["frozen_inputs"]
    for label, path, expected in (
        (
            "transition plan",
            transition_plan_path,
            frozen["transition_plan_sha256"],
        ),
        (
            "candidate scientific plan",
            candidate_plan_path,
            frozen["candidate_scientific_plan_sha256"],
        ),
        (
            "runtime manifest",
            runtime_manifest_path,
            frozen["runtime_manifest_sha256"],
        ),
    ):
        if not path.is_file() or sha256(path) != expected:
            failures.append(f"{label} checksum does not match frozen contract")

    source_bundle = (
        source_bundle_path
        if source_bundle_path is not None
        else Path(contract["source_bundle"]["path"])
    )
    if not source_bundle.is_file() or sha256(source_bundle) != contract[
        "source_bundle"
    ]["sha256"]:
        failures.append("candidate source bundle checksum is not recoverable")

    events = []
    pair_runoff = 0.0
    for name, run in (("summer", summer_run), ("winter", winter_run)):
        event, event_failures = assess_event(name, run, contract)
        events.append(event)
        failures.extend(event_failures)
        runoff = event["water_budget"]["runoff_kg_m2"]
        if runoff is not None:
            pair_runoff += max(0.0, float(runoff))
    if (
        contract["water_budget_gate"]["required_nonzero_pair_total_runoff"]
        and pair_runoff <= 0
    ):
        failures.append("paired events do not exercise nonzero runoff")

    expected_decision = contract["required_event_assessment_decision"]
    if paired_assessment.get("decision") != expected_decision:
        failures.append("paired event assessment did not reach its required decision")
    observed_event_names = {
        item.get("event") for item in paired_assessment.get("events", [])
    }
    if observed_event_names != set(contract["required_events"]):
        failures.append("paired assessment does not cover summer and winter")

    overlap_provenance = restart_overlap_completion.get("provenance", {})
    if restart_overlap_completion.get("status") != "PASS":
        failures.append("restart overlap completion is not PASS")
    if overlap_provenance.get("status") != "PASS":
        failures.append("restart overlap provenance is not PASS")
    if overlap_provenance.get("source_commit") != contract["candidate_commit"]:
        failures.append("restart overlap source commit does not match candidate")
    if (
        overlap_provenance.get("executable_sha256")
        != contract["candidate_executable_sha256"]
    ):
        failures.append("restart overlap executable checksum does not match candidate")
    if (
        overlap_provenance.get("static_sha256")
        != contract["frozen_inputs"]["summer_static_sha256"]
    ):
        failures.append("restart overlap static checksum does not match summer")

    trajectory_gate = contract["restart_trajectory_gate"]
    if restart_trajectory.get("status") != trajectory_gate["required_status"]:
        failures.append("restart trajectory status is not PASS")
    if restart_trajectory.get("failures"):
        failures.append("restart trajectory reports failures")
    expected_times = restart_trajectory.get("expected_times", [])
    if len(expected_times) != int(trajectory_gate["expected_records"]):
        failures.append("restart trajectory has the wrong record count")
    metrics = restart_trajectory.get("metrics", {})
    missing_fields = sorted(set(trajectory_gate["required_fields"]) - set(metrics))
    if missing_fields:
        failures.append(
            "restart trajectory omits required cumulative fields: "
            + ",".join(missing_fields)
        )

    decision = (
        contract["decision"]["pass"]
        if not failures
        else contract["decision"]["single_event_or_restart_failure"]
    )
    return {
        "schema_version": 1,
        "status": "PASS" if not failures else "FAIL",
        "decision": decision,
        "candidate_commit": contract["candidate_commit"],
        "events": events,
        "paired_total_runoff_kg_m2": pair_runoff,
        "paired_event_assessment_decision": paired_assessment.get("decision"),
        "restart_trajectory_status": restart_trajectory.get("status"),
        "restart_overlap_source_commit": overlap_provenance.get("source_commit"),
        "restart_overlap_executable_sha256": overlap_provenance.get(
            "executable_sha256"
        ),
        "failures": failures,
        "authorization": {
            "canonical_month_source_nomination": not failures,
            "month_compute": False,
            "annual_cycle": False,
            "twenty_year_200m_production": False,
            "hundred_meter_scientific_production": False,
        },
    }


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--transition-plan", type=Path, required=True)
    parser.add_argument("--candidate-plan", type=Path, required=True)
    parser.add_argument("--runtime-manifest", type=Path, required=True)
    parser.add_argument("--paired-event-assessment", type=Path, required=True)
    parser.add_argument("--summer-run", type=Path, required=True)
    parser.add_argument("--winter-run", type=Path, required=True)
    parser.add_argument("--restart-overlap-completion", type=Path, required=True)
    parser.add_argument("--restart-trajectory-report", type=Path, required=True)
    parser.add_argument("--source-bundle", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    try:
        contract = published_json(args.contract)
        paired = published_json(args.paired_event_assessment)
        overlap = published_json(args.restart_overlap_completion)
        trajectory = published_json(args.restart_trajectory_report)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    if contract.get("status") != "FROZEN":
        raise SystemExit("baseline transition assessment contract is not FROZEN")

    payload = assess(
        contract=contract,
        transition_plan_path=args.transition_plan,
        candidate_plan_path=args.candidate_plan,
        runtime_manifest_path=args.runtime_manifest,
        paired_assessment=paired,
        summer_run=args.summer_run,
        winter_run=args.winter_run,
        restart_overlap_completion=overlap,
        restart_trajectory=trajectory,
        source_bundle_path=args.source_bundle,
    )
    atomic_json(args.report, payload)
    Path(f"{args.report}.ready").touch()
    print(f"{payload['decision']}: baseline transition assessment published")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
