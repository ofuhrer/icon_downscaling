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
    / "assess_scientific_baseline_transition_event.py"
)
SPEC = importlib.util.spec_from_file_location(
    "baseline_transition_event_assessment", MODULE_PATH
)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


CANDIDATE = "a" * 40


def plan() -> dict:
    return {
        "configuration": {
            "event_expected_hicar_commit": CANDIDATE,
            "output_interval_seconds": 10800,
        },
        "reference_periods": {
            "summer_event": {
                "start": "2020-07-01T00:00:00",
                "duration_hours": 72,
            },
            "winter_event": {
                "start": "2020-01-15T00:00:00",
                "duration_hours": 72,
            },
        },
        "promotion_criteria": {
            "event_to_month": {"required_status": "PASS"}
        },
    }


def scientific(passed: bool = True) -> dict:
    return {
        "complete": True,
        "decision": "PASS" if passed else "HOLD_AND_DIAGNOSE",
        "failed_screens": [] if passed else ["station_quality"],
    }


def transition(passed: bool = True) -> tuple[dict, list[str]]:
    return (
        {"status": "PASS" if passed else "FAIL"},
        [] if passed else ["summer water residual exceeds threshold"],
    )


def test_summer_pass_releases_only_matching_event_and_overlap(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        MODULE.EVENT_ASSESSOR,
        "assess_event",
        lambda *args, **kwargs: scientific(),
    )
    monkeypatch.setattr(
        MODULE.TRANSITION_ASSESSOR,
        "assess_event",
        lambda *args, **kwargs: transition(),
    )

    report = MODULE.assess_event_transition(
        event_name="summer",
        run_dir=tmp_path,
        candidate_plan=plan(),
        transition_contract={"candidate_commit": CANDIDATE},
    )

    assert report["status"] == "PASS"
    assert report["authorization"]["matching_season_event"] is True
    assert report["authorization"]["summer_restart_overlap"] is True
    assert report["authorization"]["month_compute"] is False


def test_scientific_screen_failure_holds_escalation(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        MODULE.EVENT_ASSESSOR,
        "assess_event",
        lambda *args, **kwargs: scientific(False),
    )
    monkeypatch.setattr(
        MODULE.TRANSITION_ASSESSOR,
        "assess_event",
        lambda *args, **kwargs: transition(),
    )

    report = MODULE.assess_event_transition(
        event_name="summer",
        run_dir=tmp_path,
        candidate_plan=plan(),
        transition_contract={"candidate_commit": CANDIDATE},
    )

    assert report["status"] == "FAIL"
    assert report["authorization"]["matching_season_event"] is False
    assert "scientific:station_quality" in report["failures"]


def test_water_gate_failure_holds_escalation(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        MODULE.EVENT_ASSESSOR,
        "assess_event",
        lambda *args, **kwargs: scientific(),
    )
    monkeypatch.setattr(
        MODULE.TRANSITION_ASSESSOR,
        "assess_event",
        lambda *args, **kwargs: transition(False),
    )

    report = MODULE.assess_event_transition(
        event_name="summer",
        run_dir=tmp_path,
        candidate_plan=plan(),
        transition_contract={"candidate_commit": CANDIDATE},
    )

    assert report["status"] == "FAIL"
    assert report["authorization"]["summer_restart_overlap"] is False
    assert any(item.startswith("transition:") for item in report["failures"])


def test_winter_pass_does_not_release_another_event(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        MODULE.EVENT_ASSESSOR,
        "assess_event",
        lambda *args, **kwargs: scientific(),
    )
    monkeypatch.setattr(
        MODULE.TRANSITION_ASSESSOR,
        "assess_event",
        lambda *args, **kwargs: transition(),
    )

    report = MODULE.assess_event_transition(
        event_name="winter",
        run_dir=tmp_path,
        candidate_plan=plan(),
        transition_contract={"candidate_commit": CANDIDATE},
    )

    assert report["status"] == "PASS"
    assert report["authorization"]["matching_season_event"] is False
    assert report["authorization"]["summer_restart_overlap"] is False
