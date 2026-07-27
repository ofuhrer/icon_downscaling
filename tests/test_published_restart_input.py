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
    / "validate_published_restart_input.py"
)
SPEC = importlib.util.spec_from_file_location("published_restart_input", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


COMMIT = "a" * 40
TIME = "2020-07-03T00:00:00"


def model_completion(restart: Path) -> dict:
    return {
        "status": "PASS",
        "end": TIME,
        "provenance": {"source_commit": COMMIT},
        "restart": {
            "path": str(restart),
            "sha256": MODULE.sha256(restart),
        },
    }


def checkpoint(restart: Path) -> dict:
    return {
        "status": "PASS",
        "checkpoint": str(restart),
        "checkpoint_time": TIME,
        "expected_time": TIME,
        "expected_source_commit": COMMIT,
        "encoded_time_offset_seconds": 0.432,
        "sha256": MODULE.sha256(restart),
    }


def validate(report: dict, restart: Path) -> list[str]:
    return MODULE.validate_restart_input(
        report,
        restart_file=restart,
        expected_time=TIME,
        expected_source_commit=COMMIT,
    )


def test_model_completion_boundary_is_accepted(tmp_path: Path) -> None:
    restart = tmp_path / "restart.nc"
    restart.write_bytes(b"restart")

    assert validate(model_completion(restart), restart) == []


def test_intermediate_checkpoint_inventory_is_accepted(tmp_path: Path) -> None:
    restart = tmp_path / "restart.nc"
    restart.write_bytes(b"restart")

    assert validate(checkpoint(restart), restart) == []


def test_checkpoint_wrong_source_is_rejected(tmp_path: Path) -> None:
    restart = tmp_path / "restart.nc"
    restart.write_bytes(b"restart")
    report = checkpoint(restart)
    report["expected_source_commit"] = "b" * 40

    assert "restart input publication has the wrong source commit" in validate(
        report, restart
    )


def test_checkpoint_payload_mutation_is_rejected(tmp_path: Path) -> None:
    restart = tmp_path / "restart.nc"
    restart.write_bytes(b"restart")
    report = checkpoint(restart)
    restart.write_bytes(b"changed")

    assert "restart input checksum disagrees with its publication" in validate(
        report, restart
    )
