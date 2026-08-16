#!/usr/bin/env python3
"""Validate HICAR hourly wind statistics and continuous/restart equivalence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import NamedTemporaryFile

import netCDF4
import numpy as np


AGL_VARIABLES = (
    "u_agl_mean_1h",
    "v_agl_mean_1h",
    "wind_speed_agl_mean_1h",
    "wind_speed_agl_10min_max_1h",
)
SURFACE_VARIABLES = (
    "u10m_mean_1h",
    "v10m_mean_1h",
    "wind_speed_10m_mean_1h",
    "wind_speed_10m_10min_max_1h",
)
VARIABLES = AGL_VARIABLES + SURFACE_VARIABLES
HEIGHTS_M = np.asarray((50, 75, 100, 125, 150, 200, 250), dtype=float)
TIME_TOLERANCE_SECONDS = 0.5
WIND_INVARIANT_TOLERANCE_M_S = 5.0e-4


def atomic_json_dump(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", dir=path.parent, delete=False) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    temporary.replace(path)


def times(dataset: netCDF4.Dataset) -> list[datetime]:
    variable = dataset.variables["time"]
    decoded = netCDF4.num2date(
        variable[:],
        units=variable.units,
        calendar=getattr(variable, "calendar", "standard"),
        only_use_cftime_datetimes=False,
        only_use_python_datetimes=True,
    )
    return [
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
        for value in decoded
    ]


def time_slice(variable: netCDF4.Variable, index: int) -> np.ma.MaskedArray:
    selection: list[object] = [slice(None)] * variable.ndim
    selection[variable.dimensions.index("time")] = index
    return np.ma.asarray(variable[tuple(selection)])


def equivalent(left: np.ma.MaskedArray, right: np.ma.MaskedArray) -> bool:
    left_mask = np.ma.getmaskarray(left)
    right_mask = np.ma.getmaskarray(right)
    if not np.array_equal(left_mask, right_mask):
        return False
    valid = ~left_mask
    return np.array_equal(np.asarray(left)[valid], np.asarray(right)[valid])


def validate_file(path: Path) -> tuple[dict, list[datetime]]:
    failures: list[str] = []
    with netCDF4.Dataset(path) as dataset:
        missing = sorted(set(("time", "height_agl", *VARIABLES)) - set(dataset.variables))
        if missing:
            return {"path": str(path), "failures": [f"missing variables: {missing}"]}, []
        decoded = times(dataset)
        if len(decoded) < 2:
            failures.append("fewer than two output records")
        offsets = []
        for value in decoded:
            seconds = (
                value.hour * 3600
                + value.minute * 60
                + value.second
                + value.microsecond / 1.0e6
            )
            remainder = seconds % 3600.0
            offsets.append(min(remainder, 3600.0 - remainder))
        if any(offset > TIME_TOLERANCE_SECONDS for offset in offsets):
            failures.append("output timestamps are not on whole UTC hours")
        if any(
            abs((later - earlier).total_seconds() - 3600.0)
            > TIME_TOLERANCE_SECONDS
            for earlier, later in zip(decoded, decoded[1:])
        ):
            failures.append("output cadence is not exactly hourly")
        heights = np.asarray(dataset.variables["height_agl"][:], dtype=float)
        if heights.shape != HEIGHTS_M.shape or not np.allclose(
            heights, HEIGHTS_M, rtol=0.0, atol=1.0e-6
        ):
            failures.append(f"unexpected height_agl values: {heights.tolist()}")

        first_missing = {}
        later_finite_fraction = {}
        for name in VARIABLES:
            variable = dataset.variables[name]
            if "time" not in variable.dimensions:
                failures.append(f"{name} has no time dimension")
                continue
            first = time_slice(variable, 0)
            first_missing[name] = float(np.mean(np.ma.getmaskarray(first)))
            if first_missing[name] != 1.0:
                failures.append(f"cold-start {name} is not entirely missing")
            later = time_slice(variable, len(decoded) - 1)
            finite = np.isfinite(np.asarray(later)) & ~np.ma.getmaskarray(later)
            later_finite_fraction[name] = float(np.mean(finite))
            if not np.any(finite):
                failures.append(f"terminal {name} has no finite values")

        invariant_counts = {}
        for prefix, u_name, v_name, mean_name, maximum_name in (
            (
                "agl",
                "u_agl_mean_1h",
                "v_agl_mean_1h",
                "wind_speed_agl_mean_1h",
                "wind_speed_agl_10min_max_1h",
            ),
            (
                "10m",
                "u10m_mean_1h",
                "v10m_mean_1h",
                "wind_speed_10m_mean_1h",
                "wind_speed_10m_10min_max_1h",
            ),
        ):
            u = time_slice(dataset.variables[u_name], len(decoded) - 1)
            v = time_slice(dataset.variables[v_name], len(decoded) - 1)
            mean = time_slice(dataset.variables[mean_name], len(decoded) - 1)
            maximum = time_slice(dataset.variables[maximum_name], len(decoded) - 1)
            mask = (
                np.ma.getmaskarray(u)
                | np.ma.getmaskarray(v)
                | np.ma.getmaskarray(mean)
                | np.ma.getmaskarray(maximum)
            )
            valid = ~mask
            vector_speed = np.hypot(np.asarray(u), np.asarray(v))
            scalar_violations = int(
                np.count_nonzero(
                    valid
                    & (
                        np.asarray(mean) + WIND_INVARIANT_TOLERANCE_M_S
                        < vector_speed
                    )
                )
            )
            maximum_violations = int(
                np.count_nonzero(
                    valid
                    & (
                        np.asarray(maximum) + WIND_INVARIANT_TOLERANCE_M_S
                        < np.asarray(mean)
                    )
                )
            )
            invariant_counts[prefix] = {
                "finite_count": int(np.count_nonzero(valid)),
                "scalar_mean_below_vector_mean_count": scalar_violations,
                "ten_minute_max_below_hourly_mean_count": maximum_violations,
            }
            if scalar_violations or maximum_violations:
                failures.append(f"{prefix} wind statistical invariants failed")
    return {
        "path": str(path.resolve()),
        "failures": failures,
        "times": [value.isoformat() for value in decoded],
        "cold_start_missing_fraction": first_missing,
        "terminal_finite_fraction": later_finite_fraction,
        "invariants": invariant_counts,
    }, decoded


def compare_restart(continuous: Path, restarted: Path) -> dict:
    failures: list[str] = []
    with netCDF4.Dataset(continuous) as left, netCDF4.Dataset(restarted) as right:
        left_times = times(left)
        right_times = times(right)
        common = sorted(set(left_times) & set(right_times))
        if not common:
            return {"failures": ["continuous and restarted output have no common time"]}
        terminal = common[-1]
        left_index = left_times.index(terminal)
        right_index = right_times.index(terminal)
        matches = {}
        for name in VARIABLES:
            matches[name] = equivalent(
                time_slice(left.variables[name], left_index),
                time_slice(right.variables[name], right_index),
            )
            if not matches[name]:
                failures.append(f"restart mismatch for {name} at {terminal.isoformat()}")
    return {
        "terminal_common_time": terminal.isoformat(),
        "bitwise_equal": matches,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--continuous", type=Path, required=True)
    parser.add_argument("--restarted", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    continuous, _ = validate_file(args.continuous)
    restart = compare_restart(args.continuous, args.restarted) if args.restarted else None
    failures = list(continuous["failures"])
    if restart is not None:
        failures.extend(restart["failures"])
    report = {
        "schema_version": 1,
        "status": "pass" if not failures else "fail",
        "failures": failures,
        "tolerances": {
            "timestamp_seconds": TIME_TOLERANCE_SECONDS,
            "wind_invariant_m_s": WIND_INVARIANT_TOLERANCE_M_S,
        },
        "continuous": continuous,
        "restart_comparison": restart,
    }
    atomic_json_dump(args.report, report)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
