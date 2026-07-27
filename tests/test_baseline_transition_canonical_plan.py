from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "case_studies/swiss_200m/validation"
sys.path.insert(0, str(VALIDATION))
MODULE_PATH = VALIDATION / "prepare_baseline_transition_canonical_plan.py"
FINALIZER = (
    ROOT
    / "case_studies/swiss_200m/scripts"
    / "finalize_baseline_transition_source_balfrin.sbatch"
)
SPEC = importlib.util.spec_from_file_location(
    "baseline_transition_canonical_plan", MODULE_PATH
)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


CANDIDATE = "a" * 40
PARENT = "b" * 40
PREVIOUS = "c" * 40


def source_qualification(transition_sha256: str) -> dict:
    return {
        "schema_version": 1,
        "status": "PASS",
        "qualification_mode": "SCIENTIFIC_BASELINE_TRANSITION",
        "change_scope": "SCIENTIFIC_BASELINE_TRANSITION",
        "child_commit": CANDIDATE,
        "parent_commit": PARENT,
        "previous_scientific_baseline_commit": PREVIOUS,
        "evidence": {
            "baseline_transition": {
                "status": "PASS",
                "artifact_sha256": transition_sha256,
                "report_status": "PASS",
                "decision": "NOMINATE_V29_FOR_CANONICAL_MONTH_SOURCE",
                "candidate_commit": CANDIDATE,
                "event_names": ["summer", "winter"],
                "event_statuses": {
                    "summer": "PASS",
                    "winter": "PASS",
                },
                "restart_trajectory_status": "PASS",
                "restart_trajectory_fields": [
                    "precipitation",
                    "runoff_surface_cumulative",
                    "runoff_subsurface_cumulative",
                    "evaporation_net_cumulative",
                ],
                "paired_total_runoff_kg_m2": 0.5,
            },
            "assessment_contract": {
                "status": "PASS",
                "artifact_sha256": "2" * 64,
                "contract_status": "FROZEN",
                "candidate_commit": CANDIDATE,
                "candidate_parent_commit": PARENT,
            },
            "transition_plan": {
                "status": "PASS",
                "artifact_sha256": "3" * 64,
                "plan_status": "PLANNED",
                "candidate_commit": CANDIDATE,
                "candidate_parent_commit": PARENT,
                "preserved_event_commit": PREVIOUS,
            },
            "candidate_source_bundle": {
                "status": "PASS",
                "artifact_sha256": "4" * 64,
                "source_commit": CANDIDATE,
            },
        },
        "authorization": {
            "month_source": True,
            "month_compute": False,
            "annual_cycle": False,
            "twenty_year_200m_production": False,
            "hundred_meter_scientific_production": False,
        },
    }


def fixture(tmp_path: Path) -> tuple[dict, dict, Path, Path, Path]:
    transition_path = tmp_path / "transition.json"
    transition = {
        "status": "PASS",
        "decision": "NOMINATE_V29_FOR_CANONICAL_MONTH_SOURCE",
        "candidate_commit": CANDIDATE,
    }
    transition_path.write_text(json.dumps(transition))
    qualification_path = tmp_path / "qualification.json"
    qualification = source_qualification(MODULE.sha256(transition_path))
    qualification_path.write_text(json.dumps(qualification))
    plan = {
        "schema_version": 1,
        "name": "test",
        "configuration": {
            "event_expected_hicar_commit": PREVIOUS,
            "month_expected_hicar_commit": None,
            "month_required_parent_hicar_commit": PREVIOUS,
            "month_source_qualification_report": "old.json",
        },
    }
    output = tmp_path / "candidate" / "scientific_plan.json"
    return plan, qualification, qualification_path, transition_path, output


def test_passed_transition_prepares_non_authorizing_canonical_candidate(
    tmp_path: Path,
) -> None:
    plan, qualification, qualification_path, transition_path, output = fixture(
        tmp_path
    )
    transition = json.loads(transition_path.read_text())

    candidate = MODULE.prepare(
        scientific_plan=plan,
        source_qualification=qualification,
        source_qualification_path=qualification_path,
        transition_report=transition,
        transition_report_path=transition_path,
        output_path=output,
    )

    configuration = candidate["configuration"]
    assert configuration["event_expected_hicar_commit"] == CANDIDATE
    assert configuration["month_expected_hicar_commit"] == CANDIDATE
    assert configuration["month_required_parent_hicar_commit"] == PARENT
    assert (
        configuration["month_source_qualification_mode"]
        == "SCIENTIFIC_BASELINE_TRANSITION"
    )
    assert (
        candidate["source_selection"]["previous_scientific_baseline_hicar_commit"]
        == PREVIOUS
    )
    assert candidate["source_selection"]["authorization"]["month_compute"] is False


def test_canonical_candidate_rejects_wrong_previous_baseline(
    tmp_path: Path,
) -> None:
    plan, qualification, qualification_path, transition_path, output = fixture(
        tmp_path
    )
    plan["configuration"]["event_expected_hicar_commit"] = "d" * 40

    with pytest.raises(ValueError, match="preserved previous baseline"):
        MODULE.prepare(
            scientific_plan=plan,
            source_qualification=qualification,
            source_qualification_path=qualification_path,
            transition_report=json.loads(transition_path.read_text()),
            transition_report_path=transition_path,
            output_path=output,
        )


def test_canonical_candidate_does_not_replace_frozen_month_source(
    tmp_path: Path,
) -> None:
    plan, qualification, qualification_path, transition_path, output = fixture(
        tmp_path
    )
    plan["configuration"]["month_expected_hicar_commit"] = "d" * 40

    with pytest.raises(ValueError, match="already freezes a month source"):
        MODULE.prepare(
            scientific_plan=plan,
            source_qualification=qualification,
            source_qualification_path=qualification_path,
            transition_report=json.loads(transition_path.read_text()),
            transition_report_path=transition_path,
            output_path=output,
        )


def test_candidate_plan_publication_is_immutable(tmp_path: Path) -> None:
    path = tmp_path / "candidate.json"
    payload = {"status": "candidate"}
    MODULE.publish_json(path, payload)
    MODULE.publish_json(path, payload)

    with pytest.raises(ValueError, match="refusing to replace"):
        MODULE.publish_json(path, {"status": "changed"})


def test_balfrin_source_finalizer_never_activates_canonical_plan() -> None:
    script = FINALIZER.read_text()

    for token in (
        "--transition-report",
        "--transition-plan",
        "--assessment-contract",
        "--source-bundle",
        "--source-qualification",
        "No month, annual, 20-year, or 100 m compute was authorized.",
    ):
        assert token in script
    assert "mv " not in script
    assert "--execute" not in script
