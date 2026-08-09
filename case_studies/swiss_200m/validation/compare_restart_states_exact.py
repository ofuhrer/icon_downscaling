#!/usr/bin/env python3
"""Compare two HICAR restart states exactly over their physical model cores.

The default contract is deliberately narrow: compare every shared NetCDF data
variable except canonical one-dimensional coordinate variables, ignore NetCDF
attributes, and remove HICAR's three-cell horizontal MPI guard on known mass,
U, and V grids.  Auxiliary latitude/longitude and terrain arrays remain in the
comparison because a difference there can identify a wrong restart domain.

Missing variables make the comparison fail unless ``--common-variables`` is
used for diagnosis.  ``--include-coordinate-variables`` includes e.g. HICAR's
``time`` coordinate and reproduces the all-variable convention used for the
historical 196-variable restart evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
from pathlib import Path
from typing import Iterator

import netCDF4
import numpy as np


GUARD_CELLS = 3
HORIZONTAL_DIMENSION_PAIRS = {
    ("lat_y", "lon_x"),
    ("lat_y", "lon_u"),
    ("lat_v", "lon_x"),
}
DEFAULT_CHUNK_BYTES = 64 * 1024 * 1024


def sha256_file(path: Path, chunk_bytes: int = 8 * 1024 * 1024) -> str:
    """Return the SHA-256 digest of *path* without loading it into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(chunk_bytes):
            digest.update(block)
    return digest.hexdigest()


def is_dimension_coordinate(dataset: netCDF4.Dataset, name: str) -> bool:
    """Identify only canonical ``coordinate(dim)`` NetCDF variables.

    CF auxiliary coordinates such as HICAR's two-dimensional ``lat`` and
    ``lon`` are intentionally retained as domain-state checks.
    """
    variable = dataset.variables[name]
    return name in dataset.dimensions and variable.dimensions == (name,)


def physical_core_shape(variable: netCDF4.Variable) -> tuple[int, ...]:
    """Return the shape after excluding a known HICAR horizontal guard."""
    shape = list(variable.shape)
    if tuple(variable.dimensions[-2:]) in HORIZONTAL_DIMENSION_PAIRS:
        if shape[-2] <= 2 * GUARD_CELLS or shape[-1] <= 2 * GUARD_CELLS:
            raise ValueError(
                f"{variable.name}: horizontal dimensions are too short for "
                f"a {GUARD_CELLS}-cell guard: {tuple(shape[-2:])}"
            )
        shape[-2] -= 2 * GUARD_CELLS
        shape[-1] -= 2 * GUARD_CELLS
    return tuple(shape)


