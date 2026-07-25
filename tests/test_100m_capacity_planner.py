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


def publish_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload))
    Path(f"{path}.ready").touch()


def run_planner(tmp_path: Path, decision: str) -> subprocess.CompletedProcess:
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
    return subprocess.run(
        [
            sys.executable,
            str(PLANNER),
            "--gate-config",
            str(config_path),
            "--event-assessment",
            str(event),
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
    assert payload["expected_hicar_commit"] == json.loads(
        BASE_CONFIG.read_text()
    )["case"]["expected_hicar_commit"]
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
