#!/usr/bin/env python3
"""Retire compact intervals after an exact merged product is published."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_publication(path: Path) -> dict:
    marker = Path(f"{path}.ready")
    if not path.is_file() or not marker.is_file():
        raise SystemExit(f"publication is incomplete: {path}")
    payload = json.loads(path.read_text())
    if payload.get("status") != "PASS":
        raise SystemExit(f"publication is not PASS: {path}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--merged-publication", required=True, type=Path)
    parser.add_argument(
        "--execute",
        action="store_true",
        help=(
            "Delete verified interval NetCDF files and ready markers; "
            "default is a dry run."
        ),
    )
    args = parser.parse_args()

    merged = require_publication(args.merged_publication)
    entries = merged.get("inputs", [])
    if not entries:
        raise SystemExit("merged publication records no input intervals")

    merged_path = Path(merged["output"]).resolve()
    merged_ready = Path(f"{merged_path}.ready")
    if not merged_path.is_file() or not merged_ready.is_file():
        raise SystemExit("merged wind product is not published")
    if sha256(merged_path) != merged.get("output_sha256"):
        raise SystemExit("merged wind product hash changed")

    targets: list[Path] = []
    total_bytes = 0
    for entry in entries:
        path = Path(entry["path"]).resolve()
        ready = Path(f"{path}.ready")
        if path == merged_path:
            raise SystemExit("merged product aliases an input interval")
        if not path.is_file() or not ready.is_file():
            raise SystemExit(f"input interval publication is incomplete: {path}")
        if path.stat().st_size != entry.get("size_bytes"):
            raise SystemExit(f"input interval size changed: {path}")
        if sha256(path) != entry.get("sha256"):
            raise SystemExit(f"input interval hash changed: {path}")
        targets.append(path)
        total_bytes += path.stat().st_size

    print(
        json.dumps(
            {
                "status": "READY_TO_RETIRE",
                "execute": args.execute,
                "payload_bytes": total_bytes,
                "merged_product": str(merged_path),
                "targets": [str(path) for path in targets],
            },
            indent=2,
            sort_keys=True,
        )
    )
    if args.execute:
        for path in targets:
            Path(f"{path}.ready").unlink()
            path.unlink()
        print(f"retired {len(targets)} compact wind interval(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
