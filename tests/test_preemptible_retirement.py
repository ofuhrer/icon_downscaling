from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "orchestration/retire_campaign_artifacts.py"
SPEC = importlib.util.spec_from_file_location(
    "retire_campaign_artifacts", MODULE_PATH
)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def publish(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    Path(f"{path}.ready").touch()


def segment_task(tmp_path: Path) -> tuple[dict, dict[str, Path]]:
    campaign = tmp_path / "campaign"
    chunk = campaign / "segment/forcing"
    chunk.mkdir(parents=True)
    forcing = chunk / "forcing.nc"
    forcing.write_bytes(b"forcing")
    Path(f"{forcing}.ready").touch()
    plan = chunk / "chunk_plan.json"
    plan.write_text(
        json.dumps(
            {
                "chunk_root": str(chunk),
                "records": [{"forcing_file": str(forcing)}],
            }
        )
    )
    forcing_publication = chunk / "forcing_publication.json"
    publish(
        forcing_publication,
        {
            "status": "PASS",
            "plan_sha256": digest(plan),
            "entries": [
                {
                    "forcing_file": str(forcing.resolve()),
                    "forcing_sha256": digest(forcing),
                }
            ],
        },
    )

    run = campaign / "segment/attempts/a002/run"
    run.mkdir(parents=True)
    source = run / "output.nc"
    source.write_bytes(b"raw model output")
    restart = run / "restart.nc"
    restart.write_bytes(b"restart")
    completion = run / "model_chunk_completion.json"
    publish(
        completion,
        {
            "status": "PASS",
            "plan_sha256": digest(plan),
            "start": "2020-01-01T00:00:00",
            "end": "2020-01-02T00:00:00",
            "output": {
                "files": [
                    {
                        "path": str(source.resolve()),
                        "sha256": digest(source),
                    }
                ]
            },
            "restart": {
                "path": str(restart.resolve()),
                "sha256": digest(restart),
            },
        },
    )
    target = campaign / "segment/compressed/a002/output.nc"
    target.parent.mkdir(parents=True)
    target.write_bytes(source.read_bytes())
    Path(f"{target}.ready").touch()
    compression = Path(f"{target}.compression.json")
    publish(
        compression,
        {
            "status": "PASS",
            "source": str(source.resolve()),
            "source_sha256": digest(source),
            "target": str(target.resolve()),
            "target_sha256": digest(target),
        },
    )
    obsolete = campaign / "segment/attempts/a001"
    obsolete.mkdir(parents=True)
    (obsolete / "partial.nc").write_bytes(b"partial")
    report = campaign / "segment/lifecycle/segment_retirement.json"
    task = {
        "kind": "segment_retirement",
        "task_id": "chain:segment:segment-retirement",
        "chain_id": "chain",
        "segment_id": "segment",
        "segment_index": 0,
        "campaign_root": str(campaign),
        "plan": str(plan),
        "forcing_publication": str(forcing_publication),
        "model_completion": str(completion),
        "successful_attempt_id": "a002",
        "compressions": [
            {
                "source": str(source),
                "target": str(target),
                "report": str(compression),
            }
        ],
        "obsolete_attempt_dirs": [str(obsolete)],
        "report": str(report),
    }
    return task, {
        "forcing": forcing,
        "source": source,
        "restart": restart,
        "target": target,
        "obsolete": obsolete,
        "report": report,
        "forcing_publication": forcing_publication,
    }


def test_segment_retirement_resumes_from_journal_after_partial_deletion(tmp_path):
    task, paths = segment_task(tmp_path)
    journal = MODULE.segment_journal(task)
    MODULE.write_json_atomic(paths["report"], journal)

    paths["source"].unlink()
    Path(f"{paths['forcing']}.ready").unlink()
    paths["forcing"].unlink()

    result = MODULE.execute_task(task)
    assert result["status"] == "PASS"
    assert result["action"] == "RETIRED"
    assert Path(f"{paths['report']}.ready").is_file()
    assert not paths["obsolete"].exists()
    assert not Path(f"{paths['forcing_publication']}.ready").exists()
    assert paths["target"].is_file()
    assert paths["restart"].is_file()


def test_restart_retirement_resumes_after_restart_was_deleted(tmp_path):
    task, paths = segment_task(tmp_path)
    next_run = tmp_path / "campaign/next/run"
    next_run.mkdir(parents=True)
    next_restart = next_run / "restart.nc"
    next_restart.write_bytes(b"next restart")
    next_completion = next_run / "model_chunk_completion.json"
    publish(
        next_completion,
        {
            "status": "PASS",
            "start": "2020-01-02T00:00:00",
            "end": "2020-01-03T00:00:00",
            "restart": {
                "path": str(next_restart.resolve()),
                "sha256": digest(next_restart),
            },
        },
    )
    report = tmp_path / "campaign/segment/lifecycle/restart_retirement.json"
    restart_task = {
        "kind": "restart_retirement",
        "task_id": "chain:segment:restart-retirement",
        "chain_id": "chain",
        "segment_id": "segment",
        "segment_index": 0,
        "campaign_root": str(tmp_path / "campaign"),
        "previous_completion": task["model_completion"],
        "next_completion": str(next_completion),
        "preserve": False,
        "report": str(report),
    }
    journal = MODULE.restart_journal(restart_task)
    MODULE.write_json_atomic(report, journal)
    paths["restart"].unlink()

    result = MODULE.execute_task(restart_task)
    assert result["status"] == "PASS"
    assert result["action"] == "RETIRED"
    assert Path(f"{report}.ready").is_file()
    assert next_restart.is_file()


def test_retirement_rejects_targets_outside_campaign_root(tmp_path):
    task, _paths = segment_task(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    task["obsolete_attempt_dirs"] = [str(outside)]
    with pytest.raises(ValueError, match="outside the campaign root"):
        MODULE.segment_journal(task)
