#!/usr/bin/env python3
"""Losslessly compress one HICAR NetCDF output and atomically publish it."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
import subprocess
from tempfile import NamedTemporaryFile

import netCDF4
import numpy as np


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def logical_sha256(variable) -> str:
    digest = hashlib.sha256()
    if not variable.shape:
        blocks = [variable[...]]
    else:
        blocks = (variable[index] for index in range(variable.shape[0]))
    for block in blocks:
        values = np.ma.asarray(block)
        digest.update(np.ascontiguousarray(values.data).tobytes())
        digest.update(np.ascontiguousarray(np.ma.getmaskarray(values)).tobytes())
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


def verify_publication(source: Path, target: Path, report_path: Path) -> bool:
    try:
        report = json.loads(report_path.read_text())
        return (
            target.is_file()
            and report.get("status") == "PASS"
            and report.get("source_sha256") == file_sha256(source)
            and report.get("target_sha256") == file_sha256(target)
        )
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def quarantine_incomplete(target: Path, report: Path) -> None:
    artifacts = [path for path in (target, report) if path.exists()]
    if not artifacts:
        return
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    recovery = target.parent / "recovery" / f"{target.name}.{stamp}"
    recovery.mkdir(parents=True, exist_ok=False)
    for artifact in artifacts:
        os.replace(artifact, recovery / artifact.name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--target", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--compression-level", type=int, default=1)
    parser.add_argument("--nccopy", default="nccopy")
    args = parser.parse_args()
    if not 1 <= args.compression_level <= 9:
        raise SystemExit("--compression-level must be 1..9")
    if not args.source.is_file() or args.source.stat().st_size == 0:
        raise SystemExit(f"source is missing or empty: {args.source}")
    ready = Path(f"{args.target}.ready")
    if ready.exists():
        if verify_publication(args.source, args.target, args.report):
            print(f"already published and verified: {args.target}")
            return 0
        raise SystemExit("ready marker exists for an invalid compressed publication")
    if args.target.exists() or args.report.exists():
        if verify_publication(args.source, args.target, args.report):
            ready.touch()
            print(f"recovered publication marker: {args.target}")
            return 0
        quarantine_incomplete(args.target, args.report)

    args.target.parent.mkdir(parents=True, exist_ok=True)
    partial = args.target.with_name(
        f".{args.target.name}.partial.{os.getpid()}"
    )
    try:
        subprocess.run(
            [
                args.nccopy,
                "-d",
                str(args.compression_level),
                "-s",
                str(args.source),
                str(partial),
            ],
            check=True,
        )
        logical_hashes: dict[str, str] = {}
        with netCDF4.Dataset(args.source) as source, netCDF4.Dataset(partial) as target:
            if {
                name: len(value) for name, value in source.dimensions.items()
            } != {
                name: len(value) for name, value in target.dimensions.items()
            }:
                raise SystemExit("compressed dimensions differ from source")
            if set(source.variables) != set(target.variables):
                raise SystemExit("compressed variables differ from source")
            for name in source.variables:
                left = source.variables[name]
                right = target.variables[name]
                if left.dtype != right.dtype or left.dimensions != right.dimensions:
                    raise SystemExit(f"compressed variable metadata differs: {name}")
                left_hash = logical_sha256(left)
                right_hash = logical_sha256(right)
                if left_hash != right_hash:
                    raise SystemExit(f"compressed variable values differ: {name}")
                logical_hashes[name] = left_hash

        source_sha = file_sha256(args.source)
        target_sha = file_sha256(partial)
        source_bytes = args.source.stat().st_size
        target_bytes = partial.stat().st_size
        os.replace(partial, args.target)
        payload = {
            "status": "PASS",
            "source": str(args.source.resolve()),
            "source_bytes": source_bytes,
            "source_sha256": source_sha,
            "target": str(args.target.resolve()),
            "target_bytes": target_bytes,
            "target_sha256": target_sha,
            "compression_level": args.compression_level,
            "physical_size_ratio": target_bytes / source_bytes,
            "logical_variable_sha256": logical_hashes,
        }
        write_json_atomic(args.report, payload)
        ready.touch()
    finally:
        partial.unlink(missing_ok=True)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
