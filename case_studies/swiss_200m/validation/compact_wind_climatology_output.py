#!/usr/bin/env python3
"""Create and verify an atomic losslessly compressed wind-climatology file."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import NamedTemporaryFile

import netCDF4
import numpy as np

from validate_wind_climatology_output import VARIABLES, times, validate_file


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def variables_equal(left_path: Path, right_path: Path) -> list[str]:
    failures: list[str] = []
    with netCDF4.Dataset(left_path) as left, netCDF4.Dataset(right_path) as right:
        if set(left.variables) != set(right.variables):
            failures.append("variable inventories differ")
            return failures
        if times(left) != times(right):
            failures.append("decoded time coordinates differ")
        for name in left.variables:
            left_variable = left.variables[name]
            right_variable = right.variables[name]
            if left_variable.shape != right_variable.shape:
                failures.append(f"shape differs for {name}")
                continue
            if "time" in left_variable.dimensions:
                time_axis = left_variable.dimensions.index("time")
                for index in range(left_variable.shape[time_axis]):
                    selection: list[object] = [slice(None)] * left_variable.ndim
                    selection[time_axis] = index
                    left_values = np.ma.asarray(left_variable[tuple(selection)])
                    right_values = np.ma.asarray(right_variable[tuple(selection)])
                    if not (
                        np.array_equal(
                            np.ma.getmaskarray(left_values),
                            np.ma.getmaskarray(right_values),
                        )
                        and np.array_equal(
                            np.ma.getdata(left_values),
                            np.ma.getdata(right_values),
                            equal_nan=True,
                        )
                    ):
                        failures.append(f"values differ for {name} at time index {index}")
                        break
            else:
                left_values = np.ma.asarray(left_variable[:])
                right_values = np.ma.asarray(right_variable[:])
                if not (
                    np.array_equal(
                        np.ma.getmaskarray(left_values), np.ma.getmaskarray(right_values)
                    )
                    and np.array_equal(
                        np.ma.getdata(left_values),
                        np.ma.getdata(right_values),
                        equal_nan=True,
                    )
                ):
                    failures.append(f"values differ for {name}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--deflate-level", type=int, default=1)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if not 1 <= args.deflate_level <= 9:
        raise SystemExit("--deflate-level must be in 1..9")
    if args.output.exists():
        raise SystemExit(f"refusing to replace existing output: {args.output}")
    nccopy = shutil.which("nccopy")
    if nccopy is None:
        raise SystemExit("nccopy is required")
    input_validation, _ = validate_file(args.input)
    if input_validation["failures"]:
        raise SystemExit("input contract validation failed: " + "; ".join(input_validation["failures"]))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.partial")
    if temporary.exists():
        raise SystemExit(f"refusing stale partial output: {temporary}")
    try:
        subprocess.run(
            [nccopy, "-4", "-s", "-d", str(args.deflate_level), str(args.input), str(temporary)],
            check=True,
        )
        output_validation, _ = validate_file(temporary)
        failures = list(output_validation["failures"])
        failures.extend(variables_equal(args.input, temporary))
        if failures:
            raise RuntimeError("compressed copy validation failed: " + "; ".join(failures))
        temporary.replace(args.output)
    finally:
        temporary.unlink(missing_ok=True)

    input_size = args.input.stat().st_size
    output_size = args.output.stat().st_size
    report = {
        "schema_version": 1,
        "status": "pass",
        "input": str(args.input.resolve()),
        "output": str(args.output.resolve()),
        "deflate_level": args.deflate_level,
        "shuffle": True,
        "verified_variables": list(VARIABLES),
        "input_bytes": input_size,
        "output_bytes": output_size,
        "compression_ratio": input_size / output_size,
        "input_sha256": sha256(args.input),
        "output_sha256": sha256(args.output),
    }
    report_path = args.report or args.output.with_suffix(args.output.suffix + ".compression.json")
    with NamedTemporaryFile("w", dir=report_path.parent, delete=False) as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")
        report_temporary = Path(stream.name)
    report_temporary.replace(report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
