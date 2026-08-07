#!/usr/bin/env python3
"""Compare NetCDF variable arrays independently of container metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any

import netCDF4
import numpy as np


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
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


def require_published(path: Path) -> None:
    if not path.is_file() or not Path(f"{path}.ready").is_file():
        raise ValueError(f"NetCDF publication is missing: {path}")


def normalized_array(value: Any) -> np.ndarray:
    array = np.ma.asarray(value)
    if np.ma.isMaskedArray(array):
        if np.issubdtype(array.dtype, np.floating):
            return np.asarray(array.filled(np.nan))
        if np.issubdtype(array.dtype, np.integer):
            return np.asarray(array.filled(np.iinfo(array.dtype).min))
        return np.asarray(array.filled())
    return np.asarray(array)


def compare_arrays(first: np.ndarray, second: np.ndarray) -> tuple[bool, float | None]:
    if first.shape != second.shape or first.dtype != second.dtype:
        return False, None
    equal = bool(np.array_equal(first, second, equal_nan=True))
    if not np.issubdtype(first.dtype, np.number):
        return equal, None
    difference = np.abs(
        first.astype(np.float64, copy=False)
        - second.astype(np.float64, copy=False)
    )
    finite = np.isfinite(difference)
    maximum = float(np.max(difference[finite])) if np.any(finite) else 0.0
    return equal, maximum


def compare_files(
    first_path: Path, second_path: Path, output_path: Path
) -> dict[str, Any]:
    require_published(first_path)
    require_published(second_path)
    first_hash = sha256(first_path)
    second_hash = sha256(second_path)
    failures = []
    variables = []
    with netCDF4.Dataset(first_path) as first, netCDF4.Dataset(
        second_path
    ) as second:
        first_dimensions = {
            name: len(dimension)
            for name, dimension in first.dimensions.items()
        }
        second_dimensions = {
            name: len(dimension)
            for name, dimension in second.dimensions.items()
        }
        if first_dimensions != second_dimensions:
            failures.append("dimension mismatch")
        first_names = set(first.variables)
        second_names = set(second.variables)
        if first_names != second_names:
            failures.append("variable inventory mismatch")
        for name in sorted(first_names & second_names):
            first_variable = first.variables[name]
            second_variable = second.variables[name]
            if (
                first_variable.dimensions != second_variable.dimensions
                or first_variable.dtype != second_variable.dtype
                or first_variable.shape != second_variable.shape
            ):
                failures.append(f"{name}: schema mismatch")
                variables.append(
                    {
                        "name": name,
                        "arrays_identical": False,
                        "maximum_absolute_difference": None,
                    }
                )
                continue
            arrays_identical = True
            maximum = 0.0
            slices = (
                [slice(None)]
                if not first_variable.shape
                else [
                    (index, *([slice(None)] * (first_variable.ndim - 1)))
                    for index in range(first_variable.shape[0])
                ]
            )
            for selection in slices:
                first_array = normalized_array(first_variable[selection])
                second_array = normalized_array(second_variable[selection])
                equal, difference = compare_arrays(first_array, second_array)
                arrays_identical = arrays_identical and equal
                if difference is not None and math.isfinite(difference):
                    maximum = max(maximum, difference)
            if not arrays_identical:
                failures.append(f"{name}: array values differ")
            variables.append(
                {
                    "name": name,
                    "arrays_identical": arrays_identical,
                    "maximum_absolute_difference": maximum,
                }
            )
        global_attributes_identical = {
            name: getattr(first, name) == getattr(second, name)
            for name in sorted(set(first.ncattrs()) & set(second.ncattrs()))
        }
        global_attribute_inventory_identical = set(first.ncattrs()) == set(
            second.ncattrs()
        )

    arrays_identical = not failures
    payload = {
        "schema_version": 1,
        "status": "PASS" if arrays_identical else "HOLD",
        "decision": (
            "ARRAYS_IDENTICAL_CONTAINER_METADATA_DIFFERS"
            if arrays_identical and first_hash != second_hash
            else (
                "FILES_BYTE_IDENTICAL"
                if arrays_identical
                else "FORCING_ARRAYS_DIFFER"
            )
        ),
        "first": {
            "path": str(first_path),
            "sha256": first_hash,
            "size_bytes": first_path.stat().st_size,
        },
        "second": {
            "path": str(second_path),
            "sha256": second_hash,
            "size_bytes": second_path.stat().st_size,
        },
        "arrays_identical": arrays_identical,
        "container_byte_identical": first_hash == second_hash,
        "global_attribute_inventory_identical": (
            global_attribute_inventory_identical
        ),
        "global_attributes_identical": global_attributes_identical,
        "variables": variables,
        "failures": failures,
    }
    if output_path.exists() or Path(f"{output_path}.ready").exists():
        raise ValueError(f"refusing to replace comparison: {output_path}")
    write_json_atomic(output_path, payload)
    Path(f"{output_path}.ready").touch()
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--first", type=Path, required=True)
    parser.add_argument("--second", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = compare_files(
        args.first.resolve(), args.second.resolve(), args.output.resolve()
    )
    print(
        f"NetCDF array comparison: status={payload['status']} "
        f"decision={payload['decision']}"
    )
    return 0 if payload["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
