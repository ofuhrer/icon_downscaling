#!/usr/bin/env python3
"""Publish a time subset that reuses an immutable forcing publication."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as stream:
        stream.write(content)
        temporary = Path(stream.name)
    os.replace(temporary, path)


def publish(path: Path, content: str) -> None:
    ready = Path(f"{path}.ready")
    if path.exists() or ready.exists():
        if path.is_file() and ready.is_file() and path.read_text() == content:
            return
        raise ValueError(f"refusing to replace non-identical publication: {path}")
    write_atomic(path, content)
    ready.write_text(f"{sha256(path)}  {path.name}\n")


def mapped_path(path_value: str, source_dir: Path, reused_dir: Path) -> str:
    relative = Path(path_value).resolve().relative_to(source_dir.resolve())
    return str(reused_dir / relative)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-plan", required=True, type=Path)
    parser.add_argument("--source-publication", required=True, type=Path)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--chunk-id", required=True)
    parser.add_argument("--chunk-root", required=True, type=Path)
    parser.add_argument("--producer-concurrency", type=int, default=1)
    args = parser.parse_args()

    for path in (args.source_plan, args.source_publication):
        if not path.is_file() or not Path(f"{path}.ready").is_file():
            raise SystemExit(f"source publication is missing: {path}")
    source_plan = json.loads(args.source_plan.read_text())
    source_publication = json.loads(args.source_publication.read_text())
    if source_plan.get("status") != "PLANNED":
        raise SystemExit("source plan is not PLANNED")
    if source_publication.get("status") != "PASS":
        raise SystemExit("source forcing publication is not PASS")
    if source_publication.get("plan_sha256") != sha256(args.source_plan):
        raise SystemExit("source forcing publication does not match source plan")
    source_list = Path(source_plan["forcing_list"])
    if (
        not source_list.is_file()
        or not Path(f"{source_list}.ready").is_file()
        or source_publication.get("forcing_list_sha256") != sha256(source_list)
    ):
        raise SystemExit("source forcing list publication is invalid")

    start = datetime.fromisoformat(args.start)
    end = datetime.fromisoformat(args.end)
    if end <= start or any(
        (value.minute, value.second, value.microsecond) != (0, 0, 0)
        for value in (start, end)
    ):
        raise SystemExit("start/end must be distinct exact hours")
    expected_times = []
    valid = start
    while valid <= end:
        expected_times.append(valid.isoformat())
        valid += timedelta(hours=1)

    source_records = {
        item["valid_time"]: item for item in source_plan.get("records", [])
    }
    source_entries = {
        item["valid_time"]: item for item in source_publication.get("entries", [])
    }
    missing = sorted(
        value
        for value in expected_times
        if value not in source_records or value not in source_entries
    )
    if missing:
        raise SystemExit(f"source publication lacks required times: {missing}")

    chunk_root = args.chunk_root.resolve()
    reused_dir = chunk_root / "forcing"
    source_dir = Path(source_plan["chunk_root"]).resolve() / "forcing"
    if reused_dir.exists() or reused_dir.is_symlink():
        if not reused_dir.is_symlink() or reused_dir.resolve() != source_dir:
            raise SystemExit(f"incorrect existing forcing reuse link: {reused_dir}")
    else:
        chunk_root.mkdir(parents=True, exist_ok=True)
        reused_dir.symlink_to(source_dir, target_is_directory=True)

    records = []
    entries = []
    for index, valid_time in enumerate(expected_times):
        record = dict(source_records[valid_time])
        record["index"] = index
        record["forcing_file"] = mapped_path(
            record["forcing_file"], source_dir, reused_dir
        )
        record["ready_marker"] = f"{record['forcing_file']}.ready"
        entry = dict(source_entries[valid_time])
        entry["index"] = index
        for name in ("forcing_file", "record_manifest", "validation_report"):
            entry[name] = mapped_path(entry[name], source_dir, reused_dir)
        checks = (
            ("forcing_file", "forcing_sha256"),
            ("record_manifest", "record_manifest_sha256"),
            ("validation_report", "validation_report_sha256"),
        )
        for path_name, digest_name in checks:
            path = Path(entry[path_name])
            if not path.is_file() or sha256(path) != entry[digest_name]:
                raise SystemExit(
                    f"source {path_name} publication is invalid for {valid_time}"
                )
            if path_name == "forcing_file" and not Path(f"{path}.ready").is_file():
                raise SystemExit(
                    f"source forcing_file publication is not ready for {valid_time}"
                )
        records.append(record)
        entries.append(entry)

    forcing_list = chunk_root / "forcing_list.txt"
    plan_path = chunk_root / "chunk_plan.json"
    publication_path = chunk_root / "forcing_publication.json"
    listed = "".join(f'"{item["forcing_file"]}"\n' for item in records)
    plan = {
        "schema_version": 1,
        "status": "PLANNED",
        "chunk_id": args.chunk_id,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "hours": int((end - start).total_seconds() // 3600),
        "record_count": len(records),
        "producer_concurrency": args.producer_concurrency,
        "cycle_policy": source_plan["cycle_policy"],
        "transient_policy": (
            "This immutable subset reuses an already validated forcing "
            "publication; source payload retirement remains forbidden until "
            "both consumers have published."
        ),
        "chunk_root": str(chunk_root),
        "forcing_list": str(forcing_list),
        "records": records,
        "reused_source_plan": str(args.source_plan.resolve()),
        "reused_source_plan_sha256": sha256(args.source_plan),
        "reused_source_publication": str(args.source_publication.resolve()),
        "reused_source_publication_sha256": sha256(args.source_publication),
    }
    plan_content = json.dumps(plan, indent=2, sort_keys=True) + "\n"
    publish(plan_path, plan_content)
    publish(forcing_list, listed)

    stage_names = sorted(
        {
            name
            for entry in entries
            for name in entry.get("stage_seconds", {})
        }
    )
    aggregate_stage_seconds = {
        name: sum(int(entry.get("stage_seconds", {}).get(name, 0)) for entry in entries)
        for name in stage_names
    }
    total_bytes = sum(int(entry["forcing_size_bytes"]) for entry in entries)
    publication = {
        "schema_version": 1,
        "status": "PASS",
        "chunk_id": args.chunk_id,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "hours": plan["hours"],
        "records": len(records),
        "expected_records": len(records),
        "failures": [],
        "plan": str(plan_path),
        "plan_sha256": sha256(plan_path),
        "forcing_list": str(forcing_list),
        "forcing_list_sha256": sha256(forcing_list),
        "cycle_policy": plan["cycle_policy"],
        "entries": entries,
        "total_forcing_bytes": total_bytes,
        "mean_forcing_bytes": total_bytes / len(entries),
        "aggregate_worker_stage_seconds": aggregate_stage_seconds,
        "transient_source_bytes_read": 0,
        "reuse": {
            "source_plan": str(args.source_plan.resolve()),
            "source_plan_sha256": sha256(args.source_plan),
            "source_publication": str(args.source_publication.resolve()),
            "source_publication_sha256": sha256(args.source_publication),
            "source_forcing_directory": str(source_dir),
            "reused_payload_count": len(entries),
            "additional_fdb_retrievals": 0,
            "additional_fieldextra_conversions": 0,
        },
    }
    publish(
        publication_path,
        json.dumps(publication, indent=2, sort_keys=True) + "\n",
    )
    print(
        f"PASS: reused forcing chunk published at {chunk_root}; "
        f"records={len(records)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
