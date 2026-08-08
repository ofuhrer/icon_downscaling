#!/usr/bin/env python3
"""Verify that one HICAR segment is complete and uses the selected R&D physics."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path

import netCDF4
import numpy as np


def decode_times(path: Path) -> list[datetime]:
    with netCDF4.Dataset(path) as dataset:
        if "time" not in dataset.variables or dataset["time"].size == 0:
            raise ValueError(f"{path}: missing time coordinate")
        variable = dataset["time"]
        values = netCDF4.num2date(
            variable[:], variable.units,
            calendar=getattr(variable, "calendar", "standard"),
        )
        return [
            datetime(value.year, value.month, value.day, value.hour, value.minute, value.second)
            for value in values
        ]


def core_values(variable: netCDF4.Variable) -> np.ndarray:
    values = np.ma.asarray(variable[:]).filled(np.nan).astype(np.float64, copy=False)
    if values.ndim >= 2 and values.shape[-1] > 6 and values.shape[-2] > 6:
        values = values[(slice(None),) * (values.ndim - 2) + (slice(3, -3), slice(3, -3))]
    return values


def require_finite_outputs(paths: list[Path]) -> None:
    failures: list[str] = []
    for path in paths:
        with netCDF4.Dataset(path) as dataset:
            for name, variable in dataset.variables.items():
                if "time" not in variable.dimensions or variable.dtype.kind != "f":
                    continue
                values = core_values(variable)
                count = int(values.size - np.isfinite(values).sum())
                if count:
                    failures.append(f"{path.name}:{name}={count}")
    if failures:
        raise SystemExit("non-finite model output in domain core: " + ", ".join(failures[:20]))


def require_restart_state(dataset: netCDF4.Dataset) -> None:
    bounds = {
        "potential_temperature": (150.0, 500.0),
        "temperature": (150.0, 350.0),
        "pressure": (5_000.0, 120_000.0),
        "density": (0.05, 2.0),
        "qv": (0.0, 0.1),
        "u": (-200.0, 200.0),
        "v": (-200.0, 200.0),
        "w": (-200.0, 200.0),
    }
    failures: dict[str, str] = {}
    for name, (lower, upper) in bounds.items():
        if name not in dataset.variables:
            failures[name] = "missing"
            continue
        values = core_values(dataset[name])
        finite = np.isfinite(values)
        if not finite.all():
            failures[name] = f"{int(values.size - finite.sum())} non-finite values"
        elif values.min() < lower or values.max() > upper:
            failures[name] = f"range [{values.min():.6g}, {values.max():.6g}]"
    if failures:
        raise SystemExit("invalid terminal restart state: " + json.dumps(failures, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--restart", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output-interval", type=int, required=True)
    parser.add_argument("--forcing-list", type=Path, required=True)
    parser.add_argument("--boundary-list", type=Path, required=True)
    args = parser.parse_args()

    start = datetime.fromisoformat(args.start.replace("T", " ").replace("Z", ""))
    end = datetime.fromisoformat(args.end.replace("T", " ").replace("Z", ""))
    if end <= start or args.output_interval <= 0:
        raise SystemExit("invalid segment interval")

    output_files = sorted(args.output_dir.glob("*.nc"))
    if not output_files:
        raise SystemExit("segment has no NetCDF output")
    output_times = sorted({value for path in output_files for value in decode_times(path)})
    expected = []
    value = start
    while value <= end:
        expected.append(value)
        value += timedelta(seconds=args.output_interval)
    if output_times != expected:
        raise SystemExit(
            f"output times {output_times[0]}..{output_times[-1]} ({len(output_times)}) "
            f"do not equal expected {expected[0]}..{expected[-1]} ({len(expected)})"
        )
    require_finite_outputs(output_files)

    restart_times = decode_times(args.restart)
    if restart_times != [end]:
        raise SystemExit(f"terminal restart time is {restart_times}, expected {[end]}")
    with netCDF4.Dataset(args.restart) as restart:
        require_restart_state(restart)
        required_physics = {
            "physics.wind": "variational solver",
            "physics.mp": "morrison",
            "physics.pbl": "ysu",
            "physics.lsm": "noahmp",
            "physics.rad": "RRTMGP",
            "wind.Sx": "T",
            "wind.wind_solver_iterations": "2500",
            "adv.advect_density": "T",
            "domain.nz": "80",
        }
        mismatches = {
            name: {"actual": str(getattr(restart, name, "")), "expected": expected_value}
            for name, expected_value in required_physics.items()
            if str(getattr(restart, name, "")) != expected_value
        }
    if mismatches:
        raise SystemExit("restart physics mismatch: " + json.dumps(mismatches, sort_keys=True))
    with netCDF4.Dataset(args.restart) as restart:
        numeric_physics = {
            "wind.alpha_const": 1.0,
            "rad.update_interval_rad": 600.0,
            "domain.height_lowest_level": 26.0,
        }
        numeric_mismatches = {
            name: str(getattr(restart, name, ""))
            for name, expected_value in numeric_physics.items()
            if abs(float(getattr(restart, name, "nan")) - expected_value) > 1.0e-8
        }
    if numeric_mismatches:
        raise SystemExit(
            "restart numeric physics mismatch: " + json.dumps(numeric_mismatches, sort_keys=True)
        )

    forcing = [line.strip().strip('"') for line in args.forcing_list.read_text().splitlines() if line.strip()]
    boundaries = [line.strip().strip('"') for line in args.boundary_list.read_text().splitlines() if line.strip()]
    expected_inputs = int((end - start).total_seconds() // 3600) + 1
    if len(forcing) != expected_inputs or len(boundaries) != expected_inputs:
        raise SystemExit("forcing/LBC lists do not contain every hourly bracket endpoint")

    print(json.dumps({
        "start": args.start,
        "end": args.end,
        "output_files": len(output_files),
        "output_times": len(output_times),
        "restart": str(args.restart),
        "forcing_records": len(forcing),
        "boundary_records": len(boundaries),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
