#!/usr/bin/env python3
"""Relabel an exact-end HICAR restart for another pass through one forcing day.

Only the restart time coordinate is changed.  The copied prognostic state is
left intact, so the next HICAR process continues the atmospheric and land
trajectory while seeing the same model calendar and forcing timestamps.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import netCDF4


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def encoded_time(path: Path) -> tuple[datetime, float, str, str]:
    with netCDF4.Dataset(path) as dataset:
        if "time" not in dataset.variables:
            raise ValueError("restart lacks time variable")
        variable = dataset.variables["time"]
        if variable.size != 1:
            raise ValueError("restart time variable must contain exactly one value")
        units = getattr(variable, "units", "")
        calendar = getattr(variable, "calendar", "standard")
        if not units:
            raise ValueError("restart time variable lacks units")
        raw = float(variable[:].reshape(-1)[0])
        decoded = netCDF4.num2date(
            raw,
            units=units,
            calendar=calendar,
            only_use_cftime_datetimes=False,
            only_use_python_datetimes=True,
        )
        decoded = datetime(
            decoded.year,
            decoded.month,
            decoded.day,
            decoded.hour,
            decoded.minute,
            decoded.second,
            decoded.microsecond,
        )
    return decoded, raw, units, calendar


def verify_source_publication(source: Path, report_path: Path) -> dict:
    ready = Path(f"{report_path}.ready")
    if not report_path.is_file() or not ready.is_file():
        raise ValueError("source restart report is not published")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("status") != "PASS":
        raise ValueError("source restart report is not PASS")
    artifact = report.get("restart", {})
    if Path(artifact.get("path", "")).resolve() != source.resolve():
        raise ValueError("source report points to a different restart")
    digest = sha256(source)
    if artifact.get("sha256") != digest:
        raise ValueError("source restart checksum disagrees with its report")
    return report


def publish_relabelled_restart(
    *,
    source: Path,
    source_report: Path,
    target: Path,
    target_time: datetime,
    expected_source_commit: str,
    report_path: Path,
) -> dict:
    if target.resolve() == source.resolve():
        raise ValueError("target must differ from source")
    target_ready = Path(f"{target}.ready")
    report_ready = Path(f"{report_path}.ready")
    if any(path.exists() for path in (target, target_ready, report_path, report_ready)):
        if not all(path.is_file() for path in (target, target_ready, report_path, report_ready)):
            raise ValueError("incomplete repeated-day restart publication exists")
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        if payload.get("sha256") != sha256(target):
            raise ValueError("published repeated-day restart checksum changed")
        actual, _, _, _ = encoded_time(target)
        if abs((actual - target_time).total_seconds()) > 1.0:
            raise ValueError("published repeated-day restart has the wrong time")
        return payload

    source_publication = verify_source_publication(source, source_report)
    if (
        source_publication.get("provenance", {}).get("source_commit")
        != expected_source_commit
    ):
        raise ValueError("source restart publication has the wrong source commit")
    source_digest = source_publication["restart"]["sha256"]
    source_time, source_raw, units, calendar = encoded_time(source)
    if target_time >= source_time:
        raise ValueError("repeated-day target time must precede source boundary time")

    target.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f".{target.name}.partial.{os.getpid()}")
    report_partial = report_path.with_name(f".{report_path.name}.partial.{os.getpid()}")
    try:
        shutil.copy2(source, partial)
        with netCDF4.Dataset(partial, "r+") as dataset:
            variable = dataset.variables["time"]
            target_raw = float(
                netCDF4.date2num(target_time, units=units, calendar=calendar)
            )
            variable[:] = target_raw
            dataset.sync()

        actual_time, actual_raw, actual_units, actual_calendar = encoded_time(partial)
        offset = (actual_time - target_time).total_seconds()
        if abs(offset) > 1.0:
            raise ValueError(f"encoded target time differs by {offset:.6f} seconds")
        target_digest = sha256(partial)
        payload = {
            "schema_version": 1,
            "status": "PASS",
            "purpose": "repeated-day-restart-clock-transform",
            # This transform is the logical predecessor at the relabelled
            # boundary, despite sourcing its state from one cycle later.
            "end": target_time.isoformat(),
            "restart": {
                "path": str(target.resolve()),
                "sha256": target_digest,
                "size_bytes": partial.stat().st_size,
                "times": [target_time.isoformat()],
            },
            "provenance": {"source_commit": expected_source_commit},
            "checkpoint": str(target.resolve()),
            "checkpoint_time": target_time.isoformat(),
            "expected_time": target_time.isoformat(),
            "expected_source_commit": expected_source_commit,
            "sha256": target_digest,
            "encoded_time_offset_seconds": offset,
            "source_restart": {
                "path": str(source.resolve()),
                "report": str(source_report.resolve()),
                "sha256": source_digest,
                "encoded_time": source_time.isoformat(),
                "encoded_value": source_raw,
            },
            "clock_transform": {
                "target_encoded_time": actual_time.isoformat(),
                "target_encoded_value": actual_raw,
                "units": actual_units,
                "calendar": actual_calendar,
                "shift_seconds": (target_time - source_time).total_seconds(),
                "modified_variable": "time",
                "state_semantics": (
                    "All prognostic and diagnostic restart variables are copied; "
                    "only the scalar time coordinate is relabelled so the coupled "
                    "state traverses the same forcing and model calendar again."
                ),
            },
        }
        report_partial.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(partial, target)
        target_ready.touch()
        os.replace(report_partial, report_path)
        report_ready.touch()
        return payload
    finally:
        partial.unlink(missing_ok=True)
        report_partial.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-report", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--target-time", required=True)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    payload = publish_relabelled_restart(
        source=args.source,
        source_report=args.source_report,
        target=args.target,
        target_time=parse_time(args.target_time),
        expected_source_commit=args.expected_source_commit,
        report_path=args.report,
    )
    print(
        "repeated-day restart published: "
        f"time={payload['checkpoint_time']} path={payload['checkpoint']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
