#!/usr/bin/env python3
"""Publish a month-source qualification from a passed baseline transition."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from month_source_contract import (
    SCIENTIFIC_BASELINE_TRANSITION,
    validate_month_source_qualification,
)


PASS_DECISION = "NOMINATE_V29_FOR_CANONICAL_MONTH_SOURCE"
PAIRED_EVENT_DECISION = "GO_MONTH_AND_100M_CAPACITY_GATE"
WATER_FIELDS = (
    "precipitation",
    "runoff_surface_cumulative",
    "runoff_subsurface_cumulative",
    "evaporation_net_cumulative",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def published_json(path: Path, label: str) -> dict:
    if not path.is_file():
        raise ValueError(f"{label} is missing: {path}")
    ready = Path(f"{path}.ready")
    if not ready.is_file():
        raise ValueError(f"{label} lacks ready marker: {ready}")
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def build_qualification(
    *,
    transition_report: dict,
    transition_report_path: Path,
    transition_plan: dict,
    transition_plan_path: Path,
    assessment_contract: dict,
    assessment_contract_path: Path,
    source_bundle_path: Path,
) -> dict:
    failures: list[str] = []
    candidate = assessment_contract.get("candidate_commit")
    parent = assessment_contract.get("candidate_parent_commit")
    expected_executable = assessment_contract.get(
        "candidate_executable_sha256"
    )
    frozen_transition_plan_sha256 = assessment_contract.get(
        "frozen_inputs", {}
    ).get("transition_plan_sha256")

    if assessment_contract.get("status") != "FROZEN":
        failures.append("baseline transition assessment contract is not FROZEN")
    if transition_report.get("status") != "PASS":
        failures.append("baseline transition report is not PASS")
    if transition_report.get("decision") != PASS_DECISION:
        failures.append("baseline transition report did not nominate the candidate")
    if transition_report.get("candidate_commit") != candidate:
        failures.append("baseline transition candidate does not match its contract")
    if (
        transition_plan.get("status") != "PLANNED"
        or transition_plan.get("classification")
        != "SCIENTIFIC_BASELINE_CANDIDATE"
    ):
        failures.append("baseline transition plan is not a planned candidate")
    if transition_plan.get("candidate_commit") != candidate:
        failures.append("baseline transition plan candidate does not match contract")
    if transition_plan.get("candidate_parent_commit") != parent:
        failures.append("baseline transition plan parent does not match contract")
    if sha256(transition_plan_path) != frozen_transition_plan_sha256:
        failures.append("baseline transition plan checksum is not frozen by contract")
    previous_baseline = transition_plan.get("preserved_event_commit")
    if transition_report.get("failures"):
        failures.append("baseline transition report contains failures")
    authorization = transition_report.get("authorization", {})
    if authorization.get("canonical_month_source_nomination") is not True:
        failures.append("baseline transition does not nominate a month source")
    for name in (
        "month_compute",
        "annual_cycle",
        "twenty_year_200m_production",
        "hundred_meter_scientific_production",
    ):
        if authorization.get(name) is not False:
            failures.append(f"baseline transition over-authorizes {name}")

    events = transition_report.get("events", [])
    event_by_name = {
        event.get("event"): event for event in events if isinstance(event, dict)
    }
    if set(event_by_name) != {"summer", "winter"}:
        failures.append("baseline transition does not cover summer and winter")
    event_executables: set[str] = set()
    event_statuses: dict[str, object] = {}
    for name in ("summer", "winter"):
        event = event_by_name.get(name, {})
        event_statuses[name] = event.get("status")
        if event.get("status") != "PASS":
            failures.append(f"{name} baseline transition event is not PASS")
        if event.get("source_commit") != candidate:
            failures.append(f"{name} source commit does not match candidate")
        executable = event.get("executable_sha256")
        if executable:
            event_executables.add(str(executable))
        if executable != expected_executable:
            failures.append(f"{name} executable does not match frozen candidate")
        if not event.get("static_sha256"):
            failures.append(f"{name} static identity is missing")
        water = event.get("water_budget", {})
        if (
            water.get("mode") != "production_cumulative"
            or water.get("production_eligible") is not True
        ):
            failures.append(f"{name} water budget is not production cumulative")
        residual = water.get("residual_kg_m2")
        maximum = water.get("maximum_absolute_residual_kg_m2")
        if (
            not isinstance(residual, (int, float))
            or not isinstance(maximum, (int, float))
            or abs(residual) > maximum
        ):
            failures.append(f"{name} water residual exceeds its frozen threshold")
        runoff = water.get("runoff_kg_m2")
        if not isinstance(runoff, (int, float)) or runoff < 0:
            failures.append(f"{name} runoff diagnostic is missing or negative")

    if len(event_executables) != 1:
        failures.append("baseline transition event executables are inconsistent")
    if transition_report.get("paired_event_assessment_decision") != (
        PAIRED_EVENT_DECISION
    ):
        failures.append("paired scientific-event assessment did not pass")
    pair_runoff = transition_report.get("paired_total_runoff_kg_m2")
    if not isinstance(pair_runoff, (int, float)) or pair_runoff <= 0:
        failures.append("paired baseline events do not exercise nonzero runoff")
    if transition_report.get("restart_trajectory_status") != "PASS":
        failures.append("baseline transition restart trajectory is not PASS")
    if transition_report.get("restart_overlap_source_commit") != candidate:
        failures.append("restart overlap source does not match candidate")
    if (
        transition_report.get("restart_overlap_executable_sha256")
        != expected_executable
    ):
        failures.append("restart overlap executable does not match candidate")

    bundle_contract = assessment_contract.get("source_bundle", {})
    bundle_sha256 = sha256(source_bundle_path) if source_bundle_path.is_file() else None
    if (
        not source_bundle_path.is_file()
        or bundle_sha256 != bundle_contract.get("sha256")
    ):
        failures.append("candidate source bundle is missing or checksum-mismatched")

    payload = {
        "schema_version": 1,
        "status": "PASS" if not failures else "FAIL",
        "qualification_mode": SCIENTIFIC_BASELINE_TRANSITION,
        "change_scope": SCIENTIFIC_BASELINE_TRANSITION,
        "child_commit": candidate,
        "parent_commit": parent,
        "previous_scientific_baseline_commit": previous_baseline,
        "evidence": {
            "baseline_transition": {
                "status": "PASS" if not failures else "FAIL",
                "artifact": str(transition_report_path.resolve()),
                "artifact_sha256": sha256(transition_report_path),
                "report_status": transition_report.get("status"),
                "decision": transition_report.get("decision"),
                "candidate_commit": transition_report.get("candidate_commit"),
                "event_names": sorted(event_by_name),
                "event_statuses": event_statuses,
                "event_executable_sha256": (
                    next(iter(event_executables))
                    if len(event_executables) == 1
                    else None
                ),
                "paired_total_runoff_kg_m2": pair_runoff,
                "restart_trajectory_status": transition_report.get(
                    "restart_trajectory_status"
                ),
                "restart_trajectory_fields": list(WATER_FIELDS),
            },
            "assessment_contract": {
                "status": (
                    "PASS"
                    if assessment_contract.get("status") == "FROZEN"
                    else "FAIL"
                ),
                "artifact": str(assessment_contract_path.resolve()),
                "artifact_sha256": sha256(assessment_contract_path),
                "contract_status": assessment_contract.get("status"),
                "candidate_commit": candidate,
                "candidate_parent_commit": parent,
            },
            "transition_plan": {
                "status": (
                    "PASS"
                    if transition_plan.get("status") == "PLANNED"
                    and transition_plan.get("candidate_commit") == candidate
                    and transition_plan.get("candidate_parent_commit") == parent
                    and sha256(transition_plan_path)
                    == frozen_transition_plan_sha256
                    else "FAIL"
                ),
                "artifact": str(transition_plan_path.resolve()),
                "artifact_sha256": sha256(transition_plan_path),
                "plan_status": transition_plan.get("status"),
                "candidate_commit": transition_plan.get("candidate_commit"),
                "candidate_parent_commit": transition_plan.get(
                    "candidate_parent_commit"
                ),
                "preserved_event_commit": previous_baseline,
            },
            "candidate_source_bundle": {
                "status": (
                    "PASS"
                    if source_bundle_path.is_file()
                    and bundle_sha256 == bundle_contract.get("sha256")
                    else "FAIL"
                ),
                "artifact": str(source_bundle_path),
                "artifact_sha256": bundle_sha256,
                "source_commit": candidate,
            },
        },
        "authorization": {
            "month_source": not failures,
            "month_compute": False,
            "annual_cycle": False,
            "twenty_year_200m_production": False,
            "hundred_meter_scientific_production": False,
        },
        "failures": failures,
    }
    contract_failures = validate_month_source_qualification(
        payload,
        expected_child_commit=candidate,
        required_parent_commit=parent,
        qualification_mode=SCIENTIFIC_BASELINE_TRANSITION,
    )
    for failure in contract_failures:
        if failure not in payload["failures"]:
            payload["failures"].append(failure)
    if payload["failures"]:
        payload["status"] = "FAIL"
        payload["authorization"]["month_source"] = False
    return payload


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def publish_json(path: Path, payload: dict) -> None:
    marker = Path(f"{path}.ready")
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if path.exists() or marker.exists():
        if (
            path.is_file()
            and marker.is_file()
            and path.read_text(encoding="utf-8") == serialized
        ):
            return
        raise ValueError(
            f"refusing to replace non-identical source qualification: {path}"
        )
    atomic_json(path, payload)
    marker.touch()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transition-report", type=Path, required=True)
    parser.add_argument("--transition-plan", type=Path, required=True)
    parser.add_argument("--assessment-contract", type=Path, required=True)
    parser.add_argument("--source-bundle", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    try:
        transition = published_json(
            args.transition_report, "baseline transition report"
        )
        transition_plan = published_json(
            args.transition_plan, "baseline transition plan"
        )
        contract = published_json(
            args.assessment_contract, "baseline transition assessment contract"
        )
    except ValueError as error:
        raise SystemExit(str(error)) from error
    payload = build_qualification(
        transition_report=transition,
        transition_report_path=args.transition_report,
        transition_plan=transition_plan,
        transition_plan_path=args.transition_plan,
        assessment_contract=contract,
        assessment_contract_path=args.assessment_contract,
        source_bundle_path=args.source_bundle,
    )
    try:
        publish_json(args.report, payload)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    print(f"{payload['status']}: baseline transition month-source qualification")
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
