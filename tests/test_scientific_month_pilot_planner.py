from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
PLANNER = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "streaming"
    / "prepare_scientific_month_pilot.py"
)
SCIENTIFIC_PLAN = (
    ROOT / "case_studies" / "swiss_200m" / "config" / "scientific_pilot_plan.json"
)


def publish(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    Path(f"{path}.ready").touch()


def run_planner(tmp_path: Path, decision: str):
    assessment = tmp_path / "event_assessment.json"
    publish(
        assessment,
        {
            "assessment_status": "COMPLETE",
            "decision": decision,
            "authorization": {
                "month_pilot": decision == "GO_MONTH_AND_100M_CAPACITY_GATE"
            },
        },
    )
    static_file = tmp_path / "initialized_static.nc"
    static_file.touch()
    Path(f"{static_file}.ready").touch()
    initialization = tmp_path / "land_initialization.json"
    publish(initialization, {"status": "PASS"})
    month_root = tmp_path / "month_stream"
    run_root = tmp_path / "month_runs"
    result = subprocess.run(
        [
            sys.executable,
            str(PLANNER),
            "--scientific-plan",
            str(SCIENTIFIC_PLAN),
            "--event-assessment",
            str(assessment),
            "--month-root",
            str(month_root),
            "--run-root",
            str(run_root),
            "--static-file",
            str(static_file),
            "--land-initialization-manifest",
            str(initialization),
        ],
        text=True,
        capture_output=True,
    )
    return result, month_root


def test_authorized_month_has_five_segments_and_restart_overlap(tmp_path):
    result, month_root = run_planner(tmp_path, "GO_MONTH_AND_100M_CAPACITY_GATE")

    assert result.returncode == 0, result.stderr + result.stdout
    report_path = month_root / "month_pilot_plan.json"
    report = json.loads(report_path.read_text())
    assert report["status"] == "PLANNED"
    assert report["expected_hicar_commit"] == json.loads(
        SCIENTIFIC_PLAN.read_text()
    )["configuration"]["month_expected_hicar_commit"]
    assert report["expected_hicar_commit"] is None
    assert report["required_parent_hicar_commit"] == json.loads(
        SCIENTIFIC_PLAN.read_text()
    )["configuration"]["event_expected_hicar_commit"]
    assert report["source_qualification_report"].endswith(
        "validation/month_source_qualification.json"
    )
    assert report["source_qualification_sha256"] is None
    assert report["declared_spinup_days"] == 7
    assert report["retained_days"] == 24
    assert report["expected_unique_output_records"] == 249
    assert report["validation_reports"]["month_assessment"].endswith(
        "scientific_month_assessment.json"
    )
    assert report["validation_reports"]["drift_screen"].endswith(
        "postspinup_drift_screen.json"
    )
    assert report["archive_contract"].endswith(
        "config/production_archive_contract.json"
    )
    assert report["observational_validation_contract"].endswith(
        "config/observational_validation_contract.json"
    )
    assert len(report["segments"]) == 5
    assert [segment["hours"] for segment in report["segments"]] == [
        168,
        168,
        168,
        168,
        72,
    ]
    assert [segment["forcing_record_count"] for segment in report["segments"]] == [
        169,
        169,
        169,
        169,
        73,
    ]
    assert report["segments"][0]["rea_l_land_initialization"] is True
    assert all(
        segment["restart_from"] == segment["start"]
        for segment in report["segments"][1:]
    )
    overlap = report["uninterrupted_restart_overlap"]
    assert overlap["hours"] == 192
    assert overlap["comparison_start"] == "2020-07-15T00:00:00"
    assert overlap["comparison_end"] == "2020-07-16T00:00:00"
    validation = report["validation_sources"]
    assert validation["forcing_record_count"] == 745
    assert validation["expected_reference_record_count"] == 249
    assert Path(validation["chunk_plan"]).is_file()
    assert Path(f"{report_path}.ready").is_file()
    for segment in report["segments"]:
        assert Path(segment["chunk_plan"]).is_file()
        assert Path(f"{segment['chunk_plan']}.ready").is_file()
        assert Path(segment["forcing_list"]).is_file()
        assert Path(f"{segment['forcing_list']}.ready").is_file()
        assert Path(segment["output_file_list"]).read_text() == (
            f"{segment['expected_output_file']}\n"
        )
        assert Path(f"{segment['output_file_list']}.ready").is_file()
        assert segment["model_completion_report"].endswith(
            "model_chunk_completion.json"
        )
        assert segment["solver_report"].endswith(
            "scientific_validation/solver_log_diagnostics.json"
        )
        assert segment["compression_report"].endswith(".compression.json")


def test_nonpassing_event_verdict_cannot_publish_month_plan(tmp_path):
    result, month_root = run_planner(tmp_path, "HOLD_AND_DIAGNOSE")

    assert result.returncode != 0
    assert "month pilot is not authorized" in result.stderr
    assert not (month_root / "month_pilot_plan.json").exists()
