#!/usr/bin/env python3
"""Compare two HICAR forcing products without loading whole fields at once."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import netCDF4
import numpy as np


def _normalized(values: np.ndarray) -> np.ndarray:
    return np.asarray(np.ma.asarray(values).filled(np.nan))


def _slices(variable: netCDF4.Variable):
    if variable.ndim == 0:
        yield (...,)
    elif variable.shape[0] == 0:
        return
    else:
        trailing_count = int(np.prod(variable.shape[1:], dtype=np.int64))
        bytes_per_leading = max(trailing_count * variable.dtype.itemsize, 1)
        leading_chunk = max(1, (64 * 1024 * 1024) // bytes_per_leading)
        for start in range(0, variable.shape[0], leading_chunk):
            stop = min(start + leading_chunk, variable.shape[0])
            yield (slice(start, stop), *([slice(None)] * (variable.ndim - 1)))


def _compare_variable(
    reference: netCDF4.Variable,
    candidate: netCDF4.Variable,
) -> dict[str, object]:
    report: dict[str, object] = {
        "dimensions_equal": reference.dimensions == candidate.dimensions,
        "shape_equal": reference.shape == candidate.shape,
        "dtype_equal": reference.dtype == candidate.dtype,
        "reference_dtype": str(reference.dtype),
        "candidate_dtype": str(candidate.dtype),
    }
    if not report["dimensions_equal"] or not report["shape_equal"]:
        return report

    numeric = np.issubdtype(reference.dtype, np.number) and np.issubdtype(
        candidate.dtype, np.number
    )
    different_count = 0
    reference_cast_different_count = 0
    finite_count = 0
    nonfinite_pattern_equal = True
    maximum_absolute = 0.0
    maximum_relative = 0.0
    squared_difference = 0.0
    reference_squared = 0.0
    candidate_squared = 0.0
    reference_minimum = np.inf
    reference_maximum = -np.inf
    for selection in _slices(reference):
        left = _normalized(reference[selection])
        right = _normalized(candidate[selection])
        if numeric:
            left_finite = np.isfinite(left)
            right_finite = np.isfinite(right)
            nonfinite_pattern_equal &= bool(np.array_equal(left_finite, right_finite))
            finite = left_finite & right_finite
            if np.any(finite):
                left_values = left[finite].astype(np.float64)
                right_values = right[finite].astype(np.float64)
                difference = np.abs(left_values - right_values)
                scale = np.maximum(np.abs(left_values), 1.0e-30)
                maximum_absolute = max(maximum_absolute, float(np.max(difference)))
                maximum_relative = max(
                    maximum_relative, float(np.max(difference / scale))
                )
                squared_difference += float(np.dot(difference, difference))
                reference_squared += float(np.dot(left_values, left_values))
                candidate_squared += float(np.dot(right_values, right_values))
                reference_minimum = min(reference_minimum, float(np.min(left_values)))
                reference_maximum = max(reference_maximum, float(np.max(left_values)))
                finite_count += int(difference.size)
                different_count += int(np.count_nonzero(difference))
                cast_reference = left[finite].astype(candidate.dtype, copy=False)
                reference_cast_different_count += int(
                    np.count_nonzero(cast_reference != right[finite])
                )
        else:
            different_count += int(np.count_nonzero(left != right))
    report.update(
        {
            "different_value_count": different_count,
            "bitwise_equal": different_count == 0 and nonfinite_pattern_equal,
            "nonfinite_pattern_equal": nonfinite_pattern_equal,
        }
    )
    if numeric:
        rms_difference = (
            float(np.sqrt(squared_difference / finite_count)) if finite_count else 0.0
        )
        reference_rms = (
            float(np.sqrt(reference_squared / finite_count)) if finite_count else 0.0
        )
        report.update(
            {
                "finite_value_count": finite_count,
                "reference_cast_different_value_count": reference_cast_different_count,
                "candidate_equal_to_reference_cast": (
                    reference_cast_different_count == 0 and nonfinite_pattern_equal
                ),
                "maximum_absolute_difference": maximum_absolute,
                "maximum_relative_difference": maximum_relative,
                "rms_difference": rms_difference,
                "reference_rms": reference_rms,
                "candidate_rms": (
                    float(np.sqrt(candidate_squared / finite_count))
                    if finite_count
                    else 0.0
                ),
                "normalized_rms_difference": rms_difference
                / max(reference_rms, 1.0e-30),
                "reference_minimum": reference_minimum if finite_count else None,
                "reference_maximum": reference_maximum if finite_count else None,
            }
        )
    return report


def _attributes(dataset: netCDF4.Dataset) -> dict[str, object]:
    return {name: dataset.getncattr(name) for name in dataset.ncattrs()}


def compare(reference_path: Path, candidate_path: Path) -> dict[str, object]:
    with netCDF4.Dataset(reference_path) as reference, netCDF4.Dataset(
        candidate_path
    ) as candidate:
        reference_names = set(reference.variables)
        candidate_names = set(candidate.variables)
        common = sorted(reference_names & candidate_names)
        variables = {
            name: _compare_variable(reference[name], candidate[name]) for name in common
        }
        reference_attributes = _attributes(reference)
        candidate_attributes = _attributes(candidate)
        changed_attributes = {
            name: {
                "reference": reference_attributes.get(name),
                "candidate": candidate_attributes.get(name),
            }
            for name in sorted(set(reference_attributes) | set(candidate_attributes))
            if reference_attributes.get(name) != candidate_attributes.get(name)
        }
    structural_equal = (
        reference_names == candidate_names
        and all(
            item["dimensions_equal"] and item["shape_equal"]
            for item in variables.values()
        )
    )
    storage_dtype_equal = reference_names == candidate_names and all(
        item["dtype_equal"] for item in variables.values()
    )
    return {
        "schema": "hicarprep-numerical-comparison-v1",
        "reference": str(reference_path),
        "candidate": str(candidate_path),
        "structural_equal": structural_equal,
        "storage_dtype_equal": storage_dtype_equal,
        "all_variable_values_bitwise_equal": structural_equal
        and all(item["bitwise_equal"] for item in variables.values()),
        "reference_only_variables": sorted(reference_names - candidate_names),
        "candidate_only_variables": sorted(candidate_names - reference_names),
        "changed_global_attributes": changed_attributes,
        "variables": variables,
    }


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.partial")
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    payload = compare(args.reference, args.candidate)
    _atomic_json(args.report, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["structural_equal"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
