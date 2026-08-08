#!/usr/bin/env python3
"""Validate a HICAR run that crosses a sparse-LBC bracket turnover."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re

import netCDF4
import numpy as np


FIELD_BOUNDS = {
    "temperature": (150.0, 350.0),
    "potential_temperature": (150.0, 500.0),
    "pressure": (1.0, 120000.0),
    "density": (0.0, 5.0),
    "qv": (0.0, 1.0),
    "qc": (0.0, 1.0),
    "qi": (0.0, 1.0),
    "u": (-200.0, 200.0),
    "v": (-200.0, 200.0),
    "w": (-200.0, 200.0),
    "w_grid": (-200.0, 200.0),
}


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _field_range(variable: netCDF4.Variable) -> tuple[float, float]:
    minimum = np.inf
    maximum = -np.inf
    if not variable.dimensions or variable.dimensions[0] != "time":
        raise ValueError(f"{variable.name} must have time as its leading dimension")
    for record in range(variable.shape[0]):
        raw = variable[record, ...]
        if np.ma.isMaskedArray(raw) and np.ma.getmaskarray(raw).any():
            raise ValueError(f"{variable.name} contains masked or missing values")
        values = np.asarray(raw, dtype=np.float64)
        if not np.isfinite(values).all():
            raise ValueError(f"{variable.name} contains non-finite values")
        minimum = min(minimum, float(values.min()))
        maximum = max(maximum, float(values.max()))
    return minimum, maximum


def validate(
    output: pathlib.Path,
    model_log: pathlib.Path,
    expected_records: int,
    expected_interval_seconds: float,
) -> dict[str, object]:
    log_text = model_log.read_text()
    if "Sparse target-grid LBC runtime enabled" not in log_text:
        raise ValueError("model log does not show sparse-LBC initialization")
    if not re.search(
        r"Sparse LBC bracket advanced:\s*left=\s*2\s*right=\s*3", log_text
    ):
        raise ValueError("model log does not show the expected 01Z bracket turnover")

    report: dict[str, object] = {
        "output": str(output),
        "model_log": str(model_log),
        "status": "PASS",
        "time_records": expected_records,
        "time_interval_seconds": expected_interval_seconds,
        "variables": {},
    }
    with netCDF4.Dataset(output) as dataset:
        if (
            "time" not in dataset.dimensions
            or len(dataset.dimensions["time"]) != expected_records
        ):
            actual = (
                len(dataset.dimensions["time"])
                if "time" in dataset.dimensions
                else 0
            )
            raise ValueError(f"expected {expected_records} output records, found {actual}")
        time_variable = dataset.variables["time"]
        decoded = netCDF4.num2date(
            time_variable[:],
            units=time_variable.units,
            calendar=getattr(time_variable, "calendar", "standard"),
        )
        intervals = np.asarray(
            [
                (decoded[index] - decoded[index - 1]).total_seconds()
                for index in range(1, len(decoded))
            ],
            dtype=np.float64,
        )
        if intervals.size and not np.allclose(
            intervals, expected_interval_seconds, rtol=0.0, atol=1.0e-3
        ):
            raise ValueError(f"output cadence differs from {expected_interval_seconds} seconds")

        variables: dict[str, object] = {}
        for name, (lower, upper) in FIELD_BOUNDS.items():
            if name not in dataset.variables:
                raise ValueError(f"missing required runtime output variable {name}")
            minimum, maximum = _field_range(dataset.variables[name])
            if minimum < lower or maximum > upper:
                raise ValueError(
                    f"{name} range {minimum}..{maximum} lies outside {lower}..{upper}"
                )
            variables[name] = {
                "minimum": minimum,
                "maximum": maximum,
                "accepted_bounds": [lower, upper],
            }
        report["variables"] = variables
        report["hicar_git_commit"] = getattr(dataset, "git", "")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--model-log", required=True, type=pathlib.Path)
    parser.add_argument("--expected-records", type=int, default=13)
    parser.add_argument("--expected-interval-seconds", type=float, default=600.0)
    parser.add_argument("--report", required=True, type=pathlib.Path)
    args = parser.parse_args()

    report = validate(
        args.output,
        args.model_log,
        args.expected_records,
        args.expected_interval_seconds,
    )
    report["validator_sha256"] = _sha256(pathlib.Path(__file__))
    ready = pathlib.Path(f"{args.report}.ready")
    ready.unlink(missing_ok=True)
    partial = pathlib.Path(f"{args.report}.partial")
    partial.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    partial.replace(args.report)
    ready.touch()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
