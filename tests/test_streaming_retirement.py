from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
RETIRE = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "streaming"
    / "retire_forcing_chunk.py"
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_retirement_is_dry_by_default_and_guarded_by_publications(tmp_path):
    chunk = tmp_path / "chunk"
    forcing_dir = chunk / "forcing"
    cache = chunk / "cache" / "20200101"
    forcing_dir.mkdir(parents=True)
    cache.mkdir(parents=True)
    forcing_file = forcing_dir / "forcing.nc"
    forcing_file.write_bytes(b"forcing-payload")
    Path(f"{forcing_file}.ready").touch()
    (cache / "static.grib").write_bytes(b"cache")

    plan = chunk / "chunk_plan.json"
    plan.write_text(
        json.dumps(
            {
                "chunk_id": "test",
                "chunk_root": str(chunk),
                "records": [{"forcing_file": str(forcing_file)}],
            }
        )
    )
    publication = chunk / "forcing_publication.json"
    publication.write_text(
        json.dumps(
            {
                "status": "PASS",
                "chunk_id": "test",
                "plan_sha256": digest(plan),
                "entries": [
                    {
                        "forcing_file": str(forcing_file),
                        "forcing_sha256": digest(forcing_file),
                    }
                ],
            }
        )
    )
    Path(f"{publication}.ready").touch()
    completion = chunk / "model_completion.json"
    completion.write_text(json.dumps({"status": "PASS", "chunk_id": "test"}))
    Path(f"{completion}.ready").touch()
    report = chunk / "forcing_retirement.json"

    command = [
        sys.executable,
        str(RETIRE),
        "--plan",
        str(plan),
        "--forcing-publication",
        str(publication),
        "--model-completion",
        str(completion),
        "--report",
        str(report),
    ]
    dry = subprocess.run(command, text=True, capture_output=True)
    assert dry.returncode == 0, dry.stderr
    assert forcing_file.is_file()
    assert (cache / "static.grib").is_file()
    dry_report = json.loads(report.read_text())
    assert dry_report["status"] == "PASS"
    assert dry_report["action"] == "READY_TO_RETIRE"
    assert dry_report["payload_count"] == 1
    assert Path(f"{report}.ready").is_file()

    execute = subprocess.run([*command, "--execute"], text=True, capture_output=True)
    assert execute.returncode == 0, execute.stderr
    assert not forcing_file.exists()
    assert not Path(f"{forcing_file}.ready").exists()
    assert not cache.exists()
    assert publication.is_file()
    assert not Path(f"{publication}.ready").exists()
    assert completion.is_file()
    executed_report = json.loads(report.read_text())
    assert executed_report["status"] == "PASS"
    assert executed_report["action"] == "RETIRED"
    assert executed_report["execute"] is True
    assert executed_report["forcing_publication_ready_withdrawn"] is True

    repeated = subprocess.run([*command, "--execute"], text=True, capture_output=True)
    assert repeated.returncode == 0, repeated.stderr
    assert json.loads(repeated.stdout)["action"] == "RETIRED"


def test_execute_with_provenance_rejects_a_different_forcing_publication(tmp_path):
    chunk = tmp_path / "chunk"
    forcing_dir = chunk / "forcing"
    forcing_dir.mkdir(parents=True)
    forcing_file = forcing_dir / "forcing.nc"
    forcing_file.write_bytes(b"forcing-payload")
    Path(f"{forcing_file}.ready").touch()
    plan = chunk / "chunk_plan.json"
    plan.write_text(
        json.dumps(
            {
                "chunk_id": "test",
                "chunk_root": str(chunk),
                "records": [{"forcing_file": str(forcing_file)}],
            }
        )
    )
    publication = chunk / "forcing_publication.json"
    publication.write_text(
        json.dumps(
            {
                "status": "PASS",
                "chunk_id": "test",
                "plan_sha256": digest(plan),
                "entries": [
                    {
                        "forcing_file": str(forcing_file),
                        "forcing_sha256": digest(forcing_file),
                    }
                ],
            }
        )
    )
    Path(f"{publication}.ready").touch()
    completion = chunk / "model_completion.json"
    completion.write_text(
        json.dumps(
            {
                "status": "PASS",
                "chunk_id": "test",
                "provenance": {
                    "status": "PASS",
                    "plan_sha256": digest(plan),
                    "forcing_publication_sha256": "0" * 64,
                },
            }
        )
    )
    Path(f"{completion}.ready").touch()

    result = subprocess.run(
        [
            sys.executable,
            str(RETIRE),
            "--plan",
            str(plan),
            "--forcing-publication",
            str(publication),
            "--model-completion",
            str(completion),
            "--execute",
        ],
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "model provenance does not identify" in result.stderr
    assert forcing_file.is_file()
    assert Path(f"{forcing_file}.ready").is_file()
