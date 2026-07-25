#!/usr/bin/env python3
"""Verify a published recovery archive without consulting its scratch sources."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

CHUNK_BYTES = 16 * 1024 * 1024
DEFAULT_ALLOWED_PREFIX = Path("/store_new/mch/msopr/olifu/icon_downscaling")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def require_inside(path: Path, allowed_prefix: Path) -> Path:
    resolved = path.resolve()
    allowed = allowed_prefix.resolve()
    if resolved != allowed and allowed not in resolved.parents:
        raise ValueError(f"path escapes allowed archive prefix: {resolved}")
    return resolved


def publication_paths(destination: Path) -> tuple[Path, Path, Path]:
    report = destination.with_name(f"{destination.name}.archive.json")
    data_ready = destination.with_name(f"{destination.name}.ready")
    report_ready = report.with_name(f"{report.name}.ready")
    return report, data_ready, report_ready


def verify_manifest(manifest: Path, allowed_prefix: Path) -> dict[str, Any]:
    manifest = require_inside(manifest, allowed_prefix)
    manifest_ready = manifest.with_name(f"{manifest.name}.ready")
    manifest_checksum = manifest.with_name(f"{manifest.name}.sha256")
    if (
        not manifest.is_file()
        or not manifest_checksum.is_file()
        or not manifest_ready.is_file()
    ):
        raise RuntimeError(
            "archive manifest, checksum, or ready marker is missing"
        )
    observed_manifest_sha256 = sha256(manifest)
    checksum_record = manifest_checksum.read_text(encoding="utf-8").split()
    if not checksum_record or checksum_record[0] != observed_manifest_sha256:
        raise RuntimeError("archive manifest checksum does not match")

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or payload.get("status") != "PASS":
        raise RuntimeError("archive manifest is not a published schema-v1 PASS")
    records = payload.get("files")
    if not isinstance(records, list) or not records:
        raise RuntimeError("archive manifest has no file records")
    if payload.get("file_count") != len(records):
        raise RuntimeError("archive manifest file_count does not match its records")

    total = 0
    seen: set[Path] = set()
    for record in records:
        destination = require_inside(Path(record["destination"]), allowed_prefix)
        if destination in seen:
            raise RuntimeError(f"duplicate destination in manifest: {destination}")
        seen.add(destination)
        report, data_ready, report_ready = publication_paths(destination)
        for required in (destination, report, data_ready, report_ready):
            if not required.is_file():
                raise RuntimeError(f"missing published archive path: {required}")

        item_report = json.loads(report.read_text(encoding="utf-8"))
        if item_report != record:
            raise RuntimeError(f"manifest/report disagreement: {destination}")
        size = destination.stat().st_size
        if size != record.get("size_bytes"):
            raise RuntimeError(f"size mismatch: {destination}")
        observed = sha256(destination)
        if observed != record.get("sha256"):
            raise RuntimeError(f"SHA-256 mismatch: {destination}")
        total += size

    if total != payload.get("size_bytes"):
        raise RuntimeError("archive manifest size_bytes does not match its records")
    return {
        "archive_id": payload.get("archive_id"),
        "file_count": len(records),
        "manifest": str(manifest),
        "manifest_sha256": observed_manifest_sha256,
        "size_bytes": total,
        "status": "PASS",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument(
        "--allowed-destination-prefix",
        type=Path,
        default=DEFAULT_ALLOWED_PREFIX,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = verify_manifest(args.manifest, args.allowed_destination_prefix)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, KeyError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error
