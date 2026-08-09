#!/usr/bin/env python3
"""Compare selected time slices from two HICAR NetCDF output files exactly.

Every shared variable containing the configured time dimension is compared at
the independently selected left and right time indices.  Variables without a
time dimension are compared in full exactly once.  Values are compared as
stored (automatic NetCDF mask/scale conversion is disabled), while NetCDF
attributes and the total length of the time dimension are intentionally
outside the contract.

The implementation reads bounded hyperslabs, so a three-dimensional HICAR
field does not have to fit in memory.  Missing variables and incompatible
retained schemas make the comparison fail.  The command exits zero only when
the selected output states are bitwise identical.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
from pathlib import Path
import sys
from typing import Iterator

import netCDF4
import numpy as np


# Keep the exact bit comparison and whole-file digest semantics identical to
# the restart comparator without requiring this script directory to be a
# Python package when invoked directly.
VALIDATION_DIRECTORY = Path(__file__).resolve().parent
if str(VALIDATION_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(VALIDATION_DIRECTORY))
from compare_restart_states_exact import compare_arrays_exact, sha256_file  # noqa: E402


DEFAULT_CHUNK_BYTES = 64 * 1024 * 1024


def normalize_index(index: int, size: int, *, label: str) -> int:
    """Resolve a possibly negative index and reject out-of-range selections."""
    resolved = index + size if index < 0 else index
    if not 0 <= resolved < size:
        raise IndexError(f"{label} time index {index} is outside a dimension of size {size}")
    return resolved


def selected_shape(
    variable: netCDF4.Variable,
    *,
    time_dimension: str,
) -> tuple[int, ...]:
    """Return the decoded shape after selecting one time index, if present."""
    return tuple(
        size
        for dimension, size in zip(variable.dimensions, variable.shape)
        if dimension != time_dimension
    )


def _chunk_shape(shape: tuple[int, ...], itemsize: int, max_chunk_bytes: int) -> tuple[int, ...]:
    """Choose an N-dimensional tile bounded by ``max_chunk_bytes``."""
    if max_chunk_bytes <= 0:
        raise ValueError("max_chunk_bytes must be positive")
    if not shape:
        return ()

    chunks = list(shape)
    itemsize = max(1, itemsize)
    while int(np.prod(chunks, dtype=np.int64)) * itemsize > max_chunk_bytes:
        candidates = [axis for axis, length in enumerate(chunks) if length > 1]
        if not candidates:
            break
        axis = max(candidates, key=lambda candidate: chunks[candidate])
        chunks[axis] = (chunks[axis] + 1) // 2
    return tuple(chunks)


def iter_selected_chunks(
    variable: netCDF4.Variable,
    *,
    time_dimension: str,
    time_index: int,
    max_chunk_bytes: int = DEFAULT_CHUNK_BYTES,
) -> Iterator[np.ndarray | np.ma.MaskedArray]:
    """Yield bounded chunks from one time slice, or from a static variable once."""
    if variable.dimensions.count(time_dimension) > 1:
        raise ValueError(
            f"{variable.name}: time dimension {time_dimension!r} occurs more than once"
        )

    retained_shape = selected_shape(variable, time_dimension=time_dimension)
    chunk_shape = _chunk_shape(
        retained_shape, max(1, np.dtype(variable.dtype).itemsize), max_chunk_bytes
    )
    if not retained_shape:
        selection = tuple(
            time_index if dimension == time_dimension else slice(None)
            for dimension in variable.dimensions
        )
        yield variable[selection] if selection else variable[...]
        return

    ranges = [range(0, size, chunk) for size, chunk in zip(retained_shape, chunk_shape)]
    for starts in itertools.product(*ranges):
        retained_selections = iter(
            slice(start, min(start + chunk, size))
            for start, chunk, size in zip(starts, chunk_shape, retained_shape)
        )
        selection = tuple(
            time_index if dimension == time_dimension else next(retained_selections)
            for dimension in variable.dimensions
        )
        yield variable[selection]


def compare_variable_slice_exact(
    left: netCDF4.Variable,
    right: netCDF4.Variable,
    *,
    time_dimension: str,
    left_time_index: int,
    right_time_index: int,
    max_chunk_bytes: int = DEFAULT_CHUNK_BYTES,
) -> dict[str, object]:
    """Compare one shared output variable over its selected state."""
    if left.dimensions != right.dimensions:
        return {
            "equal": False,
            "reason": "dimension_mismatch",
            "left_dimensions": list(left.dimensions),
            "right_dimensions": list(right.dimensions),
        }
    if np.dtype(left.dtype) != np.dtype(right.dtype):
        return {
            "equal": False,
            "reason": "dtype_mismatch",
            "left_dtype": str(left.dtype),
            "right_dtype": str(right.dtype),
        }

    left_shape = selected_shape(left, time_dimension=time_dimension)
    right_shape = selected_shape(right, time_dimension=time_dimension)
    if left_shape != right_shape:
        return {
            "equal": False,
            "reason": "selected_shape_mismatch",
            "left_selected_shape": list(left_shape),
            "right_selected_shape": list(right_shape),
        }

    totals: dict[str, int | float | None | bool] = {
        "elements": 0,
        "different_elements": 0,
        "different_mask_elements": 0,
        "different_payload_elements": 0,
        "maximum_absolute_difference": None,
    }
    left_chunks = iter_selected_chunks(
        left,
        time_dimension=time_dimension,
        time_index=left_time_index,
        max_chunk_bytes=max_chunk_bytes,
    )
    right_chunks = iter_selected_chunks(
        right,
        time_dimension=time_dimension,
        time_index=right_time_index,
        max_chunk_bytes=max_chunk_bytes,
    )
    chunk_count = 0
    for left_chunk, right_chunk in itertools.zip_longest(left_chunks, right_chunks):
        if left_chunk is None or right_chunk is None:
            raise RuntimeError(f"{left.name}: selected chunk counts differ")
        chunk_count += 1
        result = compare_arrays_exact(left_chunk, right_chunk)
        for key in (
            "elements",
            "different_elements",
            "different_mask_elements",
            "different_payload_elements",
        ):
            totals[key] = int(totals[key]) + int(result[key])
        chunk_max = result["maximum_absolute_difference"]
        if chunk_max is not None:
            current_max = totals["maximum_absolute_difference"]
            totals["maximum_absolute_difference"] = (
                float(chunk_max)
                if current_max is None
                else max(float(current_max), float(chunk_max))
            )

    totals["chunks"] = chunk_count
    totals["selected_shape"] = list(left_shape)
    totals["time_dependent"] = time_dimension in left.dimensions
    totals["equal"] = totals["different_elements"] == 0
    return totals


def _encoded_time_value(dataset: netCDF4.Dataset, time_dimension: str, index: int) -> object:
    """Return a JSON-safe encoded coordinate value when a canonical one exists."""
    if time_dimension not in dataset.variables:
        return None
    coordinate = dataset.variables[time_dimension]
    if coordinate.dimensions != (time_dimension,):
        return None
    value = np.asarray(coordinate[index])
    if value.dtype.kind in "SU":
        return str(value.item())
    if value.dtype.kind == "b":
        return bool(value.item())
    if value.dtype.kind in "iu":
        return int(value.item())
    if value.dtype.kind in "fc":
        scalar = value.item()
        if np.isfinite(scalar):
            return float(scalar)
        return str(scalar)
    return str(value.item())


def compare_output_slices(
    left_path: Path,
    right_path: Path,
    *,
    left_time_index: int = -1,
    right_time_index: int = -1,
    time_dimension: str = "time",
    max_chunk_bytes: int = DEFAULT_CHUNK_BYTES,
) -> dict[str, object]:
    """Compare selected HICAR output states and return a JSON-safe report."""
    if max_chunk_bytes <= 0:
        raise ValueError("max_chunk_bytes must be positive")
    left_path = left_path.resolve()
    right_path = right_path.resolve()
    files: dict[str, dict[str, object]] = {
        "left": {
            "path": str(left_path),
            "size_bytes": left_path.stat().st_size,
            "sha256": sha256_file(left_path),
        },
        "right": {
            "path": str(right_path),
            "size_bytes": right_path.stat().st_size,
            "sha256": sha256_file(right_path),
        },
    }

    differing: dict[str, object] = {}
    maximum_absolute_difference: float | None = None
    equal_variables = 0
    with netCDF4.Dataset(left_path) as left, netCDF4.Dataset(right_path) as right:
        left.set_auto_maskandscale(False)
        right.set_auto_maskandscale(False)
        if time_dimension not in left.dimensions:
            raise KeyError(f"left file has no {time_dimension!r} dimension")
        if time_dimension not in right.dimensions:
            raise KeyError(f"right file has no {time_dimension!r} dimension")

        left_time_count = len(left.dimensions[time_dimension])
        right_time_count = len(right.dimensions[time_dimension])
        left_index = normalize_index(left_time_index, left_time_count, label="left")
        right_index = normalize_index(right_time_index, right_time_count, label="right")
        files["left"].update(
            {
                "requested_time_index": left_time_index,
                "resolved_time_index": left_index,
                "time_count": left_time_count,
                "encoded_time_value": _encoded_time_value(left, time_dimension, left_index),
            }
        )
        files["right"].update(
            {
                "requested_time_index": right_time_index,
                "resolved_time_index": right_index,
                "time_count": right_time_count,
                "encoded_time_value": _encoded_time_value(right, time_dimension, right_index),
            }
        )

        left_names = set(left.variables)
        right_names = set(right.variables)
        shared = left_names & right_names
        missing_from_left = sorted(right_names - left_names)
        missing_from_right = sorted(left_names - right_names)

        for name in sorted(shared):
            result = compare_variable_slice_exact(
                left[name],
                right[name],
                time_dimension=time_dimension,
                left_time_index=left_index,
                right_time_index=right_index,
                max_chunk_bytes=max_chunk_bytes,
            )
            if bool(result["equal"]):
                equal_variables += 1
            else:
                differing[name] = result
                variable_max = result.get("maximum_absolute_difference")
                if variable_max is not None:
                    maximum_absolute_difference = (
                        float(variable_max)
                        if maximum_absolute_difference is None
                        else max(maximum_absolute_difference, float(variable_max))
                    )

        non_time_dimensions = sorted(
            (set(left.dimensions) | set(right.dimensions)) - {time_dimension}
        )
        dimension_differences: dict[str, dict[str, int | None]] = {}
        for name in non_time_dimensions:
            left_size = len(left.dimensions[name]) if name in left.dimensions else None
            right_size = len(right.dimensions[name]) if name in right.dimensions else None
            if left_size != right_size:
                dimension_differences[name] = {
                    "left_size": left_size,
                    "right_size": right_size,
                }

        schema = {
            "left_variable_count": len(left_names),
            "right_variable_count": len(right_names),
            "shared_variable_count": len(shared),
            "compared_variable_count": len(shared),
            "equal_variable_count": equal_variables,
            "missing_from_left": missing_from_left,
            "missing_from_right": missing_from_right,
            "non_time_dimension_differences": dimension_differences,
        }

    schema_equal = (
        not schema["missing_from_left"]
        and not schema["missing_from_right"]
        and not schema["non_time_dimension_differences"]
    )
    bitwise_equal = schema_equal and not differing
    return {
        "comparison_contract": {
            "name": "hicar_output_selected_state_v1",
            "encoded_values": True,
            "netcdf_attributes_compared": False,
            "time_dimension": time_dimension,
            "time_dimension_length_compared": False,
            "time_dependent_variables": "selected_index_per_file",
            "non_time_variables": "compared_in_full_once",
            "schema_strict": True,
            "max_chunk_bytes": max_chunk_bytes,
        },
        **files,
        "schema": schema,
        "differing_variable_count": len(differing),
        "maximum_absolute_difference": maximum_absolute_difference,
        "differing_variables": differing,
        "bitwise_equal_selected_output_state": bitwise_equal,
    }


def write_json_atomic(path: Path, result: dict[str, object]) -> None:
    """Write one comparison report via same-directory atomic replacement."""
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    parser.add_argument("--left-time-index", type=int, default=-1)
    parser.add_argument("--right-time-index", type=int, default=-1)
    parser.add_argument("--time-dimension", default="time")
    parser.add_argument(
        "--chunk-mib",
        type=float,
        default=DEFAULT_CHUNK_BYTES / 1024**2,
        help="Maximum decoded hyperslab size in MiB (default: 64).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write JSON atomically to this path instead of standard output.",
    )
    args = parser.parse_args()
    if args.chunk_mib <= 0:
        parser.error("--chunk-mib must be positive")

    result = compare_output_slices(
        args.left,
        args.right,
        left_time_index=args.left_time_index,
        right_time_index=args.right_time_index,
        time_dimension=args.time_dimension,
        max_chunk_bytes=max(1, int(args.chunk_mib * 1024**2)),
    )
    if args.output is None:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        write_json_atomic(args.output, result)
    return 0 if result["bitwise_equal_selected_output_state"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
