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
    / "retire_wind_source_output.py"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def publish(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload))
    Path(f"{path}.ready").touch()


def prepare_publications(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    source = tmp_path / "raw.nc"
    source.write_bytes(b"raw-fixed-height-wind")
    Path(f"{source}.ready").touch()
    reduced = tmp_path / "reduced.nc"
    reduced.write_bytes(b"compact-wind-statistics")
    Path(f"{reduced}.ready").touch()
    model_report = tmp_path / "model.json"
    reduction_report = tmp_path / "reduction.json"
    publish(
        model_report,
        {
            "status": "PASS",
            "chunk_id": "wind-001",
            "start": "2020-01-01T00:00:00",
            "output_profile": "wind_climatology",
            "output": {
                "files": [
                    {
                        "path": str(source),
                        "size_bytes": source.stat().st_size,
                        "sha256": sha256(source),
                    }
                ]
            },
        },
    )
    publish(
        reduction_report,
        {
            "status": "PASS",
            "interval_start": "2020-01-01T00:00:00",
            "inputs": [
                {
                    "path": str(source),
                    "size_bytes": source.stat().st_size,
                    "sha256": sha256(source),
                }
            ],
            "output": str(reduced),
            "output_sha256": sha256(reduced),
        },
    )
    return source, reduced, model_report, reduction_report


def test_wind_retirement_is_dry_run_by_default(tmp_path):
    source, reduced, model_report, reduction_report = prepare_publications(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            str(RETIRER),
            "--model-completion",
            str(model_report),
            "--wind-reduction",
            str(reduction_report),
        ],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "READY_TO_RETIRE"
    assert source.is_file()
    assert reduced.is_file()


def test_wind_retirement_deletes_only_verified_raw_sources(tmp_path):
    source, reduced, model_report, reduction_report = prepare_publications(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            str(RETIRER),
            "--model-completion",
            str(model_report),
            "--wind-reduction",
            str(reduction_report),
            "--execute",
        ],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert not source.exists()
    assert not Path(f"{source}.ready").exists()
    assert reduced.is_file()
    assert Path(f"{reduced}.ready").is_file()


def test_wind_retirement_rejects_changed_raw_source(tmp_path):
    source, _, model_report, reduction_report = prepare_publications(tmp_path)
    source.write_bytes(b"changed")
    result = subprocess.run(
        [
            sys.executable,
            str(RETIRER),
            "--model-completion",
            str(model_report),
            "--wind-reduction",
            str(reduction_report),
            "--execute",
        ],
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert source.is_file()
    assert "size changed" in result.stderr
