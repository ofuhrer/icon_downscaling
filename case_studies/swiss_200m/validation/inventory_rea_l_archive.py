#!/usr/bin/env python3
"""Inventory REA-L daily FDB databases without descending into their contents."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile


KEY = re.compile(
    r"^(?P<date>\d{8}):0000:reanl:rd:icon-rea-l-ch1:r001:cf$"
)


def daterange(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def inventory(root: Path, production_start: date, production_end: date) -> dict:
    dates = []
    ignored = []
    with os.scandir(root) as entries:
        for entry in entries:
            match = KEY.match(entry.name)
            if entry.is_dir(follow_symlinks=False) and match:
                dates.append(datetime.strptime(match.group("date"), "%Y%m%d").date())
            else:
                ignored.append(entry.name)
    dates.sort()
    if not dates:
        raise ValueError(f"no REA-L daily FDB keys found under {root}")
    available = set(dates)
    all_missing = [day for day in daterange(dates[0], dates[-1]) if day not in available]
    production_missing = [
        day for day in daterange(production_start, production_end) if day not in available
    ]
    production_days = (production_end - production_start).days + 1
    return {
        "status": "PASS" if not production_missing else "FAIL",
        "fdb_root": str(root.resolve()),
        "key_contract": "YYYYMMDD:0000:reanl:rd:icon-rea-l-ch1:r001:cf",
        "available": {
            "first_cycle": dates[0].isoformat(),
            "last_cycle": dates[-1].isoformat(),
            "daily_cycles": len(dates),
            "per_year": {
                str(year): count
                for year, count in sorted(Counter(day.year for day in dates).items())
            },
            "missing_cycles_between_first_and_last": [
                day.isoformat() for day in all_missing
            ],
        },
        "production_period": {
            "start": production_start.isoformat(),
            "end": production_end.isoformat(),
            "expected_daily_cycles": production_days,
            "available_daily_cycles": production_days - len(production_missing),
            "missing_cycles": [day.isoformat() for day in production_missing],
        },
        "ignored_root_entries": sorted(ignored),
    }


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fdb-root",
        type=Path,
        default=Path("/store_new/mch/msopr/rea-l-ch1/fdb/data"),
    )
    parser.add_argument("--production-start", default="2005-01-01")
    parser.add_argument("--production-end", default="2024-12-31")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    start = date.fromisoformat(args.production_start)
    end = date.fromisoformat(args.production_end)
    if end < start:
        raise SystemExit("production end precedes start")
    payload = inventory(args.fdb_root, start, end)
    write_json_atomic(args.report, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
