from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "streaming"
    / "prepare_scientific_baseline_transition.py"
)
SPEC = importlib.util.spec_from_file_location("baseline_transition", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


CANDIDATE = "a" * 40
PARENT = "b" * 40
EVENT = "c" * 40


def base_plan() -> dict:
    return {
        "schema_version": 1,
        "name": "pilot",
        "purpose": "test",
        "configuration": {
            "event_expected_hicar_commit": EVENT,
            "month_expected_hicar_commit": None,
            "month_required_parent_hicar_commit": EVENT,
        },
        "reference_periods": {
            "summer_event": {"start": "2020-07-01T00:00:00", "duration_hours": 72},
            "winter_event": {"start": "2020-01-15T00:00:00", "duration_hours": 72},
        },
        "promotion_criteria": {
            "event_to_month": {
                "restart_trajectory_gate": {
                    "start_exclusive": "2020-07-03T00:00:00",
                    "end_inclusive": "2020-07-04T00:00:00",
                }
            }
        },
        "stages": [
            {"id": "event-pilots", "status": "completed"},
            {"id": "month-pilot", "status": "blocked"},
            {"id": "seasonal-cycle", "status": "blocked"},
        ],
        "decision_rules": {"launch_200m_production": "old"},
    }


def source_report() -> dict:
    return {
        "schema_version": 1,
        "status": "FAIL",
        "change_scope": "OUTPUT_DIAGNOSTIC_ONLY",
        "child_commit": CANDIDATE,
        "parent_commit": PARENT,
        "parent_ancestry": {
            "status": "PASS",
            "parent_is_ancestor": True,
            "merge_base": PARENT,
        },
        "evidence": {
            "clean_target_build": {
                "status": "PASS",
                "source_tree_clean": True,
                "source_commit": CANDIDATE,
                "artifact_sha256": "1" * 64,
            },
            "representative_bridge_run": {
                "status": "PASS",
                "completion_status": "PASS",
                "source_commit": CANDIDATE,
            },
            "national_short_run": {
                "status": "FAIL",
                "completion_status": "PASS",
                "source_commit": CANDIDATE,
                "artifact_sha256": "2" * 64,
            },
            "restart_continuity": {
                "status": "PASS",
                "source_commit": CANDIDATE,
                "nonzero_runoff_observed": True,
                "compared_fields": [
                    "precipitation",
                    "runoff_surface_cumulative",
                    "runoff_subsurface_cumulative",
                    "evaporation_net_cumulative",
                ],
            },
            "preexisting_field_equivalence": {
                "status": "FAIL",
                "compared_field_count": 32,
                "mismatch_count": 23,
            },
            "solver_gate_equivalence": {
                "status": "FAIL",
                "compared_gate_count": 13,
                "mismatch_count": 1,
            },
        },
    }


def test_failed_exact_child_can_be_routed_to_transition_events() -> None:
    candidate, parent = MODULE.validate_transition_inputs(
        base_plan(),
        source_report(),
        required_candidate_commit=CANDIDATE,
        required_parent_commit=PARENT,
    )
    assert (candidate, parent) == (CANDIDATE, PARENT)

    plan = MODULE.build_candidate_plan(base_plan(), candidate, parent)

    assert plan["configuration"]["event_expected_hicar_commit"] == CANDIDATE
    assert plan["configuration"]["month_expected_hicar_commit"] is None
    assert (
        plan["configuration"]["baseline_transition"]["mode"]
        == "SCIENTIFIC_BASELINE_REQUALIFICATION_ONLY"
    )
    assert "Never from this transition plan" in plan["decision_rules"][
        "launch_200m_production"
    ]


def test_transition_refuses_a_passing_exact_child_report() -> None:
    report = source_report()
    report["status"] = "PASS"

    try:
        MODULE.validate_transition_inputs(base_plan(), report)
    except ValueError as error:
        assert "failed exact-child gate" in str(error)
    else:
        raise AssertionError("passing exact child was incorrectly rerouted")


def test_transition_requires_nonzero_cumulative_restart_evidence() -> None:
    report = source_report()
    report["evidence"]["restart_continuity"]["nonzero_runoff_observed"] = False

    try:
        MODULE.validate_transition_inputs(base_plan(), report)
    except ValueError as error:
        assert "restart continuity" in str(error)
    else:
        raise AssertionError("zero-runoff transition was incorrectly accepted")


def test_cli_publishes_transition_only_plan(tmp_path: Path, monkeypatch) -> None:
    base = tmp_path / "base.json"
    source = tmp_path / "source.json"
    bundle = tmp_path / "candidate.bundle"
    output = tmp_path / "out"
    base.write_text(json.dumps(base_plan()))
    source.write_text(json.dumps(source_report()))
    Path(f"{source}.ready").touch()
    bundle.write_bytes(b"bundle")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(MODULE_PATH),
            "--base-plan",
            str(base),
            "--source-qualification",
            str(source),
            "--source-bundle",
            str(bundle),
            "--source-bundle-published-path",
            "/store_new/project/candidate.bundle",
            "--required-candidate-commit",
            CANDIDATE,
            "--required-parent-commit",
            PARENT,
            "--output-dir",
            str(output),
        ],
    )

    assert MODULE.main() == 0
    manifest = json.loads((output / "baseline_transition_plan.json").read_text())
    candidate_plan = json.loads(
        (output / "scientific_pilot_plan_candidate.json").read_text()
    )
    assert manifest["status"] == "PLANNED"
    assert manifest["authorization"]["month_compute"] is False
    assert (
        manifest["published_inputs"]["candidate_source_bundle"]["path"]
        == "/store_new/project/candidate.bundle"
    )
    assert candidate_plan["configuration"]["event_expected_hicar_commit"] == CANDIDATE
    assert Path(f"{output / 'baseline_transition_plan.json'}.ready").is_file()
