"""Optional compiled kernels for repeated scalar RBF application."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os

import numba
import numpy as np


_DEFAULT_RBF_THREADS = 8
_MINIMUM_OUTPUTS_PER_THREAD = 16_384


@numba.njit(cache=True, nogil=True)
def _apply_float64_range(
    source: np.ndarray,
    donor_index: np.ndarray,
    weight: np.ndarray,
    monotone: bool,
    result: np.ndarray,
    start: int,
    stop: int,
) -> None:
    """Apply a contiguous output range with a fixed serial donor loop."""
    target_count, donor_count = donor_index.shape
    for output_index in range(start, stop):
        leading = output_index // target_count
        target = output_index - leading * target_count
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


def _available_cpu_count() -> int:
    """Honor process affinity and the Slurm task allocation when present."""
    affinity_count = (
        len(os.sched_getaffinity(0))
        if hasattr(os, "sched_getaffinity")
        else int(os.cpu_count() or 1)
    )
    slurm_value = os.environ.get("SLURM_CPUS_PER_TASK")
    if slurm_value is None:
        return max(1, affinity_count)
    try:
        slurm_count = int(slurm_value)
    except ValueError as exc:
        raise ValueError("SLURM_CPUS_PER_TASK must be an integer") from exc
    if slurm_count < 1:
        raise ValueError("SLURM_CPUS_PER_TASK must be at least one")
    return min(affinity_count, slurm_count)


def _rbf_thread_count(output_count: int) -> int:
    """Return bounded parallelism without initializing Numba's OpenMP layer."""
    available = _available_cpu_count()
    configured = os.environ.get("HICARPREP_RBF_THREADS")
    try:
        requested = (
            int(configured)
            if configured is not None
            else min(_DEFAULT_RBF_THREADS, available)
        )
    except ValueError as exc:
        raise ValueError("HICARPREP_RBF_THREADS must be an integer") from exc
    if requested < 1:
        raise ValueError("HICARPREP_RBF_THREADS must be at least one")
    useful = max(
        1,
        (output_count + _MINIMUM_OUTPUTS_PER_THREAD - 1)
        // _MINIMUM_OUTPUTS_PER_THREAD,
    )
    return min(requested, available, useful)


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
    donor_array = np.ascontiguousarray(donor_index, dtype=np.int64)
    weight_array = np.ascontiguousarray(weight, dtype=np.float64)
    output_count = source_2d.shape[0] * donor_array.shape[0]
    result = np.empty((source_2d.shape[0], donor_array.shape[0]), dtype=np.float64)
    thread_count = _rbf_thread_count(output_count)
    if thread_count == 1:
        _apply_float64_range(
            source_2d, donor_array, weight_array, monotone, result, 0, output_count
        )
        return result

    # Numba's OpenMP backend is not reliably safe when a process forks after a
    # parallel region.  hicarprep does exactly that for column reconstruction,
    # so use joined Python threads around a nogil kernel instead.  Each range is
    # disjoint and donor accumulation remains serial and identically ordered.
    block = (output_count + thread_count - 1) // thread_count
    ranges = [
        (start, min(start + block, output_count))
        for start in range(0, output_count, block)
    ]
    # Compile before concurrent dispatch so worker threads only execute native
    # code and never race through dispatcher compilation.
    _apply_float64_range(
        source_2d, donor_array, weight_array, monotone, result, 0, 0
    )
    with ThreadPoolExecutor(max_workers=len(ranges)) as executor:
        futures = [
            executor.submit(
                _apply_float64_range,
                source_2d,
                donor_array,
                weight_array,
                monotone,
                result,
                start,
                stop,
            )
            for start, stop in ranges
        ]
        for future in futures:
            future.result()
    return result
