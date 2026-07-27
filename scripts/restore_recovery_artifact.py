#!/usr/bin/env python3
"""Restore one checksum-published recovery artifact into a working path."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


CHUNK_BYTES = 16 * 1024 * 1024
DEFAULT_ALLOWED_PREFIX = Path("/store_new/mch/msopr/olifu/icon_downscaling")


def sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK_BYTES), b""):
            hasher.update(block)
    return hasher.hexdigest()


def publication_paths(destination: Path) -> tuple[Path, Path, Path]:
    report = destination.with_name(f"{destination.name}.archive.json")
    data_ready = destination.with_name(f"{destination.name}.ready")
    report_ready = report.with_name(f"{report.name}.ready")
    return report, data_ready, report_ready


def resolve_item(
    plan_path: Path,
    item_id: str,
    allowed_prefix: Path,
) -> dict[str, Any]:
    plan = json.loads(plan_path.read_text())
    if plan.get("schema_version") != 1:
        raise ValueError("archive plan schema_version must be 1")
    matches = [item for item in plan.get("files", []) if item.get("id") == item_id]
    if len(matches) != 1:
        raise ValueError(f"archive plan has {len(matches)} matches for {item_id}")
    item = matches[0]
    archive_root = Path(plan["archive_root"]).resolve()
    allowed = allowed_prefix.resolve()
    if archive_root != allowed and allowed not in archive_root.parents:
        raise ValueError(f"archive root escapes allowed prefix: {archive_root}")
    relative = Path(item["destination"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe archive destination: {relative}")
    destination = (archive_root / relative).resolve()
    if destination != allowed and allowed not in destination.parents:
        raise ValueError(f"archive item escapes allowed prefix: {destination}")
    expected = item.get("expected_sha256")
    if not isinstance(expected, str) or len(expected) != 64:
        raise ValueError(f"{item_id} has no fixed expected SHA-256")
    return {
        "archive_id": plan["archive_id"],
        "classification": item["classification"],
        "item_id": item_id,
        "destination": destination,
        "expected_sha256": expected,
    }


def validate_source(item: dict[str, Any]) -> dict[str, Any]:
    destination: Path = item["destination"]
    report_path, data_ready, report_ready = publication_paths(destination)
    for path in (destination, report_path, data_ready, report_ready):
        if not path.is_file():
            raise ValueError(f"archive publication is incomplete: {path}")
    report = json.loads(report_path.read_text())
    if (
        report.get("schema_version") != 1
        or report.get("status") != "PASS"
        or report.get("item_id") != item["item_id"]
        or Path(report.get("destination", "")).resolve() != destination
    ):
        raise ValueError(f"archive item report is invalid: {report_path}")
    digest = sha256(destination)
    if digest != item["expected_sha256"] or digest != report.get("sha256"):
        raise ValueError(f"archive item checksum mismatch: {destination}")
    if destination.stat().st_size != report.get("size_bytes"):
        raise ValueError(f"archive item size mismatch: {destination}")
    return report


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
        temporary = Path(stream.name)
    os.replace(temporary, path)


def restore(item: dict[str, Any], output: Path) -> dict[str, Any]:
    source: Path = item["destination"]
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output_ready = Path(f"{output}.ready")
    restore_report = Path(f"{output}.restore.json")
    restore_report_ready = Path(f"{restore_report}.ready")
    existing = [path.exists() for path in (output, output_ready, restore_report, restore_report_ready)]
    if any(existing):
        if not all(existing):
            raise ValueError(f"incomplete existing restore publication: {output}")
        report = json.loads(restore_report.read_text())
        if (
            report.get("status") == "PASS"
            and report.get("item_id") == item["item_id"]
            and report.get("sha256") == item["expected_sha256"]
            and sha256(output) == item["expected_sha256"]
        ):
            return report
        raise ValueError(f"refusing to replace existing restore: {output}")

    partial = output.with_name(f".{output.name}.{os.getpid()}.partial")
    if partial.exists():
        raise ValueError(f"stale restore staging file exists: {partial}")
    hasher = hashlib.sha256()
    try:
        with source.open("rb") as source_stream, partial.open("xb") as target:
            for block in iter(lambda: source_stream.read(CHUNK_BYTES), b""):
                target.write(block)
                hasher.update(block)
            target.flush()
            os.fsync(target.fileno())
        digest = hasher.hexdigest()
        if digest != item["expected_sha256"] or sha256(partial) != digest:
            raise ValueError("restored artifact checksum mismatch")
        os.replace(partial, output)
        payload = {
            "schema_version": 1,
            "status": "PASS",
            "purpose": "recovery-artifact-restore",
            "archive_id": item["archive_id"],
            "classification": item["classification"],
            "item_id": item["item_id"],
            "source": str(source),
            "output": str(output),
            "size_bytes": output.stat().st_size,
            "sha256": digest,
        }
        atomic_json(restore_report, payload)
        output_ready.touch()
        restore_report_ready.touch()
        return payload
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--item-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--allowed-destination-prefix",
        type=Path,
        default=DEFAULT_ALLOWED_PREFIX,
    )
    args = parser.parse_args()
    item = resolve_item(args.plan, args.item_id, args.allowed_destination_prefix)
    validate_source(item)
    payload = restore(item, args.output)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
