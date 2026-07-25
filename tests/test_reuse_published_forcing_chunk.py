from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
PUBLISHER = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "streaming"
    / "reuse_published_forcing_chunk.py"
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ready(path: Path) -> None:
    Path(f"{path}.ready").touch()


def source_publication(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "source"
    forcing = root / "forcing"
    forcing.mkdir(parents=True)
    start = datetime.fromisoformat("2020-07-03T00:00:00")
    records = []
    entries = []
    for index in range(3):
        valid = start + timedelta(hours=index)
        stem = f"rea_l_hicar_{valid:%Y%m%d_%H%M}"
        data = forcing / f"{stem}.nc"
        manifest = forcing / f"{stem}.manifest.json"
        validation = forcing / f"{stem}.validation.json"
        data.write_bytes(f"forcing-{index}".encode())
        manifest.write_text(json.dumps({"status": "PASS", "index": index}))
        validation.write_text(json.dumps({"status": "PASS", "index": index}))
        # The production contract publishes a per-payload ready marker for the
        # forcing NetCDF. The ready aggregate publication certifies the hashes
        # of the record manifest and validation report.
        ready(data)
        records.append(
            {
                "index": index,
                "valid_time": valid.isoformat(),
                "cycle_date": valid.strftime("%Y%m%d"),
                "cycle_time": "0000",
                "step_hours": valid.hour,
                "forcing_file": str(data),
                "ready_marker": f"{data}.ready",
            }
        )
        entries.append(
            {
                **records[-1],
                "forcing_sha256": digest(data),
                "forcing_size_bytes": data.stat().st_size,
                "record_manifest": str(manifest),
                "record_manifest_sha256": digest(manifest),
                "validation_report": str(validation),
                "validation_report_sha256": digest(validation),
                "stage_seconds": {"total": 1},
            }
        )
    forcing_list = root / "forcing_list.txt"
    forcing_list.write_text("".join(f'"{item["forcing_file"]}"\n' for item in records))
    ready(forcing_list)
    plan = root / "chunk_plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "PLANNED",
                "chunk_id": "source",
                "chunk_root": str(root),
                "forcing_list": str(forcing_list),
                "cycle_policy": "test policy",
                "records": records,
            }
        )
    )
    ready(plan)
    publication = root / "forcing_publication.json"
    publication.write_text(
        json.dumps(
            {
                "status": "PASS",
                "plan_sha256": digest(plan),
                "forcing_list_sha256": digest(forcing_list),
                "entries": entries,
            }
        )
    )
    ready(publication)
    return plan, publication


def invoke(
    plan: Path,
    publication: Path,
    target: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(PUBLISHER),
            "--source-plan",
            str(plan),
            "--source-publication",
            str(publication),
            "--start",
            "2020-07-03T01:00:00",
            "--end",
            "2020-07-03T02:00:00",
            "--chunk-id",
            "reuse",
            "--chunk-root",
            str(target),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_reuse_publisher_creates_verified_subset_without_copying_payloads(tmp_path):
    plan, publication = source_publication(tmp_path)
    target = tmp_path / "reuse"

    completed = invoke(plan, publication, target)

    assert completed.returncode == 0, completed.stderr + completed.stdout
    reused_plan = json.loads((target / "chunk_plan.json").read_text())
    reused_publication = json.loads(
        (target / "forcing_publication.json").read_text()
    )
    assert reused_plan["record_count"] == 2
    assert (target / "forcing").is_symlink()
    assert reused_publication["status"] == "PASS"
    assert reused_publication["reuse"]["additional_fdb_retrievals"] == 0
    assert reused_publication["reuse"]["additional_fieldextra_conversions"] == 0
    assert Path(f"{target / 'forcing_publication.json'}.ready").is_file()


def test_reuse_publisher_rejects_changed_source_payload(tmp_path):
    plan, publication = source_publication(tmp_path)
    source = json.loads(plan.read_text())
    Path(source["records"][1]["forcing_file"]).write_bytes(b"changed")
    target = tmp_path / "reuse"

    completed = invoke(plan, publication, target)

    assert completed.returncode != 0
    assert not Path(f"{target / 'forcing_publication.json'}.ready").exists()


def test_reuse_publisher_requires_source_forcing_ready_marker(tmp_path):
    plan, publication = source_publication(tmp_path)
    source = json.loads(plan.read_text())
    Path(f"{source['records'][1]['forcing_file']}.ready").unlink()
    target = tmp_path / "reuse"

    completed = invoke(plan, publication, target)

    assert completed.returncode != 0
    assert "forcing_file publication is not ready" in completed.stderr
    assert not Path(f"{target / 'forcing_publication.json'}.ready").exists()