def iter_physical_core_chunks(
    variable: netCDF4.Variable,
    max_chunk_bytes: int = DEFAULT_CHUNK_BYTES,
) -> Iterator[np.ndarray | np.ma.MaskedArray]:
    """Yield bounded hyperslabs from a variable's physical model core."""
    if max_chunk_bytes <= 0:
        raise ValueError("max_chunk_bytes must be positive")

    shape = tuple(variable.shape)
    ndim = len(shape)
    itemsize = max(1, np.dtype(variable.dtype).itemsize)
    horizontal = tuple(variable.dimensions[-2:]) in HORIZONTAL_DIMENSION_PAIRS

    if ndim == 0:
        yield variable[...]
        return

    if horizontal:
        horizontal_slice = (
            slice(GUARD_CELLS, -GUARD_CELLS),
            slice(GUARD_CELLS, -GUARD_CELLS),
        )
        core_plane_elements = (shape[-2] - 2 * GUARD_CELLS) * (
            shape[-1] - 2 * GUARD_CELLS
        )
        leading_shape = shape[:-2]
        if not leading_shape:
            yield variable[horizontal_slice]
            return

        # Iterate any outer prefix one value at a time, while grouping as many
        # values of the innermost leading dimension (normally model level) as
        # fit in the requested memory bound.
        prefix_shape = leading_shape[:-1]
        inner_size = leading_shape[-1]
        inner_chunk = max(
            1, min(inner_size, max_chunk_bytes // (core_plane_elements * itemsize))
        )
        prefixes = itertools.product(*(range(size) for size in prefix_shape))
        if not prefix_shape:
            prefixes = [()]
        for prefix in prefixes:
            for start in range(0, inner_size, inner_chunk):
                stop = min(inner_size, start + inner_chunk)
                yield variable[prefix + (slice(start, stop),) + horizontal_slice]
        return

    # Non-horizontal variables are uncommon in restart state.  Chunk the first
    # dimension, retaining all trailing dimensions in each hyperslab.
    trailing_elements = int(np.prod(shape[1:], dtype=np.int64)) if ndim > 1 else 1
    first_chunk = max(
        1, min(shape[0], max_chunk_bytes // max(1, trailing_elements * itemsize))
    )
    for start in range(0, shape[0], first_chunk):
        selection = (slice(start, min(shape[0], start + first_chunk)),) + (
            slice(None),
        ) * (ndim - 1)
        yield variable[selection]


def _bitwise_difference_mask(
    left: np.ndarray, right: np.ndarray
) -> np.ndarray:
    """Return one boolean per element whose stored bits differ."""
    if left.dtype.hasobject or right.dtype.hasobject:
        return np.not_equal(left, right)
    left_bytes = np.ascontiguousarray(left).view(np.uint8).reshape(left.size, -1)
    right_bytes = np.ascontiguousarray(right).view(np.uint8).reshape(right.size, -1)
    return np.any(left_bytes != right_bytes, axis=1).reshape(left.shape)


def compare_arrays_exact(
    left: np.ndarray | np.ma.MaskedArray,
    right: np.ndarray | np.ma.MaskedArray,
) -> dict[str, int | float | None]:
    """Compare arrays by mask and unmasked payload bits."""
    left_ma = np.ma.asarray(left)
    right_ma = np.ma.asarray(right)
    if left_ma.shape != right_ma.shape:
        raise ValueError(f"array shapes differ: {left_ma.shape} != {right_ma.shape}")
    if left_ma.dtype != right_ma.dtype:
        raise TypeError(f"array dtypes differ: {left_ma.dtype} != {right_ma.dtype}")

    left_mask = np.ma.getmaskarray(left_ma)
    right_mask = np.ma.getmaskarray(right_ma)
    mask_differences = left_mask != right_mask
    both_valid = ~(left_mask | right_mask)
    payload_differences = _bitwise_difference_mask(
        np.ma.getdata(left_ma), np.ma.getdata(right_ma)
    ) & both_valid
    differences = mask_differences | payload_differences

    max_abs: float | None = None
    if left_ma.dtype.kind in "biufc" and np.any(payload_differences):
        left_values = np.ma.getdata(left_ma)[payload_differences]
        right_values = np.ma.getdata(right_ma)[payload_differences]
        finite = np.isfinite(left_values) & np.isfinite(right_values)
        if np.any(finite):
            delta = np.abs(
                left_values[finite].astype(np.complex128 if left_ma.dtype.kind == "c" else np.float64)
                - right_values[finite].astype(
                    np.complex128 if right_ma.dtype.kind == "c" else np.float64
                )
            )
            max_abs = float(np.max(delta))

    return {
        "elements": int(left_ma.size),
        "different_elements": int(np.count_nonzero(differences)),
        "different_mask_elements": int(np.count_nonzero(mask_differences)),
        "different_payload_elements": int(np.count_nonzero(payload_differences)),
        "maximum_absolute_difference": max_abs,
    }


def compare_variable_exact(
    left: netCDF4.Variable,
    right: netCDF4.Variable,
    max_chunk_bytes: int = DEFAULT_CHUNK_BYTES,
) -> dict[str, object]:
    """Compare a NetCDF variable exactly, reading bounded chunks."""
    left_shape = physical_core_shape(left)
    right_shape = physical_core_shape(right)
    if left_shape != right_shape:
        return {
            "equal": False,
            "reason": "shape_mismatch",
            "left_shape": list(left_shape),
            "right_shape": list(right_shape),
        }
    if np.dtype(left.dtype) != np.dtype(right.dtype):
        return {
            "equal": False,
            "reason": "dtype_mismatch",
            "left_dtype": str(left.dtype),
            "right_dtype": str(right.dtype),
        }
    if left.dimensions != right.dimensions:
        return {
            "equal": False,
            "reason": "dimension_mismatch",
            "left_dimensions": list(left.dimensions),
            "right_dimensions": list(right.dimensions),
        }

    totals: dict[str, int | float | None] = {
        "elements": 0,
        "different_elements": 0,
        "different_mask_elements": 0,
        "different_payload_elements": 0,
        "maximum_absolute_difference": None,
    }
    left_chunks = iter_physical_core_chunks(left, max_chunk_bytes)
    right_chunks = iter_physical_core_chunks(right, max_chunk_bytes)
    chunk_count = 0
    for left_chunk, right_chunk in itertools.zip_longest(left_chunks, right_chunks):
        if left_chunk is None or right_chunk is None:
            raise RuntimeError(f"{left.name}: chunk iteration count differs")
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
    totals["equal"] = totals["different_elements"] == 0
    return totals


def compare_restart_files(
    left_path: Path,
    right_path: Path,
    *,
    common_variables: bool = False,
    include_coordinate_variables: bool = False,
    include_file_sha256: bool = False,
    max_chunk_bytes: int = DEFAULT_CHUNK_BYTES,
) -> dict[str, object]:
    """Compare restart files and return a JSON-serializable result."""
    left_path = left_path.resolve()
    right_path = right_path.resolve()
    file_records = {
        "left": {"path": str(left_path), "size_bytes": left_path.stat().st_size},
        "right": {"path": str(right_path), "size_bytes": right_path.stat().st_size},
    }
    if include_file_sha256:
        file_records["left"]["sha256"] = sha256_file(left_path)
        file_records["right"]["sha256"] = sha256_file(right_path)

    differing: dict[str, object] = {}
    excluded: dict[str, str] = {}
    compared = 0
    equal_variables = 0
    maximum_absolute_difference: float | None = None
    with netCDF4.Dataset(left_path) as left, netCDF4.Dataset(right_path) as right:
        # Exact restart comparison is over encoded values.  Disabling automatic
        # scale/mask avoids lossy transformations; fill payloads are therefore
        # compared exactly.  ``compare_arrays_exact`` still handles masked
        # arrays for callers and non-netCDF adapters.
        left.set_auto_maskandscale(False)
        right.set_auto_maskandscale(False)
        left_names = set(left.variables)
        right_names = set(right.variables)
        shared = left_names & right_names
        missing_from_left = sorted(right_names - left_names)
        missing_from_right = sorted(left_names - right_names)

        selected: list[str] = []
        for name in sorted(shared):
            left_coord = is_dimension_coordinate(left, name)
            right_coord = is_dimension_coordinate(right, name)
            if not include_coordinate_variables and left_coord and right_coord:
                excluded[name] = "canonical_dimension_coordinate"
                continue
            selected.append(name)

        for name in selected:
            compared += 1
            result = compare_variable_exact(
                left[name], right[name], max_chunk_bytes=max_chunk_bytes
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

        schema_equal = not missing_from_left and not missing_from_right
        state_equal = not differing and (schema_equal or common_variables)
        schema = {
            "left_variable_count": len(left_names),
            "right_variable_count": len(right_names),
            "shared_variable_count": len(shared),
            "compared_variable_count": compared,
            "equal_variable_count": equal_variables,
            "excluded_shared_variables": excluded,
            "missing_from_left": missing_from_left,
            "missing_from_right": missing_from_right,
        }

    return {
        "comparison_contract": {
            "name": "hicar_restart_shared_state_v1",
            "encoded_values": True,
            "netcdf_attributes_compared": False,
            "coordinate_variables_included": include_coordinate_variables,
            "coordinate_exclusion_rule": "name is a dimension and dimensions == (name,)",
            "auxiliary_coordinates_retained": True,
            "guard_cells_excluded_per_horizontal_edge": GUARD_CELLS,
            "horizontal_dimension_pairs": [
                list(pair) for pair in sorted(HORIZONTAL_DIMENSION_PAIRS)
            ],
            "schema_strict": not common_variables,
            "whole_file_sha256_included": include_file_sha256,
            "max_chunk_bytes": max_chunk_bytes,
        },
        **file_records,
        "schema": schema,
        "differing_variable_count": len(differing),
        "maximum_absolute_difference": maximum_absolute_difference,
        "differing_variables": differing,
        "bitwise_equal_model_core_state": state_equal,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    parser.add_argument(
        "--common-variables",
        action="store_true",
        help="Diagnostic mode: missing variables are reported but do not make the result fail.",
    )
    parser.add_argument(
        "--include-coordinate-variables",
        action="store_true",
        help="Also compare canonical dimension coordinates (the historical 196-variable mode).",
    )
    parser.add_argument(
        "--file-sha256",
        action="store_true",
        help=(
            "Also hash both complete NetCDF files. This doubles large-file I/O and "
            "is unnecessary for the exact model-state comparison."
        ),
    )
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

    result = compare_restart_files(
        args.left,
        args.right,
        common_variables=args.common_variables,
        include_coordinate_variables=args.include_coordinate_variables,
        include_file_sha256=args.file_sha256,
        max_chunk_bytes=max(1, int(args.chunk_mib * 1024**2)),
    )
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(payload, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_name(f".{args.output.name}.{os.getpid()}.tmp")
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, args.output)
    return 0 if result["bitwise_equal_model_core_state"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
