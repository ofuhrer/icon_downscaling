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
    / "retire_wind_interval_statistics.py"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_publication(tmp_path: Path) -> tuple[list[Path], Path, Path]:
    intervals = []
    entries = []
    for index in range(2):
        interval = tmp_path / f"interval-{index}.nc"
        interval.write_bytes(f"interval-{index}".encode())
        Path(f"{interval}.ready").touch()
        intervals.append(interval)
        entries.append(
            {
                "path": str(interval),
                "size_bytes": interval.stat().st_size,
                "sha256": sha256(interval),
            }
        )
    merged = tmp_path / "monthly.nc"
    merged.write_bytes(b"merged-month")
    Path(f"{merged}.ready").touch()
    report = tmp_path / "monthly.json"
    report.write_text(
        json.dumps(
            {
                "status": "PASS",
                "inputs": entries,
                "output": str(merged),
                "output_sha256": sha256(merged),
            }
        )
    )
    Path(f"{report}.ready").touch()
    return intervals, merged, report


def test_interval_retirement_is_dry_run_by_default(tmp_path):
    intervals, merged, report = prepare_publication(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            str(RETIRER),
            "--merged-publication",
            str(report),
        ],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "READY_TO_RETIRE"
    assert all(path.is_file() for path in intervals)
    assert merged.is_file()


def test_interval_retirement_removes_only_verified_inputs(tmp_path):
    intervals, merged, report = prepare_publication(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            str(RETIRER),
            "--merged-publication",
            str(report),
            "--execute",
        ],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    for path in intervals:
        assert not path.exists()
        assert not Path(f"{path}.ready").exists()
    assert merged.is_file()
    assert Path(f"{merged}.ready").is_file()


def test_interval_retirement_rejects_changed_input(tmp_path):
    intervals, merged, report = prepare_publication(tmp_path)
    intervals[0].write_bytes(b"changed")
    result = subprocess.run(
        [
            sys.executable,
            str(RETIRER),
            "--merged-publication",
            str(report),
            "--execute",
        ],
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert all(path.is_file() for path in intervals)
    assert merged.is_file()
    assert "size changed" in result.stderr
