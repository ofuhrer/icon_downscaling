#!/usr/bin/env python3
"""Adapt a published clock-transform report to the chunk-validator contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def publish(transform_path: Path, restart_path: Path, output_path: Path) -> dict[str, Any]:
    if not transform_path.is_file() or not Path(f"{transform_path}.ready").is_file():
        raise ValueError("clock-transform publication is missing")
    transform = json.loads(transform_path.read_text(encoding="utf-8"))
    if transform.get("status") != "PASS":
        raise ValueError("clock-transform publication is not PASS")
    if Path(transform.get("checkpoint", "")).resolve() != restart_path.resolve():
        raise ValueError("clock-transform publication points to another restart")
    digest = sha256(restart_path)
    if transform.get("sha256") != digest:
        raise ValueError("relabelled restart checksum changed")
    target_time = transform["checkpoint_time"]
    payload = {
        **transform,
        "end": target_time,
        "restart": {
            "path": str(restart_path.resolve()),
            "sha256": digest,
            "size_bytes": restart_path.stat().st_size,
            "times": [target_time],
        },
        "provenance": {
            "source_commit": transform["expected_source_commit"],
        },
        "compatibility_source": {
            "path": str(transform_path.resolve()),
            "sha256": sha256(transform_path),
        },
    }
    ready = Path(f"{output_path}.ready")
    if output_path.exists() or ready.exists():
        raise ValueError(f"refusing to replace compatibility report: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=output_path.parent, delete=False
    ) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, output_path)
    ready.touch()
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transform", type=Path, required=True)
    parser.add_argument("--restart", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = publish(
        args.transform.resolve(), args.restart.resolve(), args.output.resolve()
    )
    print(f"restart compatibility publication: {payload['end']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
