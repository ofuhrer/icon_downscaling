#!/usr/bin/env python3
"""Merge consecutive 24-hour native REA-L station-reference CSV files."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
import math
import os
from pathlib import Path


REQUIRED_COLUMNS = {
    "valid_time",
    "station_key",
    "precipitation_interval_ref",
}


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def read_day(path: Path) -> tuple[list[str], dict[tuple[datetime, str], dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None or not REQUIRED_COLUMNS <= set(reader.fieldnames):
            raise ValueError(f"{path}: missing required columns")
        rows: dict[tuple[datetime, str], dict[str, str]] = {}
        for line_number, row in enumerate(reader, start=2):
            valid = parse_time(row["valid_time"])
            station = row["station_key"].strip()
            if not station:
                raise ValueError(f"{path}:{line_number}: empty station_key")
            key = (valid, station)
            if key in rows:
                raise ValueError(f"{path}:{line_number}: duplicate {valid}/{station}")
            rows[key] = row
    if not rows:
        raise ValueError(f"{path}: no records")
    return list(reader.fieldnames), rows


def validate_day(
    path: Path, rows: dict[tuple[datetime, str], dict[str, str]]
) -> tuple[datetime, datetime, set[str]]:
    times = sorted({key[0] for key in rows})
    if len(times) != 25 or times[-1] - times[0] != timedelta(hours=24):
        raise ValueError(f"{path}: expected 25 inclusive hourly endpoints spanning 24 hours")
    if times != [times[0] + timedelta(hours=index) for index in range(25)]:
        raise ValueError(f"{path}: endpoints are not exact consecutive hours")
    stations = {key[1] for key in rows}
    for valid in times:
        present = {station for time, station in rows if time == valid}
        if present != stations:
            raise ValueError(f"{path}: station set changes at {valid.isoformat()}")
    return times[0], times[-1], stations


def merge_days(
    paths: list[Path], start: datetime, end: datetime
) -> tuple[list[str], list[dict[str, str]], int]:
    if not paths:
        raise ValueError("at least one daily input is required")
    if end <= start or (end - start).total_seconds() % 86400:
        raise ValueError("requested interval must span a positive whole number of days")
    if len(paths) != int((end - start).total_seconds() // 86400):
        raise ValueError("daily input count does not match the requested interval")

    header: list[str] | None = None
    merged: dict[tuple[datetime, str], dict[str, str]] = {}
    expected_stations: set[str] | None = None
    previous_end: datetime | None = None
    for path in paths:
        day_header, rows = read_day(path)
        day_start, day_end, stations = validate_day(path, rows)
        if header is None:
            header = day_header
        elif day_header != header:
            raise ValueError(f"{path}: CSV columns/order differ from previous day")
        if expected_stations is None:
            expected_stations = stations
        elif stations != expected_stations:
            raise ValueError(f"{path}: station set differs from previous day")
        if previous_end is None:
            if day_start != start:
                raise ValueError(f"{path}: first daily endpoint does not equal requested start")
        elif day_start != previous_end:
            raise ValueError(f"{path}: daily windows are not consecutive")

        for key, row in rows.items():
            existing = merged.get(key)
            if existing is None:
                merged[key] = row
                continue
            for column in header:
                if column == "precipitation_interval_ref":
                    continue
                if existing[column] != row[column]:
                    raise ValueError(
                        f"{path}: duplicate join record differs for {key} column {column}"
                    )
            right_precipitation = float(row["precipitation_interval_ref"])
            if not math.isfinite(right_precipitation) or abs(right_precipitation) > 1.0e-12:
                raise ValueError(
                    f"{path}: next-day baseline precipitation is not zero for {key}"
                )
            # Retain the prior day's ending-hour interval at the shared midnight.
        previous_end = day_end

    if previous_end != end or header is None or expected_stations is None:
        raise ValueError("daily inputs do not end at the requested endpoint")
    expected_times = [
        start + timedelta(hours=index)
        for index in range(int((end - start).total_seconds() // 3600) + 1)
    ]
    if {key[0] for key in merged} != set(expected_times):
        raise ValueError("merged endpoints do not exactly cover the requested interval")
    for valid in expected_times:
        present = {station for time, station in merged if time == valid}
        if present != expected_stations:
            raise ValueError(f"merged station set is incomplete at {valid.isoformat()}")
    ordered = [merged[(valid, station)] for valid in expected_times for station in sorted(expected_stations)]
    return header, ordered, len(expected_stations)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, action="append", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    start = parse_time(args.start)
    end = parse_time(args.end)
    header, rows, station_count = merge_days(args.input, start, end)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    partial = args.output.with_name(f".{args.output.name}.partial.{os.getpid()}")
    ready = Path(f"{args.output}.ready")
    if args.output.exists() or ready.exists():
        raise SystemExit(f"refusing to overwrite existing output/marker: {args.output}")
    try:
        with partial.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=header)
            writer.writeheader()
            writer.writerows(rows)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(partial, args.output)
        ready.touch()
    finally:
        partial.unlink(missing_ok=True)
    print(
        f"PASS records={len(rows)} sites={station_count} "
        f"start={start.isoformat()} end={end.isoformat()} output={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
