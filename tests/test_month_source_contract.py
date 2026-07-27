from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "validation"
    / "month_source_contract.py"
)
SPEC = importlib.util.spec_from_file_location("month_source_contract", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


CHILD = "a" * 40
PARENT = "b" * 40


def passing_report() -> dict:
    return {
        "schema_version": 1,
        "status": "PASS",
        "change_scope": "OUTPUT_DIAGNOSTIC_ONLY",
        "child_commit": CHILD,
        "parent_commit": PARENT,
        "parent_ancestry": {
            "status": "PASS",
            "parent_is_ancestor": True,
            "merge_base": PARENT,
        },
        "evidence": {
            "clean_target_build": {
                "status": "PASS",
                "artifact_sha256": "1" * 64,
                "source_tree_clean": True,
                "source_commit": CHILD,
                "target": "HICAR",
            },
            "restart_continuity": {
                "status": "PASS",
                "artifact_sha256": "2" * 64,
                "source_commit": CHILD,
                "nonzero_runoff_observed": True,
                "compared_fields": [
                    "precipitation",
                    "runoff_surface_cumulative",
                    "runoff_subsurface_cumulative",
                    "evaporation_net_cumulative",
                ],
            },
            "representative_bridge_run": {
                "status": "PASS",
                "artifact_sha256": "3" * 64,
                "source_commit": CHILD,
                "completion_status": "PASS",
            },
            "national_short_run": {
                "status": "PASS",
                "artifact_sha256": "4" * 64,
                "source_commit": CHILD,
                "completion_status": "PASS",
            },
            "preexisting_field_equivalence": {
                "status": "PASS",
                "artifact_sha256": "5" * 64,
                "compared_field_count": 42,
                "mismatch_count": 0,
            },
            "solver_gate_equivalence": {
                "status": "PASS",
                "artifact_sha256": "6" * 64,
                "compared_gate_count": 8,
                "mismatch_count": 0,
            },
        },
    }


def passing_transition_report() -> dict:
    return {
        "schema_version": 1,
        "status": "PASS",
        "qualification_mode": "SCIENTIFIC_BASELINE_TRANSITION",
        "change_scope": "SCIENTIFIC_BASELINE_TRANSITION",
        "child_commit": CHILD,
        "parent_commit": PARENT,
        "previous_scientific_baseline_commit": "c" * 40,
        "evidence": {
            "baseline_transition": {
                "status": "PASS",
                "artifact_sha256": "1" * 64,
                "report_status": "PASS",
                "decision": "NOMINATE_V29_FOR_CANONICAL_MONTH_SOURCE",
                "candidate_commit": CHILD,
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
                "paired_total_runoff_kg_m2": 0.25,
            },
            "assessment_contract": {
                "status": "PASS",
                "artifact_sha256": "2" * 64,
                "contract_status": "FROZEN",
                "candidate_commit": CHILD,
                "candidate_parent_commit": PARENT,
            },
            "transition_plan": {
                "status": "PASS",
                "artifact_sha256": "3" * 64,
                "plan_status": "PLANNED",
                "candidate_commit": CHILD,
                "candidate_parent_commit": PARENT,
                "preserved_event_commit": "c" * 40,
            },
            "candidate_source_bundle": {
                "status": "PASS",
                "artifact_sha256": "4" * 64,
                "source_commit": CHILD,
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


def validate(report: dict) -> list[str]:
    return MODULE.validate_month_source_qualification(
        report,
        expected_child_commit=CHILD,
        required_parent_commit=PARENT,
    )


def test_complete_output_only_child_contract_passes() -> None:
    assert validate(passing_report()) == []


def test_zero_runoff_restart_coverage_cannot_hide_missing_observables() -> None:
    report = passing_report()
    report["evidence"]["restart_continuity"]["compared_fields"].remove(
        "runoff_surface_cumulative"
    )

    failures = validate(report)

    assert any("every cumulative water observable" in item for item in failures)


def test_source_contract_rejects_non_equivalent_preexisting_fields() -> None:
    report = passing_report()
    report["evidence"]["preexisting_field_equivalence"]["mismatch_count"] = 1

    failures = validate(report)

    assert "pre-existing field equivalence is not exact" in failures


def test_source_contract_rejects_event_commit_reuse() -> None:
    report = passing_report()
    report["child_commit"] = PARENT

    failures = MODULE.validate_month_source_qualification(
        report,
        expected_child_commit=PARENT,
        required_parent_commit=PARENT,
    )

    assert "month child commit must differ from preserved event commit" in failures


def test_complete_scientific_baseline_transition_contract_passes() -> None:
    failures = MODULE.validate_month_source_qualification(
        passing_transition_report(),
        expected_child_commit=CHILD,
        required_parent_commit=PARENT,
        qualification_mode="SCIENTIFIC_BASELINE_TRANSITION",
    )

    assert failures == []


def test_transition_mode_does_not_accept_output_only_evidence() -> None:
    failures = MODULE.validate_month_source_qualification(
        passing_report(),
        expected_child_commit=CHILD,
        required_parent_commit=PARENT,
        qualification_mode="SCIENTIFIC_BASELINE_TRANSITION",
    )

    assert failures
    assert any("SCIENTIFIC_BASELINE_TRANSITION" in failure for failure in failures)


def test_transition_mode_requires_both_events_and_water_restart_fields() -> None:
    report = passing_transition_report()
    transition = report["evidence"]["baseline_transition"]
    transition["event_names"] = ["summer"]
    transition["restart_trajectory_fields"].remove(
        "evaporation_net_cumulative"
    )

    failures = MODULE.validate_month_source_qualification(
        report,
        expected_child_commit=CHILD,
        required_parent_commit=PARENT,
        qualification_mode="SCIENTIFIC_BASELINE_TRANSITION",
    )

    assert "baseline transition does not cover summer and winter" in failures
    assert any("omits cumulative water fields" in failure for failure in failures)


def test_unknown_source_qualification_mode_fails_closed() -> None:
    failures = MODULE.validate_month_source_qualification(
        passing_report(),
        expected_child_commit=CHILD,
        required_parent_commit=PARENT,
        qualification_mode="UNKNOWN",
    )

    assert failures == ["unsupported month source qualification mode: UNKNOWN"]
