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
