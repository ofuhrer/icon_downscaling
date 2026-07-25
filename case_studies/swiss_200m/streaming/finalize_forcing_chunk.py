#!/usr/bin/env python3
"""Validate and publish the aggregate forcing manifest for a stream chunk."""

from __future__ import annotations

import argparse
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


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    report_path = (
        args.report or Path(plan["chunk_root"]) / "forcing_publication.json"
    ).resolve()
    failures = []
    entries = []
    total_forcing_bytes = 0
    total_source_bytes = 0
    stage_totals: dict[str, int] = {}
    for record in plan["records"]:
        forcing = Path(record["forcing_file"])
        manifest_path = forcing.with_suffix(".manifest.json")
        validation_path = forcing.with_suffix(".validation.json")
        ready = Path(f"{forcing}.ready")
        for path in (forcing, ready, manifest_path, validation_path):
            if not path.exists():
                failures.append(f"missing record artifact: {path}")
        if failures and not all(
            path.exists() for path in (forcing, ready, manifest_path, validation_path)
        ):
            continue
        manifest = json.loads(manifest_path.read_text())
        validation = json.loads(validation_path.read_text())
        actual_hash = sha256(forcing)
        if manifest.get("status") != "PASS" or validation.get("status") != "PASS":
            failures.append(f"record {record['index']} is not PASS")
        if manifest.get("valid_time") != record["valid_time"]:
            failures.append(f"record {record['index']} valid-time mismatch")
        if manifest.get("forcing_sha256") != actual_hash:
            failures.append(f"record {record['index']} forcing hash mismatch")
        total_forcing_bytes += forcing.stat().st_size
        total_source_bytes += int(manifest["source_dynamic"]["size_bytes"])
        for name, seconds in manifest["stage_seconds"].items():
            stage_totals[name] = stage_totals.get(name, 0) + int(seconds)
        entries.append(
            {
                "index": record["index"],
                "valid_time": record["valid_time"],
                "cycle_date": record["cycle_date"],
                "step_hours": record["step_hours"],
                "forcing_file": str(forcing.resolve()),
                "forcing_size_bytes": forcing.stat().st_size,
                "forcing_sha256": actual_hash,
                "record_manifest": str(manifest_path.resolve()),
                "record_manifest_sha256": sha256(manifest_path),
                "validation_report": str(validation_path.resolve()),
                "validation_report_sha256": sha256(validation_path),
                "stage_seconds": manifest["stage_seconds"],
            }
        )
    payload = {
        "status": "PASS" if not failures else "FAIL",
        "chunk_id": plan["chunk_id"],
        "start": plan["start"],
        "end": plan["end"],
        "hours": plan["hours"],
        "records": len(entries),
        "expected_records": plan["record_count"],
        "cycle_policy": plan["cycle_policy"],
        "plan": str(args.plan.resolve()),
        "plan_sha256": sha256(args.plan),
        "forcing_list": plan["forcing_list"],
        "forcing_list_sha256": sha256(Path(plan["forcing_list"])),
        "total_forcing_bytes": total_forcing_bytes,
        "mean_forcing_bytes": total_forcing_bytes / len(entries) if entries else None,
        "transient_source_bytes_read": total_source_bytes,
        "aggregate_worker_stage_seconds": stage_totals,
        "failures": failures,
        "entries": entries,
    }
    write_json_atomic(report_path, payload)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    Path(f"{report_path}.ready").touch()
    print(
        f"PASS: forcing chunk {plan['chunk_id']} published with "
        f"{len(entries)} records and {total_forcing_bytes} bytes"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
