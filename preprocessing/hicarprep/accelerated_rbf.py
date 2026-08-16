"""Optional compiled kernels for repeated scalar RBF application."""

from __future__ import annotations

import numba
import numpy as np


@numba.njit(cache=True)
def _apply_float64(
    source: np.ndarray,
    donor_index: np.ndarray,
    weight: np.ndarray,
    monotone: bool,
) -> np.ndarray:
    """Apply one fixed donor stencil without allocating gathered donors."""
    leading_count, _ = source.shape
    target_count, donor_count = donor_index.shape
    result = np.empty((leading_count, target_count), dtype=np.float64)
    for leading in range(leading_count):
        for target in range(target_count):
            total = 0.0
            lower = np.inf
            upper = -np.inf
            for donor in range(donor_count):
                value = source[leading, donor_index[target, donor]]
                total += value * weight[target, donor]
                if value < lower:
                    lower = value
                if value > upper:
                    upper = value
            if monotone:
                if total < lower:
                    total = lower
                elif total > upper:
                    total = upper
            result[leading, target] = total
    return result


def apply_rbf(
    source: np.ndarray,
    donor_index: np.ndarray,
    weight: np.ndarray,
    *,
    monotone: bool,
) -> np.ndarray:
    """Normalize arrays for the compiled float64 RBF kernel."""
    source_array = np.ascontiguousarray(source, dtype=np.float64)
    source_2d = source_array.reshape((-1, source_array.shape[-1]))
    return _apply_float64(
        source_2d,
        np.ascontiguousarray(donor_index, dtype=np.int64),
        np.ascontiguousarray(weight, dtype=np.float64),
        monotone,
    )
