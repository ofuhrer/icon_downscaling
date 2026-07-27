from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "restore_recovery_artifact",
    ROOT / "scripts/restore_recovery_artifact.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def publish_archive_item(tmp_path: Path) -> tuple[Path, str, Path]:
    allowed = tmp_path / "store_new"
    archive_root = allowed / "recovery/v1"
    source = archive_root / "artifacts/static.nc"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"static domain")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    item_id = "static:test"
    report, data_ready, report_ready = MODULE.publication_paths(source)
    report.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "PASS",
                "archive_id": "test",
                "classification": "static",
                "item_id": item_id,
                "destination": str(source),
                "sha256": digest,
                "size_bytes": source.stat().st_size,
            }
        )
    )
    data_ready.touch()
    report_ready.touch()
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "archive_id": "test",
                "archive_root": str(archive_root),
                "files": [
                    {
                        "id": item_id,
                        "destination": "artifacts/static.nc",
                        "classification": "static",
                        "expected_sha256": digest,
                    }
                ],
            }
        )
    )
    return plan, item_id, allowed


def test_restore_is_checksum_bound_published_and_idempotent(tmp_path):
    plan, item_id, allowed = publish_archive_item(tmp_path)
    item = MODULE.resolve_item(plan, item_id, allowed)
    MODULE.validate_source(item)
    output = tmp_path / "scratch/static.nc"
    first = MODULE.restore(item, output)
    second = MODULE.restore(item, output)
    assert first == second
    assert output.read_bytes() == b"static domain"
    assert Path(f"{output}.ready").is_file()
    assert Path(f"{output}.restore.json.ready").is_file()


def test_restore_refuses_an_incomplete_existing_target(tmp_path):
    plan, item_id, allowed = publish_archive_item(tmp_path)
    item = MODULE.resolve_item(plan, item_id, allowed)
    output = tmp_path / "scratch/static.nc"
    output.parent.mkdir()
    output.write_bytes(b"partial")
    try:
        MODULE.restore(item, output)
    except ValueError as exc:
        assert "incomplete existing restore" in str(exc)
    else:
        raise AssertionError("incomplete restore was not rejected")
