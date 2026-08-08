#!/usr/bin/env python3
"""Require two HICAR terminal restarts to contain identical model-state arrays."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import netCDF4
import numpy as np


def core_values(variable: netCDF4.Variable) -> np.ndarray:
    """Read a restart variable without the three-cell MPI guard region."""
    values = np.ma.asarray(variable[:]).filled(np.nan)
    if values.ndim >= 2 and values.shape[-1] > 6 and values.shape[-2] > 6:
        values = values[
            (slice(None),) * (values.ndim - 2) + (slice(3, -3), slice(3, -3))
        ]
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("continuous", type=Path)
    parser.add_argument("segmented", type=Path)
    args = parser.parse_args()

    mismatches: dict[str, object] = {}
    compared = 0
    with netCDF4.Dataset(args.continuous) as left, netCDF4.Dataset(args.segmented) as right:
        if set(left.variables) != set(right.variables):
            missing_left = sorted(set(right.variables) - set(left.variables))
            missing_right = sorted(set(left.variables) - set(right.variables))
            raise SystemExit(
                f"restart schemas differ: missing_left={missing_left}, missing_right={missing_right}"
            )
        for name in sorted(left.variables):
            a = core_values(left[name])
            b = core_values(right[name])
            if a.shape != b.shape:
                mismatches[name] = {"left_shape": a.shape, "right_shape": b.shape}
                continue
            compared += 1
            equal = (
                np.array_equal(a, b, equal_nan=True)
                if a.dtype.kind in "fc" and b.dtype.kind in "fc"
                else np.array_equal(a, b)
            )
            if equal:
                continue
            if a.dtype.kind in "fc" and b.dtype.kind in "fc":
                finite = np.isfinite(a) & np.isfinite(b)
                maximum = float(np.max(np.abs(a[finite] - b[finite]))) if np.any(finite) else None
                mismatches[name] = {"maximum_absolute_difference": maximum}
            else:
                mismatches[name] = {"different_elements": int(np.count_nonzero(a != b))}
    if mismatches:
        print(json.dumps({"compared_variables": compared, "mismatches": mismatches}, indent=2))
        return 1
    print(json.dumps({
        "continuous": str(args.continuous),
        "segmented": str(args.segmented),
        "compared_variables": compared,
        "bitwise_equal_model_core_state": True,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
