from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "orchestration/seed_preemptible_forcing_cache.py"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def publish(path: Path, payload: dict | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, bytes):
        path.write_bytes(payload)
    else:
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    Path(f"{path}.ready").touch()


def test_seed_reuses_exact_payload_and_rewrites_local_paths(tmp_path: Path) -> None:
    valid_time = "2020-07-02T00:00:00"
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_file = source_root / "rea_l_hicar_20200702_0000.nc"
    publish(source_file, b"exact forcing payload")
    source_base = source_file.with_suffix("")
    source_validation = Path(f"{source_base}.validation.json")
    source_manifest = Path(f"{source_base}.manifest.json")
    publish(
        source_validation,
        {"status": "PASS", "valid_time": valid_time, "forcing_file": str(source_file)},
    )
    publish(
        source_manifest,
        {
            "status": "PASS",
            "valid_time": valid_time,
            "forcing_file": str(source_file),
            "forcing_sha256": digest(source_file),
            "forcing_size_bytes": source_file.stat().st_size,
            "validation_report": str(source_validation),
        },
    )

    source_index_path = tmp_path / "source_index.json"
    publish(
        source_index_path,
        {
            "records": [{"valid_time": valid_time, "forcing_file": str(source_file)}]
        },
    )
    target_file = target_root / source_file.name
    target_plan_path = tmp_path / "target_plan.json"
    publish(
        target_plan_path,
        {"records": [{"valid_time": valid_time, "forcing_file": str(target_file)}]},
    )
    target_index_path = tmp_path / "target_index.json"
    publish(
        target_index_path,
        {
            "records_root": str(target_root),
            "records": [
                {
                    "valid_time": valid_time,
                    "forcing_file": str(target_file),
                    "consumers": [
                        {
                            "segment_id": "target-segment",
                            "plan": str(target_plan_path),
                        }
                    ],
                }
            ],
        },
    )
    target_campaign_path = tmp_path / "target_campaign.json"
    publish(
        target_campaign_path,
        {
            "campaign_id": "target",
            "forcing_cache": {
                "index": str(target_index_path),
                "index_sha256": digest(target_index_path),
                "records_root": str(target_root),
            },
        },
    )

    evidence_plan_path = tmp_path / "evidence_plan.json"
    publish(evidence_plan_path, {"records": []})
    evidence_publication_path = tmp_path / "evidence_publication.json"
    publish(
        evidence_publication_path,
        {
            "status": "PASS",
            "plan_sha256": digest(evidence_plan_path),
            "entries": [
                {
                    "valid_time": valid_time,
                    "forcing_sha256": digest(source_file),
                    "forcing_size_bytes": source_file.stat().st_size,
                }
            ],
        },
    )
    evidence_campaign_path = tmp_path / "evidence_campaign.json"
    publish(
        evidence_campaign_path,
        {
            "campaign_id": "evidence",
            "chains": [
                {
                    "segments": [
                        {
                            "plan": str(evidence_plan_path),
                            "forcing_publication": str(evidence_publication_path),
                        }
                    ]
                }
            ],
        },
    )

    report = tmp_path / "reuse_report.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--target-campaign",
            str(target_campaign_path),
            "--evidence-campaign",
            str(evidence_campaign_path),
            "--source-index",
            str(source_index_path),
            "--output-report",
            str(report),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert target_file.stat().st_ino == source_file.stat().st_ino
    assert target_file.read_bytes() == source_file.read_bytes()
    assert Path(f"{target_file}.ready").is_file()
    target_manifest = json.loads(Path(f"{target_file.with_suffix('')}.manifest.json").read_text())
    assert target_manifest["forcing_file"] == str(target_file)
    assert target_manifest["chunk_id"] == "target-segment"
    assert target_manifest["reuse"]["method"] == "hardlink"
    report_payload = json.loads(report.read_text())
    assert report_payload["status"] == "PASS"
    assert report_payload["record_count"] == 1
