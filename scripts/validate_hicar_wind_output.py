#!/usr/bin/env python3
"""Validate HICAR wind-run NetCDF output without loading national fields whole."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
from pathlib import Path
import tempfile
from typing import Iterator

import netCDF4
import numpy as np


REQUIRED_VARIABLES = (
    "u",
    "v",
    "w",
    "w_grid",
    "pressure",
    "temperature",
    "qv",
    "density",
    "z",
    "jacobian",
)

# Broad engineering bounds. They reject corrupt or explosively unstable output;
# they are not a substitute for case-specific scientific evaluation.
DEFAULT_BOUNDS = {
    "u": (-150.0, 150.0),
    "v": (-150.0, 150.0),
    "w": (-100.0, 100.0),
    "w_grid": (-100.0, 100.0),
    "pressure": (1_000.0, 110_000.0),
    "temperature": (150.0, 340.0),
    "potential_temperature": (150.0, 500.0),
    "qv": (-1.0e-8, 0.1),
    "density": (0.01, 2.0),
    "precipitation": (-1.0e-5, 5_000.0),
    "z": (-500.0, 30_000.0),
    "jacobian": (1.0e-6, 10.0),
    "wind_alpha": (0.0, 2.1),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _chunk_shape(shape: tuple[int, ...], max_elements: int) -> tuple[int, ...]:
    chunks = list(shape)
    while int(np.prod(chunks, dtype=np.int64)) > max_elements:
        axis = max(range(len(chunks)), key=chunks.__getitem__)
        chunks[axis] = max(1, (chunks[axis] + 1) // 2)
    return tuple(chunks)


def iter_variable_chunks(
    variable: netCDF4.Variable,
    *,
    max_elements: int,
) -> Iterator[np.ndarray]:
    if variable.ndim == 0:
        yield np.ma.asarray(variable[...]).filled(np.nan)
        return
    chunks = _chunk_shape(tuple(variable.shape), max_elements)
    starts = [range(0, extent, chunk) for extent, chunk in zip(variable.shape, chunks)]
    for origin in itertools.product(*starts):
        selection = tuple(
            slice(start, min(start + chunk, extent))
            for start, chunk, extent in zip(origin, chunks, variable.shape)
        )
        yield np.ma.asarray(variable[selection]).filled(np.nan)


def variable_stats(
    variable: netCDF4.Variable,
    *,
    max_elements: int,
) -> dict[str, float | int | None]:
    count = 0
    nonfinite = 0
    minimum = np.inf
    maximum = -np.inf
    for chunk in iter_variable_chunks(variable, max_elements=max_elements):
        values = np.asarray(chunk, dtype=np.float64)
        finite = np.isfinite(values)
        count += values.size
        nonfinite += values.size - int(np.count_nonzero(finite))
        if np.any(finite):
            minimum = min(minimum, float(np.min(values[finite])))
            maximum = max(maximum, float(np.max(values[finite])))
    return {
        "count": count,
        "nonfinite": nonfinite,
        "minimum": minimum if np.isfinite(minimum) else None,
        "maximum": maximum if np.isfinite(maximum) else None,
    }


def vertical_difference_stats(
    variable: netCDF4.Variable,
    *,
    max_elements: int,
) -> dict[str, float | int | None]:
    if "level" not in variable.dimensions:
        raise ValueError(f"{variable.name} has no level dimension")
    level_axis = variable.dimensions.index("level")
    shape = tuple(variable.shape)
    if shape[level_axis] < 2:
        raise ValueError(f"{variable.name} needs at least two levels")

    # Keep the complete vertical column in each read and block only the other
    # axes, so adjacent-level differences are never split across chunks.
    nonlevel_axes = [axis for axis in range(variable.ndim) if axis != level_axis]
    chunk_lengths = {axis: shape[axis] for axis in nonlevel_axes}
    elements = int(np.prod(shape, dtype=np.int64))
    while elements > max_elements:
        axis = max(nonlevel_axes, key=lambda candidate: chunk_lengths[candidate])
        chunk_lengths[axis] = max(1, (chunk_lengths[axis] + 1) // 2)
        elements = shape[level_axis]
        for candidate in nonlevel_axes:
            elements *= chunk_lengths[candidate]

    starts = [
        range(0, shape[axis], chunk_lengths[axis])
        for axis in nonlevel_axes
    ]
    minimum = np.inf
    maximum = -np.inf
    count = 0
    nonfinite = 0
    for origin in itertools.product(*starts):
        selection: list[slice] = [slice(None)] * variable.ndim
        for axis, start in zip(nonlevel_axes, origin):
            selection[axis] = slice(start, min(start + chunk_lengths[axis], shape[axis]))
        values = np.asarray(
            np.ma.asarray(variable[tuple(selection)]).filled(np.nan),
            dtype=np.float64,
        )
        differences = np.diff(values, axis=level_axis)
        finite = np.isfinite(differences)
        count += differences.size
        nonfinite += differences.size - int(np.count_nonzero(finite))
        if np.any(finite):
            minimum = min(minimum, float(np.min(differences[finite])))
            maximum = max(maximum, float(np.max(differences[finite])))
    return {
        "count": count,
        "nonfinite": nonfinite,
        "minimum": minimum if np.isfinite(minimum) else None,
        "maximum": maximum if np.isfinite(maximum) else None,
    }


def validate_file(
    path: Path,
    *,
    expected_levels: int,
    expected_times: int | None = None,
    max_elements: int,
) -> dict[str, object]:
    failures: list[str] = []
    result: dict[str, object] = {
        "path": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "sha256": sha256(path),
    }
    with netCDF4.Dataset(path) as dataset:
        dimensions = {name: len(dimension) for name, dimension in dataset.dimensions.items()}
        result["dimensions"] = dimensions
        if dimensions.get("level") != expected_levels:
            failures.append(
                f"level dimension is {dimensions.get('level')}, expected {expected_levels}"
            )
        if expected_times is not None and dimensions.get("time") != expected_times:
            failures.append(
                f"time dimension is {dimensions.get('time')}, expected {expected_times}"
            )
        elif dimensions.get("time", 0) < 1:
            failures.append("time dimension is empty or missing")

        missing = sorted(set(REQUIRED_VARIABLES) - set(dataset.variables))
        if missing:
            failures.append(f"missing required variables: {', '.join(missing)}")

        stats: dict[str, object] = {}
        for name, bounds in DEFAULT_BOUNDS.items():
            if name not in dataset.variables:
                continue
            summary = variable_stats(dataset.variables[name], max_elements=max_elements)
            stats[name] = summary
            if summary["nonfinite"] != 0:
                failures.append(f"{name} contains {summary['nonfinite']} nonfinite values")
            lower, upper = bounds
            if (
                summary["minimum"] is None
                or summary["maximum"] is None
                or not (
                    summary["minimum"] >= lower
                    and summary["maximum"] <= upper
                )
            ):
                failures.append(
                    f"{name} range {summary['minimum']}..{summary['maximum']} "
                    f"is outside {lower}..{upper}"
                )
        result["variables"] = stats

        if "time" in dataset.variables:
            time_values = np.asarray(
                np.ma.asarray(dataset.variables["time"][:]).filled(np.nan),
                dtype=np.float64,
            )
            if not np.isfinite(time_values).all():
                failures.append("time contains nonfinite values")
            if time_values.size > 1 and not np.all(np.diff(time_values) > 0.0):
                failures.append("time is not strictly increasing")
            finite_time = time_values[np.isfinite(time_values)]
            result["time"] = {
                "count": int(time_values.size),
                "minimum": float(np.min(finite_time)) if finite_time.size else None,
                "maximum": float(np.max(finite_time)) if finite_time.size else None,
                "units": getattr(dataset.variables["time"], "units", ""),
            }

        if "z" in dataset.variables:
            z_differences = vertical_difference_stats(
                dataset.variables["z"], max_elements=max_elements
            )
            result["z_vertical_difference"] = z_differences
            if (
                z_differences["nonfinite"] != 0
                or z_differences["minimum"] is None
                or not z_differences["minimum"] > 0.0
            ):
                failures.append(
                    f"z is not finite and strictly increasing with level: {z_differences}"
                )

        if "pressure" in dataset.variables:
            pressure_differences = vertical_difference_stats(
                dataset.variables["pressure"], max_elements=max_elements
            )
            result["pressure_vertical_difference"] = pressure_differences
            if (
                pressure_differences["nonfinite"] != 0
                or pressure_differences["maximum"] is None
                or not pressure_differences["maximum"] < 0.0
            ):
                failures.append(
                    "pressure is not finite and strictly decreasing with level: "
                    f"{pressure_differences}"
                )

        if all(name in dataset.variables for name in ("u", "v", "z")):
            mass_shape = dataset.variables["z"].shape[-2:]
            u_shape = dataset.variables["u"].shape[-2:]
            v_shape = dataset.variables["v"].shape[-2:]
            if u_shape != (mass_shape[0], mass_shape[1] + 1):
                failures.append(f"u staggering {u_shape} is inconsistent with mass grid {mass_shape}")
            if v_shape != (mass_shape[0] + 1, mass_shape[1]):
                failures.append(f"v staggering {v_shape} is inconsistent with mass grid {mass_shape}")

    result["failures"] = failures
    result["status"] = "PASS" if not failures else "FAIL"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path, help="HICAR output NetCDF files.")
    parser.add_argument("--expected-levels", type=int, default=80)
    parser.add_argument(
        "--expected-times",
        type=int,
        help="Require this many time records in every input file.",
    )
    parser.add_argument(
        "--max-elements",
        type=int,
        default=2_000_000,
        help="Maximum array elements read in one chunk.",
    )
    parser.add_argument("--report", type=Path, help="Optional atomically written JSON report.")
    args = parser.parse_args()

    if args.expected_levels < 2:
        raise SystemExit("--expected-levels must be at least 2")
    if args.expected_times is not None and args.expected_times < 1:
        raise SystemExit("--expected-times must be at least 1")
    if args.max_elements < args.expected_levels:
        raise SystemExit("--max-elements must be at least --expected-levels")
    missing = [str(path) for path in args.files if not path.is_file()]
    if missing:
        raise SystemExit(f"missing output files: {', '.join(missing)}")

    file_reports = [
        validate_file(
            path,
            expected_levels=args.expected_levels,
            expected_times=args.expected_times,
            max_elements=args.max_elements,
        )
        for path in args.files
    ]
    report = {
        "status": "PASS" if all(item["status"] == "PASS" for item in file_reports) else "FAIL",
        "expected_levels": args.expected_levels,
        "expected_times": args.expected_times,
        "max_elements_per_read": args.max_elements,
        "files": file_reports,
    }
    rendered = json.dumps(report, indent=2, sort_keys=True, allow_nan=False)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f".{args.report.name}.",
            suffix=".tmp",
            dir=args.report.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(rendered + "\n")
        os.replace(temporary, args.report)
    print(rendered)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
