#!/usr/bin/env python3
"""Publish an explicit recovery-archive plan with checksums and ready markers."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

CHUNK_BYTES = 16 * 1024 * 1024
DEFAULT_ALLOWED_PREFIX = Path("/store_new/mch/msopr/olifu/icon_downscaling")


@dataclass(frozen=True)
class ArchiveItem:
    item_id: str
    source: Path
    destination: Path
    classification: str
    expected_sha256: str | None


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".partial",
        delete=False,
    ) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
        temporary = Path(stream.name)
    os.replace(temporary, path)


def atomic_marker(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="wb",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".partial",
        delete=False,
    ) as stream:
        stream.flush()
        os.fsync(stream.fileno())
        temporary = Path(stream.name)
    os.replace(temporary, path)


def atomic_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".partial",
        delete=False,
    ) as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
        temporary = Path(stream.name)
    os.replace(temporary, path)


def require_sha(value: Any, item_id: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{item_id}: expected_sha256 must be null or 64 hex digits")
    try:
        int(value, 16)
    except ValueError as error:
        raise ValueError(
            f"{item_id}: expected_sha256 must be null or 64 hex digits"
        ) from error
    return value.lower()


def safe_destination(
    archive_root: Path,
    relative: str,
    allowed_prefix: Path,
) -> Path:
    candidate_relative = Path(relative)
    if candidate_relative.is_absolute() or ".." in candidate_relative.parts:
        raise ValueError(f"unsafe archive destination: {relative}")
    destination = (archive_root / candidate_relative).resolve()
    allowed = allowed_prefix.resolve()
    if destination != allowed and allowed not in destination.parents:
        raise ValueError(f"destination escapes allowed prefix: {destination}")
    return destination


def expand_plan(
    plan: dict[str, Any],
    allowed_prefix: Path,
) -> tuple[Path, Path, list[ArchiveItem]]:
    if plan.get("schema_version") != 1:
        raise ValueError("archive plan schema_version must be 1")
    archive_root = Path(plan["archive_root"])
    if not archive_root.is_absolute():
        raise ValueError("archive_root must be absolute")
    manifest = Path(plan["manifest"])
    if not manifest.is_absolute():
        raise ValueError("manifest must be absolute")
    manifest = safe_destination(
        allowed_prefix,
        os.path.relpath(manifest, allowed_prefix),
        allowed_prefix,
    )

    raw_items: list[dict[str, Any]] = list(plan.get("files", []))
    for file_set in plan.get("file_sets", []):
        source_root = Path(file_set["source_root"])
        destination_root = file_set["destination_root"]
        classification = file_set["classification"]
        id_prefix = file_set["id_prefix"]
        for relative in file_set["paths"]:
            raw_items.append(
                {
                    "id": f"{id_prefix}:{relative}",
                    "source": str(source_root / relative),
                    "destination": str(Path(destination_root) / relative),
                    "classification": classification,
                    "expected_sha256": None,
                }
            )

    items: list[ArchiveItem] = []
    seen_ids: set[str] = set()
    seen_publication_paths = {
        manifest,
        manifest.with_name(f"{manifest.name}.sha256"),
        manifest.with_name(f"{manifest.name}.ready"),
    }
    for raw in raw_items:
        item_id = raw["id"]
        if item_id in seen_ids:
            raise ValueError(f"duplicate archive item id: {item_id}")
        source = Path(raw["source"])
        if not source.is_absolute():
            raise ValueError(f"{item_id}: source must be absolute")
        destination = safe_destination(
            archive_root,
            raw["destination"],
            allowed_prefix,
        )
        report, data_ready, report_ready = publication_paths(destination)
        item_publication_paths = {destination, report, data_ready, report_ready}
        collisions = item_publication_paths & seen_publication_paths
        if collisions:
            collision = sorted(str(path) for path in collisions)[0]
            raise ValueError(f"archive publication path collision: {collision}")
        items.append(
            ArchiveItem(
                item_id=item_id,
                source=source,
                destination=destination,
                classification=raw["classification"],
                expected_sha256=require_sha(raw.get("expected_sha256"), item_id),
            )
        )
        seen_ids.add(item_id)
        seen_publication_paths.update(item_publication_paths)
    if not items:
        raise ValueError("archive plan contains no files")
    return archive_root, manifest, items


def publication_paths(destination: Path) -> tuple[Path, Path, Path]:
    report = destination.with_name(f"{destination.name}.archive.json")
    data_ready = destination.with_name(f"{destination.name}.ready")
    report_ready = report.with_name(f"{report.name}.ready")
    return report, data_ready, report_ready


def validate_existing(item: ArchiveItem) -> dict[str, Any] | None:
    report, data_ready, report_ready = publication_paths(item.destination)
    paths = (item.destination, report, data_ready, report_ready)
    existing = [path.exists() for path in paths]
    if not any(existing):
        return None
    if not all(existing):
        raise RuntimeError(f"{item.item_id}: incomplete existing publication")
    payload = json.loads(report.read_text(encoding="utf-8"))
    observed = sha256(item.destination)
    if payload.get("sha256") != observed:
        raise RuntimeError(f"{item.item_id}: archived payload/report hash mismatch")
    if item.expected_sha256 and observed != item.expected_sha256:
        raise RuntimeError(f"{item.item_id}: archived payload has unexpected hash")
    if payload.get("size_bytes") != item.destination.stat().st_size:
        raise RuntimeError(f"{item.item_id}: archived payload/report size mismatch")
    return payload


def publish_item(item: ArchiveItem, archive_id: str) -> dict[str, Any]:
    existing = validate_existing(item)
    if existing is not None:
        return existing
    if not item.source.is_file():
        raise FileNotFoundError(f"{item.item_id}: missing source {item.source}")

    item.destination.parent.mkdir(parents=True, exist_ok=True)
    partial = item.destination.with_name(
        f".{item.destination.name}.{os.getpid()}.partial"
    )
    if partial.exists():
        raise RuntimeError(f"{item.item_id}: stale partial path exists: {partial}")

    source_before = item.source.stat()
    digest = hashlib.sha256()
    try:
        with item.source.open("rb") as source, partial.open("xb") as target:
            while block := source.read(CHUNK_BYTES):
                target.write(block)
                digest.update(block)
            target.flush()
            os.fsync(target.fileno())
        source_after = item.source.stat()
        identity_before = (
            source_before.st_dev,
            source_before.st_ino,
            source_before.st_size,
            source_before.st_mtime_ns,
        )
        identity_after = (
            source_after.st_dev,
            source_after.st_ino,
            source_after.st_size,
            source_after.st_mtime_ns,
        )
        if identity_before != identity_after:
            raise RuntimeError(f"{item.item_id}: source changed during copy")

        observed = digest.hexdigest()
        if item.expected_sha256 and observed != item.expected_sha256:
            raise RuntimeError(
                f"{item.item_id}: source hash {observed} does not match "
                f"{item.expected_sha256}"
            )
        copied = sha256(partial)
        if copied != observed:
            raise RuntimeError(f"{item.item_id}: copy readback hash mismatch")

        os.chmod(partial, stat.S_IRUSR | stat.S_IRGRP)
        os.replace(partial, item.destination)
        report, data_ready, report_ready = publication_paths(item.destination)
        payload = {
            "archive_id": archive_id,
            "classification": item.classification,
            "destination": str(item.destination),
            "item_id": item.item_id,
            "schema_version": 1,
            "sha256": observed,
            "size_bytes": source_after.st_size,
            "source": str(item.source),
            "status": "PASS",
        }
        atomic_json(report, payload)
        os.chmod(report, stat.S_IRUSR | stat.S_IRGRP)
        atomic_marker(data_ready)
        atomic_marker(report_ready)
        os.chmod(data_ready, stat.S_IRUSR | stat.S_IRGRP)
        os.chmod(report_ready, stat.S_IRUSR | stat.S_IRGRP)
        return payload
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument(
        "--allowed-destination-prefix",
        type=Path,
        default=DEFAULT_ALLOWED_PREFIX,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the plan and source paths without copying data.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    plan_bytes = args.plan.read_bytes()
    plan = json.loads(plan_bytes)
    archive_root, manifest, items = expand_plan(
        plan,
        args.allowed_destination_prefix,
    )
    plan_sha256 = hashlib.sha256(plan_bytes).hexdigest()

    if args.dry_run:
        missing = [str(item.source) for item in items if not item.source.is_file()]
        if missing:
            raise FileNotFoundError("missing archive sources:\n" + "\n".join(missing))
        total = sum(item.source.stat().st_size for item in items)
        print(
            json.dumps(
                {
                    "archive_id": plan["archive_id"],
                    "file_count": len(items),
                    "plan_sha256": plan_sha256,
                    "size_bytes": total,
                    "status": "DRY_RUN_PASS",
                },
                sort_keys=True,
            )
        )
        return 0

    archive_root.mkdir(parents=True, exist_ok=True)
    results = []
    for index, item in enumerate(items, start=1):
        print(f"[{index}/{len(items)}] {item.item_id}", flush=True)
        results.append(publish_item(item, plan["archive_id"]))

    payload = {
        "archive_id": plan["archive_id"],
        "archive_root": str(archive_root),
        "file_count": len(results),
        "files": results,
        "plan": str(args.plan),
        "plan_sha256": plan_sha256,
        "schema_version": 1,
        "size_bytes": sum(result["size_bytes"] for result in results),
        "status": "PASS",
    }
    manifest_ready = manifest.with_name(f"{manifest.name}.ready")
    manifest_checksum = manifest.with_name(f"{manifest.name}.sha256")
    if manifest.exists() or manifest_checksum.exists() or manifest_ready.exists():
        if not (
            manifest.exists()
            and manifest_checksum.exists()
            and manifest_ready.exists()
        ):
            raise RuntimeError("incomplete existing archive manifest publication")
        if json.loads(manifest.read_text(encoding="utf-8")) != payload:
            raise RuntimeError("existing archive manifest does not match this plan")
        checksum_record = manifest_checksum.read_text(encoding="utf-8").split()
        if not checksum_record or checksum_record[0] != sha256(manifest):
            raise RuntimeError("existing archive manifest checksum does not match")
    else:
        atomic_json(manifest, payload)
        os.chmod(manifest, stat.S_IRUSR | stat.S_IRGRP)
        manifest_sha256 = sha256(manifest)
        atomic_text(
            manifest_checksum,
            f"{manifest_sha256}  {manifest.name}\n",
        )
        os.chmod(manifest_checksum, stat.S_IRUSR | stat.S_IRGRP)
        atomic_marker(manifest_ready)
        os.chmod(manifest_ready, stat.S_IRUSR | stat.S_IRGRP)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error
