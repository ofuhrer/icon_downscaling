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
    / "prepare_scientific_annual_pilot.py"
)
CONFIG = ROOT / "case_studies" / "swiss_200m" / "config"


def publish(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    Path(f"{path}.ready").touch()


def make_inputs(tmp_path: Path, decision: str = "GO_ANNUAL_CYCLE") -> dict:
    config = tmp_path / "config"
    config.mkdir()
    scientific = config / "scientific_pilot_plan.json"
    scientific_payload = json.loads((CONFIG / scientific.name).read_text())
    scientific_payload["configuration"]["month_expected_hicar_commit"] = "a" * 40
    scientific.write_text(json.dumps(scientific_payload))

    restore = tmp_path / "restore.json"
    publish(restore, {"status": "PASS"})
    archive = json.loads(
        (CONFIG / "production_archive_contract.json").read_text()
    )
    archive["status"] = "APPROVED"
    archive["approval"] = {
        "destination": "/archive/project/hicar",
        "owner": "owner",
        "quota_bytes": 10**15,
        "measured_transfer_bytes_per_second": 10**8,
        "restore_drill_report": str(restore),
        "approved_by": "approver",
    }
    (config / "production_archive_contract.json").write_text(
        json.dumps(archive)
    )

    quality = json.loads(
        (CONFIG / "observational_validation_contract.json").read_text()
    )
    families = quality["annual_acceptance_thresholds"]["required_metrics"]
    quality["annual_acceptance_thresholds"]["status"] = "APPROVED"
    quality["annual_acceptance_thresholds"]["approval"] = {
        "application": "test",
        "metric_weights": {name: 1.0 for name in families},
        "absolute_limits": {
            name: {"limit": 1.0} for name in families
        },
        "approved_by": "approver",
        "frozen_at": "2019-01-01T00:00:00",
    }
    (config / "observational_validation_contract.json").write_text(
        json.dumps(quality)
    )

    month = tmp_path / "month_assessment.json"
    publish(
        month,
        {
            "assessment_status": "COMPLETE",
            "decision": decision,
            "authorization": {"annual_cycle": decision == "GO_ANNUAL_CYCLE"},
        },
    )

    paths = {}
    for label in ("annual", "winter", "summer"):
        static = tmp_path / f"{label}_static.nc"
        static.write_bytes(b"netcdf")
        Path(f"{static}.ready").touch()
        manifest = tmp_path / f"{label}_initialization.json"
        publish(manifest, {"status": "PASS"})
        paths[f"{label}_static"] = static
        paths[f"{label}_manifest"] = manifest

    return {
        "scientific": scientific,
        "month": month,
        **paths,
    }


def planner_command(tmp_path: Path, inputs: dict) -> list[str]:
    return [
        sys.executable,
        str(PLANNER),
        "--scientific-plan",
        str(inputs["scientific"]),
        "--month-assessment",
        str(inputs["month"]),
        "--annual-root",
        str(tmp_path / "annual"),
        "--run-root",
        str(tmp_path / "runs"),
        "--static-file",
        str(inputs["annual_static"]),
        "--land-initialization-manifest",
        str(inputs["annual_manifest"]),
        "--winter-static-file",
        str(inputs["winter_static"]),
        "--winter-initialization-manifest",
        str(inputs["winter_manifest"]),
        "--summer-static-file",
        str(inputs["summer_static"]),
        "--summer-initialization-manifest",
        str(inputs["summer_manifest"]),
        "--report",
        str(tmp_path / "annual_plan.json"),
    ]


def test_authorized_annual_plan_has_full_year_and_equivalence_contracts(tmp_path):
    inputs = make_inputs(tmp_path)

    result = subprocess.run(
        planner_command(tmp_path, inputs),
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    report = json.loads((tmp_path / "annual_plan.json").read_text())
    assert report["status"] == "PLANNED"
    assert report["expected_hicar_commit"] == json.loads(
        inputs["scientific"].read_text()
    )["configuration"]["month_expected_hicar_commit"]
    assert len(report["segments"]) == 53
    assert report["expected_unique_output_records"] == 2929
    assert sum(
        item["expected_output_records"] for item in report["segments"]
    ) == 2929
    assert {
        item["season"] for item in report["restart_trajectory_reports"]
    } == {"DJF", "MAM", "JJA", "SON"}
    assert len(report["restart_trajectory_reports"]) == 4
    assert {
        item["season"] for item in report["initialization_equivalence_reports"]
    } == {"DJF", "JJA"}
    assert all(
        item["overlap"]["declared_spinup_days"] == 7
        and item["overlap"]["retained_days"] == 21
        for item in report["initialization_equivalence_reports"]
    )
    assert report["validation_sources"]["forcing_record_count"] == 8785
    assert report["validation_sources"]["expected_reference_record_count"] == 2929
    assert Path(f"{tmp_path / 'annual_plan.json'}.ready").is_file()


def test_non_go_month_assessment_cannot_publish_annual_plan(tmp_path):
    inputs = make_inputs(tmp_path, decision="HOLD_AND_DIAGNOSE")

    result = subprocess.run(
        planner_command(tmp_path, inputs),
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "annual pilot is not authorized" in result.stderr
    assert not (tmp_path / "annual_plan.json").exists()


def test_balfrin_wrapper_is_planning_only_and_requires_publications():
    wrapper = (
        ROOT
        / "case_studies"
        / "swiss_200m"
        / "scripts"
        / "prepare_scientific_annual_pilot_balfrin.sbatch"
    ).read_text()

    assert "#SBATCH --partition=pp-short" in wrapper
    assert "sbatch " not in wrapper
    assert '"$month_assessment.ready"' in wrapper
    assert '"$annual_static.ready"' in wrapper
    assert "no compute jobs were submitted" in wrapper
