#!/usr/bin/env python3
"""Validate and publish a HICAR restart-checkpoint inventory."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import netCDF4


REQUIRED_DIMENSIONS = (
    "lon_x",
    "lon_u",
    "lat_y",
    "lat_v",
    "level",
    "level_i",
    "time",
)

REQUIRED_VARIABLES = (
    "time",
    "u",
    "v",
    "w",
    "pressure",
    "potential_temperature",
    "qv",
    "qc",
    "qr",
    "qi",
    "qs",
    "qg",
    "soil_temperature",
    "soil_water_content",
    "snow_height",
    "canopy_water",
    "precipitation",
)


def json_scalar(value):
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


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


def timestamp(dataset: netCDF4.Dataset) -> tuple[datetime, float]:
    variable = dataset.variables["time"]
    if variable.size != 1:
        raise ValueError(f"restart time coordinate has {variable.size} records, expected 1")
    decoded = netCDF4.num2date(
        variable[:],
        variable.units,
        calendar=getattr(variable, "calendar", "standard"),
    )[0]
    exact = datetime(
        decoded.year,
        decoded.month,
        decoded.day,
        decoded.hour,
        decoded.minute,
        decoded.second,
        decoded.microsecond,
    )
    canonical = exact.replace(microsecond=0)
    return canonical, (exact - canonical).total_seconds()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--expected-time", required=True)
    parser.add_argument("--expected-source-commit")
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()

    expected_time = datetime.fromisoformat(args.expected_time)
    failures: list[str] = []
    dimensions: dict[str, int] = {}
    variables: list[str] = []
    attributes: dict[str, object] = {}
    checkpoint_time = None
    encoded_time_offset_seconds = None
    digest = None

    if not args.checkpoint.is_file() or args.checkpoint.stat().st_size == 0:
        failures.append(f"checkpoint is missing or empty: {args.checkpoint}")
    else:
        try:
            with netCDF4.Dataset(args.checkpoint) as dataset:
                dimensions = {
                    name: len(dimension)
                    for name, dimension in dataset.dimensions.items()
                }
                variables = sorted(dataset.variables)
                attributes = {
                    name: json_scalar(dataset.getncattr(name))
                    for name in ("git", "git_tag", "dt_seconds")
                    if name in dataset.ncattrs()
                }
                checkpoint_time, encoded_time_offset_seconds = timestamp(dataset)
        except Exception as exc:
            failures.append(f"cannot read checkpoint: {exc}")

    if dimensions:
        missing_dimensions = sorted(set(REQUIRED_DIMENSIONS) - set(dimensions))
        if missing_dimensions:
            failures.append(
                f"checkpoint lacks required dimensions: {missing_dimensions}"
            )
        if dimensions.get("time") != 1:
            failures.append("checkpoint must contain exactly one time record")
        if dimensions.get("level") != 80 or dimensions.get("level_i") != 81:
            failures.append("checkpoint does not contain the qualified 80-level grid")

    if variables:
        missing_variables = sorted(set(REQUIRED_VARIABLES) - set(variables))
        if missing_variables:
            failures.append(
                f"checkpoint lacks required restart variables: {missing_variables}"
            )

    if checkpoint_time is not None and checkpoint_time != expected_time:
        failures.append(
            "checkpoint time "
            f"{checkpoint_time.isoformat()} is not expected {expected_time.isoformat()}"
        )
    if (
        encoded_time_offset_seconds is not None
        and abs(encoded_time_offset_seconds) > 1.0
    ):
        failures.append("encoded restart time differs from its canonical second by >1 s")

    dt_seconds = attributes.get("dt_seconds")
    if dt_seconds is None or float(dt_seconds) <= 0:
        failures.append("checkpoint lacks a positive dt_seconds attribute")

    if args.expected_source_commit:
        short_commit = args.expected_source_commit[:8]
        source_text = " ".join(
            str(attributes.get(name, "")) for name in ("git", "git_tag")
        )
        if short_commit not in source_text:
            failures.append(
                "checkpoint source attributes do not contain expected commit "
                f"{short_commit}"
            )

    if not failures:
        digest = sha256(args.checkpoint)

    payload = {
        "schema_version": 1,
        "status": "PASS" if not failures else "FAIL",
        "checkpoint": str(args.checkpoint.resolve()),
        "size_bytes": (
            args.checkpoint.stat().st_size if args.checkpoint.is_file() else None
        ),
        "sha256": digest,
        "expected_time": expected_time.isoformat(),
        "checkpoint_time": (
            checkpoint_time.isoformat() if checkpoint_time is not None else None
        ),
        "encoded_time_offset_seconds": encoded_time_offset_seconds,
        "dimensions": dimensions,
        "variable_count": len(variables),
        "required_variable_count": len(REQUIRED_VARIABLES),
        "attributes": attributes,
        "expected_source_commit": args.expected_source_commit,
        "validation_scope": (
            "Header/schema/time/provenance plus whole-file checksum; this bounded "
            "inventory does not scan every restart-state value for finiteness."
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
    print(f"PASS: restart checkpoint published at {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
