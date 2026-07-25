#!/usr/bin/env python3
"""Retire raw fixed-height wind files after compact reduction is published."""

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
    parser.add_argument("--model-completion", required=True, type=Path)
    parser.add_argument("--wind-reduction", required=True, type=Path)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Delete the verified raw HICAR wind files; default is a dry run.",
    )
    args = parser.parse_args()

    model = require_publication(args.model_completion)
    reduction = require_publication(args.wind_reduction)
    if model.get("output_profile") != "wind_climatology":
        raise SystemExit("model completion is not a wind_climatology profile")
    if reduction.get("interval_start") != model.get("start"):
        raise SystemExit("wind reduction interval start disagrees with model chunk")

    model_entries = model.get("output", {}).get("files", [])
    reduction_entries = reduction.get("inputs", [])
    if not model_entries or len(model_entries) != len(reduction_entries):
        raise SystemExit("wind reduction does not cover every raw model output")

    targets: list[Path] = []
    total_bytes = 0
    for model_entry, reduction_entry in zip(model_entries, reduction_entries):
        model_path = Path(model_entry["path"]).resolve()
        reduction_path = Path(reduction_entry["path"]).resolve()
        if model_path != reduction_path:
            raise SystemExit("wind reduction input order differs from model completion")
        if model_entry["sha256"] != reduction_entry["sha256"]:
            raise SystemExit(f"published source hashes disagree: {model_path}")
        if model_entry["size_bytes"] != reduction_entry.get("size_bytes"):
            raise SystemExit(f"published source sizes disagree: {model_path}")
        if not model_path.is_file():
            raise SystemExit(f"raw wind source is missing: {model_path}")
        if model_path.stat().st_size != model_entry["size_bytes"]:
            raise SystemExit(f"raw wind source size changed: {model_path}")
        if sha256(model_path) != model_entry["sha256"]:
            raise SystemExit(f"raw wind source hash changed: {model_path}")
        targets.append(model_path)
        total_bytes += model_path.stat().st_size

    reduced_path = Path(reduction["output"]).resolve()
    if reduced_path in targets:
        raise SystemExit("reduced product aliases a raw source")
    if not reduced_path.is_file() or not Path(f"{reduced_path}.ready").is_file():
        raise SystemExit("reduced wind product is not published")
    if sha256(reduced_path) != reduction.get("output_sha256"):
        raise SystemExit("reduced wind product hash changed")

    print(
        json.dumps(
            {
                "status": "READY_TO_RETIRE",
                "chunk_id": model["chunk_id"],
                "execute": args.execute,
                "payload_bytes": total_bytes,
                "reduced_product": str(reduced_path),
                "targets": [str(path) for path in targets],
            },
            indent=2,
            sort_keys=True,
        )
    )
    if args.execute:
        for path in targets:
            marker = Path(f"{path}.ready")
            if marker.exists():
                marker.unlink()
            path.unlink()
        print(f"retired {len(targets)} raw wind file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
