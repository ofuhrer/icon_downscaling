from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "case_studies/swiss_200m/validation"
sys.path.insert(0, str(VALIDATION))
MODULE_PATH = VALIDATION / "publish_baseline_transition_month_source.py"
SPEC = importlib.util.spec_from_file_location(
    "baseline_transition_month_source", MODULE_PATH
)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


CANDIDATE = "a" * 40
PARENT = "b" * 40
PRESERVED = "c" * 40
EXECUTABLE = "d" * 64


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload))


def fixtures(
    tmp_path: Path,
) -> tuple[dict, Path, dict, Path, dict, Path, Path]:
    transition_path = tmp_path / "transition.json"
    plan_path = tmp_path / "transition_plan.json"
    contract_path = tmp_path / "contract.json"
    bundle = tmp_path / "candidate.bundle"
    bundle.write_bytes(b"candidate")
    events = []
    for name, static in (("summer", "1" * 64), ("winter", "2" * 64)):
        events.append(
            {
                "event": name,
                "status": "PASS",
                "source_commit": CANDIDATE,
                "executable_sha256": EXECUTABLE,
                "static_sha256": static,
                "water_budget": {
                    "mode": "production_cumulative",
                    "production_eligible": True,
                    "residual_kg_m2": 0.5,
                    "maximum_absolute_residual_kg_m2": 5.0,
                    "runoff_kg_m2": 0.25,
                },
            }
        )
    transition = {
        "schema_version": 1,
        "status": "PASS",
        "decision": "NOMINATE_V29_FOR_CANONICAL_MONTH_SOURCE",
        "candidate_commit": CANDIDATE,
        "events": events,
        "paired_total_runoff_kg_m2": 0.5,
        "paired_event_assessment_decision": "GO_MONTH_AND_100M_CAPACITY_GATE",
        "restart_trajectory_status": "PASS",
        "restart_overlap_source_commit": CANDIDATE,
        "restart_overlap_executable_sha256": EXECUTABLE,
        "failures": [],
        "authorization": {
            "canonical_month_source_nomination": True,
            "month_compute": False,
            "annual_cycle": False,
            "twenty_year_200m_production": False,
            "hundred_meter_scientific_production": False,
        },
    }
    plan = {
        "schema_version": 1,
        "status": "PLANNED",
        "classification": "SCIENTIFIC_BASELINE_CANDIDATE",
        "candidate_commit": CANDIDATE,
        "candidate_parent_commit": PARENT,
        "preserved_event_commit": PRESERVED,
    }
    write_json(plan_path, plan)
    contract = {
        "schema_version": 1,
        "status": "FROZEN",
        "candidate_commit": CANDIDATE,
        "candidate_parent_commit": PARENT,
        "candidate_executable_sha256": EXECUTABLE,
        "frozen_inputs": {
            "transition_plan_sha256": MODULE.sha256(plan_path),
        },
        "source_bundle": {
            "path": str(bundle),
            "sha256": MODULE.sha256(bundle),
        },
    }
    write_json(transition_path, transition)
    write_json(contract_path, contract)
    return (
        transition,
        transition_path,
        plan,
        plan_path,
        contract,
        contract_path,
        bundle,
    )


def test_passed_transition_publishes_guarded_month_source(tmp_path: Path) -> None:
    (
        transition,
        transition_path,
        plan,
        plan_path,
        contract,
        contract_path,
        bundle,
    ) = fixtures(tmp_path)

    report = MODULE.build_qualification(
        transition_report=transition,
        transition_report_path=transition_path,
        transition_plan=plan,
        transition_plan_path=plan_path,
        assessment_contract=contract,
        assessment_contract_path=contract_path,
        source_bundle_path=bundle,
    )

    assert report["status"] == "PASS"
    assert report["qualification_mode"] == "SCIENTIFIC_BASELINE_TRANSITION"
    assert report["child_commit"] == CANDIDATE
    assert report["parent_commit"] == PARENT
    assert report["previous_scientific_baseline_commit"] == PRESERVED
    assert report["authorization"]["month_source"] is True
    assert report["authorization"]["month_compute"] is False
    assert report["failures"] == []


def test_transition_source_rejects_mixed_executable_identity(
    tmp_path: Path,
) -> None:
    (
        transition,
        transition_path,
        plan,
        plan_path,
        contract,
        contract_path,
        bundle,
    ) = fixtures(tmp_path)
    transition["events"][1]["executable_sha256"] = "e" * 64
    write_json(transition_path, transition)

    report = MODULE.build_qualification(
        transition_report=transition,
        transition_report_path=transition_path,
        transition_plan=plan,
        transition_plan_path=plan_path,
        assessment_contract=contract,
        assessment_contract_path=contract_path,
        source_bundle_path=bundle,
    )

    assert report["status"] == "FAIL"
    assert report["authorization"]["month_source"] is False
    assert any("executable" in failure for failure in report["failures"])


def test_transition_source_rejects_failed_or_zero_runoff_event(
    tmp_path: Path,
) -> None:
    (
        transition,
        transition_path,
        plan,
        plan_path,
        contract,
        contract_path,
        bundle,
    ) = fixtures(tmp_path)
    transition["events"][0]["status"] = "FAIL"
    transition["events"][0]["water_budget"]["runoff_kg_m2"] = 0.0
    transition["events"][1]["water_budget"]["runoff_kg_m2"] = 0.0
    transition["paired_total_runoff_kg_m2"] = 0.0
    write_json(transition_path, transition)

    report = MODULE.build_qualification(
        transition_report=transition,
        transition_report_path=transition_path,
        transition_plan=plan,
        transition_plan_path=plan_path,
        assessment_contract=contract,
        assessment_contract_path=contract_path,
        source_bundle_path=bundle,
    )

    assert report["status"] == "FAIL"
    assert any("summer baseline transition event" in item for item in report["failures"])
    assert any("nonzero runoff" in item for item in report["failures"])


def test_transition_source_rejects_changed_previous_baseline_plan(
    tmp_path: Path,
) -> None:
    (
        transition,
        transition_path,
        plan,
        plan_path,
        contract,
        contract_path,
        bundle,
    ) = fixtures(tmp_path)
    plan["preserved_event_commit"] = "e" * 40
    write_json(plan_path, plan)

    report = MODULE.build_qualification(
        transition_report=transition,
        transition_report_path=transition_path,
        transition_plan=plan,
        transition_plan_path=plan_path,
        assessment_contract=contract,
        assessment_contract_path=contract_path,
        source_bundle_path=bundle,
    )

    assert report["status"] == "FAIL"
    assert any("checksum" in failure for failure in report["failures"])


def test_source_qualification_publication_is_immutable(tmp_path: Path) -> None:
    path = tmp_path / "qualification.json"
    payload = {"status": "PASS"}
    MODULE.publish_json(path, payload)
    MODULE.publish_json(path, payload)

    with pytest.raises(ValueError, match="refusing to replace"):
        MODULE.publish_json(path, {"status": "FAIL"})
