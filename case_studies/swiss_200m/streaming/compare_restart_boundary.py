#!/usr/bin/env python3
"""Compare HICAR output immediately before and after a streamed restart."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import netCDF4
import numpy as np

from validate_model_chunk import ROUTINE_VARIABLES


def timestamp_index(dataset: netCDF4.Dataset, target: datetime) -> int:
    time = dataset.variables["time"]
    values = netCDF4.num2date(
        time[:], time.units, calendar=getattr(time, "calendar", "standard")
    )
    normalized = [
        datetime(value.year, value.month, value.day, value.hour, value.minute, value.second)
        for value in values
    ]
    try:
        return normalized.index(target)
    except ValueError as exc:
        raise ValueError(f"{target.isoformat()} is absent from {dataset.filepath()}") from exc


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
    parser.add_argument("--before", required=True, type=Path)
    parser.add_argument("--after", required=True, type=Path)
    parser.add_argument("--boundary", required=True, type=datetime.fromisoformat)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--atol", type=float, default=1.0e-6)
    parser.add_argument("--rtol", type=float, default=1.0e-6)
    parser.add_argument("--variable", action="append", dest="variables")
    args = parser.parse_args()

    variables = tuple(args.variables or ROUTINE_VARIABLES)
    failures: list[str] = []
    metrics: dict[str, dict] = {}
    try:
        before = netCDF4.Dataset(args.before)
        after = netCDF4.Dataset(args.after)
    except Exception as exc:
        payload = {"status": "FAIL", "failures": [f"cannot open outputs: {exc}"]}
        write_json_atomic(args.report, payload)
        return 1

    with before, after:
        try:
            before_index = timestamp_index(before, args.boundary)
            after_index = timestamp_index(after, args.boundary)
        except ValueError as exc:
            failures.append(str(exc))
            before_index = after_index = None

        for name in variables:
            if name not in before.variables or name not in after.variables:
                failures.append(f"boundary output is missing {name}")
                continue
            if before_index is None:
                continue
            left = np.ma.filled(before.variables[name][before_index], np.nan).astype(
                np.float64, copy=False
            )
            right = np.ma.filled(after.variables[name][after_index], np.nan).astype(
                np.float64, copy=False
            )
            if left.shape != right.shape:
                failures.append(f"{name} shape changed: {left.shape} != {right.shape}")
                continue
            finite = np.isfinite(left) & np.isfinite(right)
            if not finite.any():
                failures.append(f"{name} has no jointly finite boundary values")
                continue
            missing_mismatch = int(np.count_nonzero(np.isfinite(left) != np.isfinite(right)))
            delta = np.abs(left[finite] - right[finite])
            scale = np.abs(left[finite])
            allowed = args.atol + args.rtol * scale
            failures_count = int(np.count_nonzero(delta > allowed))
            metrics[name] = {
                "joint_finite_count": int(finite.sum()),
                "missing_mismatch_count": missing_mismatch,
                "tolerance_failure_count": failures_count,
                "max_abs": float(delta.max(initial=0.0)),
                "rmse": float(np.sqrt(np.mean(delta * delta))),
                "reference_abs_max": float(scale.max(initial=0.0)),
                "bitwise_equal": bool(np.array_equal(left, right, equal_nan=True)),
            }
            if missing_mismatch or failures_count:
                failures.append(
                    f"{name} differs at restart boundary: missing={missing_mismatch}, "
                    f"outside_tolerance={failures_count}"
                )

    payload = {
        "status": "PASS" if not failures else "FAIL",
        "before": str(args.before.resolve()),
        "after": str(args.after.resolve()),
        "boundary": args.boundary.isoformat(),
        "absolute_tolerance": args.atol,
        "relative_tolerance": args.rtol,
        "variables": metrics,
        "failures": failures,
    }
    write_json_atomic(args.report, payload)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    Path(f"{args.report}.ready").touch()
    print(f"PASS: restart boundary {args.boundary.isoformat()} matches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
