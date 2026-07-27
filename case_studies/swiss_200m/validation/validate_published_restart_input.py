#!/usr/bin/env python3
"""Validate a published model boundary or intermediate checkpoint for restart."""

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


def validate_restart_input(
    report: dict,
    *,
    restart_file: Path,
    expected_time: str,
    expected_source_commit: str,
) -> list[str]:
    failures: list[str] = []
    if report.get("status") != "PASS":
        failures.append("restart input publication is not PASS")

    if isinstance(report.get("restart"), dict):
        report_type = "model_completion"
        artifact = report["restart"]
        reported_path = artifact.get("path")
        reported_sha = artifact.get("sha256")
        reported_time = report.get("end")
        reported_source = report.get("provenance", {}).get("source_commit")
    elif report.get("checkpoint"):
        report_type = "checkpoint_inventory"
        reported_path = report.get("checkpoint")
        reported_sha = report.get("sha256")
        reported_time = report.get("checkpoint_time")
        reported_source = report.get("expected_source_commit")
        if report.get("expected_time") != expected_time:
            failures.append("checkpoint expected_time does not match restart boundary")
    else:
        report_type = "unsupported"
        reported_path = None
        reported_sha = None
        reported_time = None
        reported_source = None
        failures.append("restart input publication schema is unsupported")

    if reported_time != expected_time:
        failures.append("restart input publication has the wrong boundary time")
    if reported_source != expected_source_commit:
        failures.append("restart input publication has the wrong source commit")
    if not reported_path or Path(reported_path).resolve() != restart_file.resolve():
        failures.append("restart input path disagrees with its publication")
    if not restart_file.is_file():
        failures.append(f"restart input file is missing: {restart_file}")
    elif not reported_sha or sha256(restart_file) != reported_sha:
        failures.append("restart input checksum disagrees with its publication")

    if report_type == "checkpoint_inventory":
        encoded_offset = report.get("encoded_time_offset_seconds")
        if not isinstance(encoded_offset, (int, float)):
            failures.append("checkpoint lacks encoded time-offset evidence")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--restart-file", type=Path, required=True)
    parser.add_argument("--expected-time", required=True)
    parser.add_argument("--expected-source-commit", required=True)
    args = parser.parse_args()

    ready = Path(f"{args.report}.ready")
    if not args.report.is_file() or not ready.is_file():
        raise SystemExit("restart input publication or ready marker is missing")
    with args.report.open(encoding="utf-8") as stream:
        report = json.load(stream)
    failures = validate_restart_input(
        report,
        restart_file=args.restart_file,
        expected_time=args.expected_time,
        expected_source_commit=args.expected_source_commit,
    )
    if failures:
        raise SystemExit("; ".join(failures))
    print(f"PASS: published restart input verified: {args.restart_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
