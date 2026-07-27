from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
PLANNER = (
    ROOT
    / "case_studies/swiss_100m/streaming/prepare_engineering_capacity_gate.py"
)
BASE_CONFIG = (
    ROOT / "case_studies/swiss_100m/config/engineering_capacity_gate.json"
)
CHILD = "a" * 40
PARENT = "b" * 40


def publish_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload))
    Path(f"{path}.ready").touch()


def source_qualification() -> dict:
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
                "compared_field_count": 20,
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


def transition_source_qualification() -> dict:
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
                "paired_total_runoff_kg_m2": 0.5,
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


def run_planner(
    tmp_path: Path,
    decision: str,
    qualification_payload: dict | None = None,
) -> subprocess.CompletedProcess:
    static = tmp_path / "static.nc"
    static.write_bytes(b"national-100m-static")
    Path(f"{static}.ready").touch()
    digest = hashlib.sha256(static.read_bytes()).hexdigest()
    config = json.loads(BASE_CONFIG.read_text())
    config["case"]["static_sha256"] = digest
    config_path = tmp_path / "gate.json"
    config_path.write_text(json.dumps(config))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"static_sha256": digest}))
    event = tmp_path / "event.json"
    publish_json(
        event,
        {
            "assessment_status": "COMPLETE",
            "decision": decision,
            "authorization": {
                "100m_engineering_capacity_gate": decision
                == "GO_MONTH_AND_100M_CAPACITY_GATE"
            },
        },
    )
    geometry = tmp_path / "geometry.json"
    publish_json(
        geometry,
        {
            "status": "PASS",
            "static_sha256": digest,
            "minimum_mass_jacobian": {"value": 0.2},
            "minimum_interface_layer_thickness": {"value_m": 8.0},
            "minimum_mass_level_spacing": {"value_m": 8.0},
        },
    )
    qualification = tmp_path / "source_qualification.json"
    publish_json(
        qualification,
        qualification_payload or source_qualification(),
    )
    return subprocess.run(
        [
            sys.executable,
            str(PLANNER),
            "--gate-config",
            str(config_path),
            "--event-assessment",
            str(event),
            "--source-qualification",
            str(qualification),
            "--geometry-report",
            str(geometry),
            "--static-file",
            str(static),
            "--static-manifest",
            str(manifest),
            "--gate-root",
            str(tmp_path / "capacity"),
        ],
        text=True,
        capture_output=True,
    )


def test_capacity_planner_publishes_two_restart_linked_segments(tmp_path):
    result = run_planner(tmp_path, "GO_MONTH_AND_100M_CAPACITY_GATE")
    assert result.returncode == 0, result.stderr + result.stdout
    report = tmp_path / "capacity/capacity_gate_plan.json"
    payload = json.loads(report.read_text())
    assert payload["status"] == "AUTHORIZED_AND_PLANNED"
    assert payload["expected_hicar_commit"] == CHILD
    assert payload["source_qualification_mode"] == "OUTPUT_DIAGNOSTIC_ONLY"
    assert payload["required_parent_hicar_commit"] == PARENT
    assert len(payload["source_qualification_sha256"]) == 64
    assert [item["hours"] for item in payload["segments"]] == [2, 2]
    assert [item["forcing_record_count"] for item in payload["segments"]] == [3, 3]
    assert payload["segments"][1]["restart_continuation"]
    assert (
        payload["segments"][0]["shared_restart_dir"]
        == payload["segments"][1]["shared_restart_dir"]
    )
    initial_records = json.loads(
        Path(payload["segments"][0]["chunk_plan"]).read_text()
    )["records"]
    continuation_records = json.loads(
        Path(payload["segments"][1]["chunk_plan"]).read_text()
    )["records"]
    assert (
        initial_records[-1]["forcing_file"]
        == continuation_records[0]["forcing_file"]
    )
    assert Path(f"{report}.ready").is_file()


def test_capacity_planner_refuses_non_go_event_verdict(tmp_path):
    result = run_planner(tmp_path, "HOLD_AND_DIAGNOSE")
    assert result.returncode != 0
    assert "does not authorize" in result.stderr


def test_capacity_planner_accepts_passed_baseline_transition_source(tmp_path):
    result = run_planner(
        tmp_path,
        "GO_MONTH_AND_100M_CAPACITY_GATE",
        transition_source_qualification(),
    )

    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(
        (tmp_path / "capacity/capacity_gate_plan.json").read_text()
    )
    assert payload["expected_hicar_commit"] == CHILD
    assert (
        payload["source_qualification_mode"]
        == "SCIENTIFIC_BASELINE_TRANSITION"
    )
