#!/usr/bin/env python3
"""Validate every declared restart boundary of one completed event."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
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


def ready_marker_valid(report: Path) -> bool:
    ready = Path(f"{report}.ready")
    if not report.is_file() or not ready.is_file():
        return False
    return ready.read_text().strip() == f"{sha256(report)}  {report.name}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--static-basename", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--duration-hours", required=True, type=int)
    parser.add_argument("--interval-hours", type=int, default=24)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()

    if (
        args.duration_hours <= 0
        or args.interval_hours <= 0
        or args.duration_hours % args.interval_hours
    ):
        raise SystemExit(
            "duration-hours must be positive and divisible by interval-hours"
        )

    completion_path = args.run_dir / "model_chunk_completion.json"
    completion_ready = Path(f"{completion_path}.ready")
    failures: list[str] = []
    event_name = None
    if not completion_path.is_file() or not completion_ready.is_file():
        failures.append("model completion publication is missing")
    else:
        completion = json.loads(completion_path.read_text())
        event_name = completion.get("chunk_id")
        if completion.get("status") != "PASS":
            failures.append("model completion status is not PASS")
        if not isinstance(event_name, str) or not event_name:
            failures.append("model completion lacks chunk_id")

    start = datetime.fromisoformat(args.start)
    validator = Path(__file__).with_name("validate_restart_checkpoint.py")
    report_dir = args.report.parent / "restart_checkpoints"
    inventories = []
    for elapsed_hours in range(
        args.interval_hours,
        args.duration_hours + 1,
        args.interval_hours,
    ):
        expected = start + timedelta(hours=elapsed_hours)
        checkpoint = (
            args.run_dir
            / "restart"
            / f"{args.static_basename}_{expected:%Y-%m-%d_%H-%M-%S}.nc"
        )
        inventory = report_dir / f"checkpoint_{elapsed_hours:03d}h.json"
        completed = subprocess.run(
            [
                sys.executable,
                str(validator),
                "--checkpoint",
                str(checkpoint),
                "--expected-time",
                expected.isoformat(),
                "--expected-source-commit",
                args.expected_source_commit,
                "--report",
                str(inventory),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        payload = json.loads(inventory.read_text()) if inventory.is_file() else {}
        publication_valid = ready_marker_valid(inventory)
        passed = (
            completed.returncode == 0
            and payload.get("status") == "PASS"
            and publication_valid
        )
        if not passed:
            failures.append(f"{elapsed_hours} h restart checkpoint did not pass")
        inventories.append(
            {
                "elapsed_hours": elapsed_hours,
                "expected_time": expected.isoformat(),
                "checkpoint": str(checkpoint.resolve()),
                "inventory_report": str(inventory.resolve()),
                "inventory_report_sha256": (
                    sha256(inventory) if inventory.is_file() else None
                ),
                "inventory_ready_marker_valid": publication_valid,
                "checkpoint_size_bytes": payload.get("size_bytes"),
                "checkpoint_sha256": payload.get("sha256"),
                "checkpoint_time": payload.get("checkpoint_time"),
                "status": "PASS" if passed else "FAIL",
                "validator_stdout": completed.stdout.strip(),
                "validator_stderr": completed.stderr.strip(),
            }
        )

    payload = {
        "schema_version": 1,
        "status": "PASS" if not failures else "FAIL",
        "event_name": event_name,
        "run_dir": str(args.run_dir.resolve()),
        "start": start.isoformat(),
        "duration_hours": args.duration_hours,
        "interval_hours": args.interval_hours,
        "expected_source_commit": args.expected_source_commit,
        "checkpoint_count": len(inventories),
        "checkpoints": inventories,
        "validation_scope": (
            "Every declared 24-hour event boundary is independently checked for "
            "canonical time, qualified schema, repaired source identity, and "
            "whole-file SHA-256."
        ),
        "failures": failures,
    }
    write_json_atomic(args.report, payload)
    ready = Path(f"{args.report}.ready")
    if failures:
        ready.unlink(missing_ok=True)
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1

    ready.write_text(f"{sha256(args.report)}  {args.report.name}\n")
    print(f"PASS: event restart audit published at {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
