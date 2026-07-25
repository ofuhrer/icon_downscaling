from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
RETIRER = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "streaming"
    / "retire_restart_boundary.py"
)


def completion(path: Path, start: str, end: str, restart: Path) -> Path:
    digest = hashlib.sha256(restart.read_bytes()).hexdigest()
    payload = {
        "status": "PASS",
        "start": start,
        "end": end,
        "restart": {
            "path": str(restart),
            "size_bytes": restart.stat().st_size,
            "sha256": digest,
        },
    }
    path.write_text(json.dumps(payload))
    Path(f"{path}.ready").touch()
    return path


def test_restart_retirement_requires_successor_and_is_dry_run_by_default(tmp_path):
    first_restart = tmp_path / "first.nc"
    next_restart = tmp_path / "next.nc"
    first_restart.write_bytes(b"first")
    next_restart.write_bytes(b"next")
    first = completion(
        tmp_path / "first.json",
        "2020-01-01T00:00:00",
        "2020-01-08T00:00:00",
        first_restart,
    )
    successor = completion(
        tmp_path / "next.json",
        "2020-01-08T00:00:00",
        "2020-01-15T00:00:00",
        next_restart,
    )
    report = tmp_path / "restart_retirement.json"

    dry_run = subprocess.run(
        [
            sys.executable,
            str(RETIRER),
            "--previous-completion",
            str(first),
            "--next-completion",
            str(successor),
            "--report",
            str(report),
        ],
        capture_output=True,
        text=True,
    )
    assert dry_run.returncode == 0, dry_run.stderr
    assert first_restart.is_file()
    assert json.loads(report.read_text())["action"] == "READY_TO_RETIRE"
    assert Path(f"{report}.ready").is_file()

    executed = subprocess.run(
        [
            sys.executable,
            str(RETIRER),
            "--previous-completion",
            str(first),
            "--next-completion",
            str(successor),
            "--report",
            str(report),
            "--execute",
        ],
        capture_output=True,
        text=True,
    )
    assert executed.returncode == 0, executed.stderr
    assert not first_restart.exists()
    assert next_restart.is_file()
    result = json.loads(report.read_text())
    assert result["status"] == "PASS"
    assert result["action"] == "RETIRED"
    assert result["execute"] is True

    repeated = subprocess.run(
        [
            sys.executable,
            str(RETIRER),
            "--previous-completion",
            str(first),
            "--next-completion",
            str(successor),
            "--report",
            str(report),
            "--execute",
        ],
        capture_output=True,
        text=True,
    )
    assert repeated.returncode == 0, repeated.stderr
    assert json.loads(repeated.stdout)["action"] == "RETIRED"
