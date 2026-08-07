#!/usr/bin/env python3
"""Seed a new campaign cache from exact forcing bytes used by an evidence campaign."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_published_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file() or not Path(f"{path}.ready").is_file():
        raise ValueError(f"{label} is not published: {path}")
    return json.loads(path.read_text())


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def publish_ready(path: Path, content: str = "") -> None:
    marker = Path(f"{path}.ready")
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=marker.parent, prefix=f".{marker.name}.", delete=False
    ) as stream:
        stream.write(content)
        temporary = Path(stream.name)
    os.replace(temporary, marker)


def collect_expected_entries(campaign: dict[str, Any]) -> tuple[dict[str, dict], list[dict]]:
    expected: dict[str, dict] = {}
    publications = []
    for chain in campaign["chains"]:
        for segment in chain["segments"]:
            publication_path = Path(segment["forcing_publication"])
            publication = load_published_json(publication_path, "evidence forcing publication")
            if publication.get("status") != "PASS":
                raise ValueError(f"evidence forcing publication is not PASS: {publication_path}")
            plan_path = Path(segment["plan"])
            if publication.get("plan_sha256") != sha256(plan_path):
                raise ValueError(f"evidence forcing publication changed: {publication_path}")
            publications.append(
                {
                    "path": str(publication_path.resolve()),
                    "sha256": sha256(publication_path),
                    "entries": len(publication.get("entries", [])),
                }
            )
            for entry in publication.get("entries", []):
                valid_time = str(entry["valid_time"])
                identity = {
                    "forcing_sha256": entry["forcing_sha256"],
                    "forcing_size_bytes": int(entry["forcing_size_bytes"]),
                    "valid_time": valid_time,
                }
                previous = expected.get(valid_time)
                if previous is not None and any(
                    previous[key] != identity[key] for key in identity
                ):
                    raise ValueError(f"conflicting evidence forcing identity: {valid_time}")
                expected[valid_time] = identity
    return expected, publications


def record_index_in_plan(consumer: dict[str, Any], valid_time: str) -> int:
    plan = load_published_json(Path(consumer["plan"]), "target segment plan")
    matches = [
        index
        for index, record in enumerate(plan["records"])
        if record["valid_time"] == valid_time
    ]
    if len(matches) != 1:
        raise ValueError(f"target plan does not identify {valid_time} exactly once")
    return matches[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-campaign", required=True, type=Path)
    parser.add_argument("--evidence-campaign", required=True, type=Path)
    parser.add_argument("--source-index", required=True, type=Path)
    parser.add_argument("--output-report", required=True, type=Path)
    args = parser.parse_args()

    target_campaign = load_published_json(args.target_campaign, "target campaign")
    evidence_campaign = load_published_json(args.evidence_campaign, "evidence campaign")
    source_index = load_published_json(args.source_index, "source forcing index")
    target_index_path = Path(target_campaign["forcing_cache"]["index"])
    target_index = load_published_json(target_index_path, "target forcing index")
    if sha256(target_index_path) != target_campaign["forcing_cache"]["index_sha256"]:
        raise ValueError("target forcing index changed")
    if target_campaign["campaign_id"] == evidence_campaign["campaign_id"]:
        raise ValueError("target and evidence campaigns must differ")

    target_root = Path(target_index["records_root"]).resolve()
    declared_target_root = Path(target_campaign["forcing_cache"]["records_root"]).resolve()
    if target_root != declared_target_root:
        raise ValueError("target forcing index records root differs from campaign")
    target_root.mkdir(parents=True, exist_ok=True)
    if any(target_root.iterdir()):
        raise ValueError(f"target forcing cache is not empty: {target_root}")

    expected, evidence_publications = collect_expected_entries(evidence_campaign)
    target_records = {record["valid_time"]: record for record in target_index["records"]}
    source_records = {record["valid_time"]: record for record in source_index["records"]}
    if set(target_records) != set(expected):
        missing = sorted(set(target_records) - set(expected))
        extra = sorted(set(expected) - set(target_records))
        raise ValueError(f"evidence valid times differ; missing={missing}, extra={extra}")
    if not set(target_records).issubset(source_records):
        raise ValueError("source forcing index lacks target valid times")

    staged = []
    ready_targets = []
    for valid_time in sorted(target_records):
        target_record = target_records[valid_time]
        source_record = source_records[valid_time]
        source_file = Path(source_record["forcing_file"]).resolve()
        target_file = Path(target_record["forcing_file"]).resolve()
        if target_file.parent != target_root:
            raise ValueError(f"target forcing record escapes records root: {target_file}")
        if not source_file.is_file() or not Path(f"{source_file}.ready").is_file():
            raise ValueError(f"source forcing record is not published: {source_file}")
        if source_file.stat().st_dev != target_root.stat().st_dev:
            raise ValueError("source and target forcing caches are on different filesystems")

        source_base = Path(str(source_file)[:-3]) if str(source_file).endswith(".nc") else source_file
        source_manifest_path = Path(f"{source_base}.manifest.json")
        source_validation_path = Path(f"{source_base}.validation.json")
        source_manifest = json.loads(source_manifest_path.read_text())
        source_validation = json.loads(source_validation_path.read_text())
        identity = expected[valid_time]
        source_digest = sha256(source_file)
        if (
            source_manifest.get("status") != "PASS"
            or source_validation.get("status") != "PASS"
            or source_manifest.get("valid_time") != valid_time
            or source_validation.get("valid_time") != valid_time
            or source_manifest.get("forcing_sha256") != identity["forcing_sha256"]
            or source_digest != identity["forcing_sha256"]
            or source_file.stat().st_size != identity["forcing_size_bytes"]
        ):
            raise ValueError(f"source forcing record fails evidence identity: {valid_time}")

        target_base = Path(str(target_file)[:-3]) if str(target_file).endswith(".nc") else target_file
        target_manifest_path = Path(f"{target_base}.manifest.json")
        target_validation_path = Path(f"{target_base}.validation.json")
        for path in (
            target_file,
            Path(f"{target_file}.ready"),
            target_manifest_path,
            target_validation_path,
        ):
            if path.exists() or path.is_symlink():
                raise ValueError(f"refusing to overwrite target forcing artifact: {path}")

        temporary = target_root / f".{target_file.name}.{os.getpid()}.hardlink"
        os.link(source_file, temporary)
        os.replace(temporary, target_file)
        if sha256(target_file) != source_digest or target_file.stat().st_ino != source_file.stat().st_ino:
            raise ValueError(f"hardlinked forcing payload differs: {valid_time}")

        consumer = target_record["consumers"][0]
        target_validation = dict(source_validation)
        target_validation["forcing_file"] = str(target_file)
        target_manifest = dict(source_manifest)
        target_manifest.update(
            {
                "chunk_id": consumer["segment_id"],
                "forcing_file": str(target_file),
                "record_index": record_index_in_plan(consumer, valid_time),
                "validation_report": str(target_validation_path),
                "reuse": {
                    "source_forcing_file": str(source_file),
                    "source_forcing_sha256": source_digest,
                    "source_forcing_index": str(args.source_index.resolve()),
                    "source_forcing_index_sha256": sha256(args.source_index),
                    "evidence_campaign": str(args.evidence_campaign.resolve()),
                    "evidence_campaign_sha256": sha256(args.evidence_campaign),
                    "method": "hardlink",
                },
                "stage_seconds": {"reuse": 0},
            }
        )
        write_json_atomic(target_validation_path, target_validation)
        write_json_atomic(target_manifest_path, target_manifest)
        ready_targets.append(target_file)
        staged.append(
            {
                "valid_time": valid_time,
                "source": str(source_file),
                "target": str(target_file),
                "forcing_sha256": source_digest,
                "forcing_size_bytes": target_file.stat().st_size,
                "inode": target_file.stat().st_ino,
                "target_manifest_sha256": sha256(target_manifest_path),
                "target_validation_sha256": sha256(target_validation_path),
            }
        )

    for target_file in ready_targets:
        publish_ready(target_file)

    assessor = Path(__file__).resolve()
    report = {
        "schema_version": 1,
        "status": "PASS",
        "decision": "EXACT_FORCING_CACHE_REUSED",
        "assessor": str(assessor),
        "assessor_sha256": sha256(assessor),
        "target_campaign": str(args.target_campaign.resolve()),
        "target_campaign_sha256": sha256(args.target_campaign),
        "target_index": str(target_index_path.resolve()),
        "target_index_sha256": sha256(target_index_path),
        "evidence_campaign": str(args.evidence_campaign.resolve()),
        "evidence_campaign_sha256": sha256(args.evidence_campaign),
        "source_index": str(args.source_index.resolve()),
        "source_index_sha256": sha256(args.source_index),
        "evidence_publications": evidence_publications,
        "record_count": len(staged),
        "valid_time_range": [min(target_records), max(target_records)],
        "method": "same-filesystem hardlink with path-local manifest rewrite",
        "records": staged,
    }
    write_json_atomic(args.output_report, report)
    publish_ready(args.output_report, sha256(args.output_report) + "\n")
    print(f"PASS: reused {len(staged)} exact forcing records at {target_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
