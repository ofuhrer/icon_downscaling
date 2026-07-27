from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestration"))
SPEC = importlib.util.spec_from_file_location(
    "qualify_hicar_preemptible_recovery",
    ROOT / "orchestration/qualify_hicar_preemptible_recovery.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def publish(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    Path(f"{path}.ready").touch()


def campaign(tmp_path: Path) -> tuple[Path, dict]:
    payload = {
        "purpose": "qualification",
        "model": {
            "partition": "preemptible",
            "nodes": 4,
            "output_profile": "routine",
        },
        "policy": {"max_model_attempts": 0},
        "chains": [
            {
                "chain_id": "smoke",
                "segments": [
                    {
                        "start": "2020-01-01T00:00:00",
                        "end": "2020-01-01T01:00:00",
                        "rea_l_land_initialization": True,
                    },
                    {
                        "start": "2020-01-01T01:00:00",
                        "end": "2020-01-01T02:00:00",
                        "rea_l_land_initialization": False,
                    },
                ],
            }
        ],
        "controller": {"state": str(tmp_path / "state.json")},
    }
    path = tmp_path / "campaign.json"
    publish(path, payload)
    return path, payload


def test_recovery_qualification_requires_fresh_adjacent_two_segment_campaign(
    tmp_path,
):
    path, payload = campaign(tmp_path)
    assert MODULE.validate_campaign(path) == payload
    payload["chains"][0]["segments"][1]["start"] = "2020-01-01T03:00:00"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="not adjacent"):
        MODULE.validate_campaign(path)


def test_recovery_qualification_binds_successor_to_predecessor_restart(tmp_path):
    restart = tmp_path / "restart.nc"
    restart.write_bytes(b"checkpoint")
    predecessor = tmp_path / "first.json"
    publish(
        predecessor,
        {
            "status": "PASS",
            "restart": {
                "path": str(restart.resolve()),
                "sha256": MODULE.controller.sha256(restart),
            },
        },
    )
    successor = tmp_path / "second.json"
    publish(
        successor,
        {
            "status": "PASS",
            "restart_input": {
                "path": str(restart.resolve()),
                "sha256": MODULE.controller.sha256(restart),
                "publication": str(predecessor.resolve()),
                "publication_sha256": MODULE.controller.sha256(predecessor),
            },
        },
    )
    evidence = MODULE.matching_restart_evidence(predecessor, successor)
    assert evidence["sha256"] == MODULE.controller.sha256(restart)


def test_recovery_qualification_rejects_another_predecessor(tmp_path):
    predecessor = tmp_path / "first.json"
    publish(
        predecessor,
        {
            "status": "PASS",
            "restart": {"path": "/campaign/restart.nc", "sha256": "a" * 64},
        },
    )
    successor = tmp_path / "second.json"
    publish(
        successor,
        {
            "status": "PASS",
            "restart_input": {
                "path": "/campaign/other.nc",
                "sha256": "b" * 64,
                "publication": str(predecessor.resolve()),
                "publication_sha256": MODULE.controller.sha256(predecessor),
            },
        },
    )
    with pytest.raises(ValueError, match="not bound"):
        MODULE.matching_restart_evidence(predecessor, successor)


def test_model_started_accepts_the_real_srun_step(tmp_path, monkeypatch):
    attempt = {
        "job_id": "1234",
        "run_dir": str(tmp_path),
    }

    class Result:
        stdout = "1234.0\n1234.batch\n"

    monkeypatch.setattr(MODULE.subprocess, "run", lambda *args, **kwargs: Result())
    assert MODULE.model_started(attempt)
