#!/usr/bin/env python3
"""Write an atomically published, timestamp-sorted HICAR forcing-file list."""

import argparse
from datetime import datetime, timedelta
import os
from pathlib import Path
import re
import shutil
import subprocess


def published(path):
    if not path.is_file() or not Path(str(path) + ".ready").is_file():
        raise SystemExit("forcing input is not published: %s" % path)


def timestamp(path):
    try:
        import netCDF4
    except ImportError:
        if not shutil.which("ncdump") or not shutil.which("ncks"):
            raise SystemExit("netCDF4 or ncdump+ncks is required for forcing timestamps")
        header = subprocess.check_output(["ncdump", "-h", str(path)], universal_newlines=True)
        match = re.search(r'time:units = "([^"]+)"', header)
        if not match:
            raise SystemExit("time units missing: %s" % path)
        unit_match = re.fullmatch(r"(seconds|minutes|hours|days) since (.+)", match.group(1))
        if not unit_match:
            raise SystemExit("unsupported time units: %s" % match.group(1))
        origin = datetime.strptime(unit_match.group(2).strip(), "%Y-%m-%d %H:%M:%S")
        values = subprocess.check_output(["ncks", "-H", "-C", "-v", "time", "-s", "%g\\n", str(path)], universal_newlines=True).split()
        if not values:
            raise SystemExit("no time record: %s" % path)
        return origin + timedelta(**{unit_match.group(1): float(values[-1])})
    with netCDF4.Dataset(str(path)) as ds:
        time = ds.variables.get("time")
        if time is None or not time.units or time.size == 0:
            raise SystemExit("invalid time coordinate: %s" % path)
        value = netCDF4.num2date(time[-1], time.units, calendar=getattr(time, "calendar", "standard"))
        return datetime(value.year, value.month, value.day, value.hour, value.minute, value.second)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--expected-start", help="Require this ISO first timestamp.")
    parser.add_argument("--expected-end", help="Require this ISO final timestamp.")
    parser.add_argument("--expected-interval-seconds", type=int, default=3600)
    parser.add_argument("forcing", nargs="+", type=Path)
    args = parser.parse_args()
    if args.output.exists() and not args.replace:
        raise SystemExit("refusing to overwrite forcing list: %s" % args.output)
    entries = []
    for path in args.forcing:
        path = path.resolve()
        published(path)
        entries.append((timestamp(path), path))
    entries.sort()
    times = [entry[0] for entry in entries]
    if len(set(times)) != len(times):
        raise SystemExit("forcing timestamps are not unique")
    if args.expected_interval_seconds <= 0:
        raise SystemExit("--expected-interval-seconds must be positive")
    interval = timedelta(seconds=args.expected_interval_seconds)
    if any(right - left != interval for left, right in zip(times, times[1:])):
        raise SystemExit("forcing timestamps are not continuous at the expected interval")
    if args.expected_start and times[0] != datetime.fromisoformat(args.expected_start.replace("T", " ")):
        raise SystemExit("first forcing timestamp does not match --expected-start")
    if args.expected_end and times[-1] != datetime.fromisoformat(args.expected_end.replace("T", " ")):
        raise SystemExit("last forcing timestamp does not match --expected-end")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    marker = Path(str(args.output) + ".ready")
    if marker.exists():
        marker.unlink()
    temporary = args.output.with_name(".%s.partial.%s" % (args.output.name, os.getpid()))
    temporary.write_text("".join('"%s"\n' % path for _, path in entries))
    os.replace(str(temporary), str(args.output))
    marker.touch()
    for value, path in entries:
        print("%s %s" % (value.isoformat(sep=" "), path))


if __name__ == "__main__":
    main()
