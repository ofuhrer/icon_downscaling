#!/usr/bin/env python3
"""Publish a deterministic REA-L/HICAR streaming-chunk plan and forcing list."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile


TIME_FORMAT = "%Y-%m-%dT%H:%M:%S"


def records_for_period(start: datetime, end: datetime, forcing_dir: Path) -> list[dict]:
    if start.minute or start.second or start.microsecond:
        raise ValueError("start must be on an exact hour")
    if end.minute or end.second or end.microsecond:
        raise ValueError("end must be on an exact hour")
    if end <= start:
        raise ValueError("end must be later than start")
    records = []
    valid = start
    while valid <= end:
        # At midnight choose the new cycle's analysis (step 0), not the
        # previous cycle's almost-identical step 24. This gives one source
        # record per valid time and avoids duplicate timestamps.
        cycle = valid.replace(hour=0)
        step = valid.hour
        stamp = valid.strftime("%Y%m%d_%H%M")
        path = forcing_dir / f"rea_l_hicar_{stamp}.nc"
        records.append(
            {
                "index": len(records),
                "valid_time": valid.strftime(TIME_FORMAT),
                "cycle_date": cycle.strftime("%Y%m%d"),
                "cycle_time": "0000",
                "step_hours": step,
                "forcing_file": str(path.resolve()),
                "ready_marker": str(Path(f"{path.resolve()}.ready")),
            }
        )
        valid += timedelta(hours=1)
    return records


def write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as stream:
        stream.write(content)
        temporary = Path(stream.name)
    os.replace(temporary, path)


def publish(path: Path, content: str) -> None:
    ready = Path(f"{path}.ready")
    if path.exists() or ready.exists():
        if path.is_file() and ready.is_file() and path.read_text() == content:
            print(f"already published: {path}")
            return
        raise ValueError(f"refusing to replace non-identical publication: {path}")
    write_atomic(path, content)
    ready.touch()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="ISO timestamp on an exact hour")
    parser.add_argument("--end", required=True, help="ISO timestamp on an exact hour")
    parser.add_argument("--chunk-id", required=True)
    parser.add_argument("--chunk-root", type=Path, required=True)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--forcing-list", type=Path)
    parser.add_argument("--producer-concurrency", type=int, default=4)
    args = parser.parse_args()
    if args.producer_concurrency < 1:
        raise SystemExit("--producer-concurrency must be positive")
    start = datetime.fromisoformat(args.start)
    end = datetime.fromisoformat(args.end)
    chunk_root = args.chunk_root.resolve()
    forcing_dir = chunk_root / "forcing"
    plan_path = (args.plan or chunk_root / "chunk_plan.json").resolve()
    list_path = (args.forcing_list or chunk_root / "forcing_list.txt").resolve()
    records = records_for_period(start, end, forcing_dir)
    forcing_list = "".join(f'"{record["forcing_file"]}"\n' for record in records)
    payload = {
        "schema_version": 1,
        "status": "PLANNED",
        "chunk_id": args.chunk_id,
        "start": start.strftime(TIME_FORMAT),
        "end": end.strftime(TIME_FORMAT),
        "hours": int((end - start).total_seconds() // 3600),
        "record_count": len(records),
        "producer_concurrency": args.producer_concurrency,
        "cycle_policy": (
            "For each valid hour use that UTC date's 00 UTC cycle and step equal "
            "to the valid hour; use next-cycle step 0 at midnight and never "
            "previous-cycle step 24."
        ),
        "transient_policy": (
            "Native GRIB and converter work are job-local; forcing NetCDF is "
            "retired only after validated model output and restart publication."
        ),
        "chunk_root": str(chunk_root),
        "forcing_list": str(list_path),
        "records": records,
    }
    plan_content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    publish(plan_path, plan_content)
    publish(list_path, forcing_list)
    print(f"chunk plan published: plan={plan_path} list={list_path} records={len(records)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
