#!/usr/bin/env python3
"""Compare two HICAR terminal restart states over the physical model core."""

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
    parser.add_argument(
        "--common-variables",
        action="store_true",
        help="Compare only shared variables (diagnostic use for restart versus initial output).",
    )
    args = parser.parse_args()

    mismatches: dict[str, object] = {}
    compared = 0
    with netCDF4.Dataset(args.continuous) as left, netCDF4.Dataset(args.segmented) as right:
        if set(left.variables) != set(right.variables) and not args.common_variables:
            missing_left = sorted(set(right.variables) - set(left.variables))
            missing_right = sorted(set(left.variables) - set(right.variables))
            raise SystemExit(
                f"restart schemas differ: missing_left={missing_left}, missing_right={missing_right}"
            )
        variable_names = (
            set(left.variables) & set(right.variables)
            if args.common_variables
            else set(left.variables)
        )
        for name in sorted(variable_names):
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
                if np.any(finite):
                    delta = np.asarray(a[finite] - b[finite], dtype=np.float64)
                    absolute = np.abs(delta)
                    mismatches[name] = {
                        "maximum_absolute_difference": float(np.max(absolute)),
                        "mean_absolute_difference": float(np.mean(absolute)),
                        "root_mean_square_difference": float(
                            np.sqrt(np.mean(delta * delta))
                        ),
                        "different_fraction": float(np.count_nonzero(delta) / delta.size),
                        "finite_elements": int(delta.size),
                    }
                else:
                    mismatches[name] = {
                        "maximum_absolute_difference": None,
                        "finite_elements": 0,
                    }
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
