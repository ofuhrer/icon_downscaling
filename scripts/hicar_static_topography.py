"""Scale-selective terrain filtering helpers for HICAR static files.

The filter deliberately operates outside HICAR.  It smooths land terrain without
mixing values across the land/water mask and leaves every non-terrain static
field to the caller.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class TerrainMetrics:
    minimum_m: float
    mean_m: float
    maximum_m: float
    max_land_neighbor_difference_m: float
    p999_land_neighbor_difference_m: float
    mean_land_neighbor_difference_m: float


def _normalized_121_axis(values: np.ndarray, active: np.ndarray, axis: int) -> np.ndarray:
    """Apply one normalized 1-2-1 pass along *axis* on active cells only."""
    if axis not in (0, 1):
        raise ValueError("axis must be 0 or 1")

    numerator = 2.0 * values
    denominator = np.full(values.shape, 2.0, dtype=np.float64)

    if axis == 1:
        neighbor = active[:, :-1]
        numerator[:, 1:] += np.where(neighbor, values[:, :-1], 0.0)
        denominator[:, 1:] += neighbor
        neighbor = active[:, 1:]
        numerator[:, :-1] += np.where(neighbor, values[:, 1:], 0.0)
        denominator[:, :-1] += neighbor
    else:
        neighbor = active[:-1, :]
        numerator[1:, :] += np.where(neighbor, values[:-1, :], 0.0)
        denominator[1:, :] += neighbor
        neighbor = active[1:, :]
        numerator[:-1, :] += np.where(neighbor, values[1:, :], 0.0)
        denominator[:-1, :] += neighbor

    result = values.copy()
    result[active] = numerator[active] / denominator[active]
    return result


def filter_land_topography(
    topography_m: np.ndarray,
    landmask: np.ndarray,
    *,
    passes: int,
    order: int = 8,
    strength: float = 1.0,
    water_policy: str = "preserve",
    sea_level_m: float = 0.0,
) -> np.ndarray:
    """Return land-aware, high-order Shapiro-filtered topography.

    Let ``S`` be one normalized, separable 1-2-1 operator.  One Shapiro pass is
    ``I - strength * (I-S)**(order/2)``.  On an unmasked one-dimensional grid its
    transfer function is ``1-strength*sin(k*dx/2)**order``: the two-grid wave is
    damped while the response approaches one to high order at long wavelengths.
    Normalization prevents land and water from mixing.  With
    ``water_policy='preserve'`` water values are bitwise unchanged;
    ``'sea-level'`` explicitly sets every water cell to ``sea_level_m``.
    """
    topo = np.asarray(topography_m, dtype=np.float64)
    mask = np.asarray(landmask)
    if topo.ndim != 2 or mask.shape != topo.shape:
        raise ValueError(f"topography and landmask must be matching 2D arrays, got {topo.shape} and {mask.shape}")
    if not np.isfinite(topo).all():
        raise ValueError("topography contains non-finite values")
    if passes < 1:
        raise ValueError("passes must be at least 1")
    if order < 2 or order % 2 != 0:
        raise ValueError("order must be an even integer of at least 2")
    if not 0.0 < strength <= 1.0:
        raise ValueError("strength must be in (0, 1]")
    if water_policy not in {"preserve", "sea-level"}:
        raise ValueError("water_policy must be 'preserve' or 'sea-level'")

    active = mask != 0
    result = topo.copy()
    for _ in range(passes):
        highpass = result.copy()
        highpass[~active] = 0.0
        for _ in range(order // 2):
            smoothed = _normalized_121_axis(highpass, active, axis=1)
            smoothed = _normalized_121_axis(smoothed, active, axis=0)
            highpass[active] -= smoothed[active]
            highpass[~active] = 0.0
        result[active] -= strength * highpass[active]
        if water_policy == "sea-level":
            result[~active] = sea_level_m

    if water_policy == "preserve":
        result[~active] = topo[~active]
    return result.astype(np.float32)


def terrain_metrics(topography_m: np.ndarray, landmask: np.ndarray) -> TerrainMetrics:
    """Summarize terrain elevation and same-land-cell neighbor differences."""
    topo = np.asarray(topography_m, dtype=np.float64)
    land = np.asarray(landmask) != 0
    if topo.ndim != 2 or land.shape != topo.shape:
        raise ValueError("topography and landmask must be matching 2D arrays")
    if not np.isfinite(topo).all():
        raise ValueError("topography contains non-finite values")

    differences = []
    valid_x = land[:, 1:] & land[:, :-1]
    valid_y = land[1:, :] & land[:-1, :]
    if np.any(valid_x):
        differences.append(np.abs(topo[:, 1:] - topo[:, :-1])[valid_x])
    if np.any(valid_y):
        differences.append(np.abs(topo[1:, :] - topo[:-1, :])[valid_y])
    neighbor_differences = np.concatenate(differences) if differences else np.array([0.0])
    elevations = topo[land] if np.any(land) else topo.ravel()
    return TerrainMetrics(
        minimum_m=float(np.min(elevations)),
        mean_m=float(np.mean(elevations)),
        maximum_m=float(np.max(elevations)),
        max_land_neighbor_difference_m=float(np.max(neighbor_differences)),
        p999_land_neighbor_difference_m=float(np.percentile(neighbor_differences, 99.9)),
        mean_land_neighbor_difference_m=float(np.mean(neighbor_differences)),
    )


def nominal_shapiro_response(order: int, strength: float, wavelength_cells: float) -> float:
    """Return the one-dimensional unmasked amplitude response of one pass."""
    if order < 2 or order % 2 != 0:
        raise ValueError("order must be an even integer of at least 2")
    if wavelength_cells < 2.0:
        raise ValueError("wavelength_cells must be at least 2")
    return float(1.0 - strength * np.sin(np.pi / wavelength_cells) ** order)


def block_mean_change_metrics(
    unfiltered_m: np.ndarray,
    filtered_m: np.ndarray,
    landmask: np.ndarray,
    block_cells: int,
) -> dict[str, float | int]:
    """Measure residual terrain change after averaging into non-overlapping blocks."""
    if block_cells < 1:
        raise ValueError("block_cells must be positive")
    before = np.asarray(unfiltered_m, dtype=np.float64)
    after = np.asarray(filtered_m, dtype=np.float64)
    land = np.asarray(landmask) != 0
    if before.shape != after.shape or before.shape != land.shape or before.ndim != 2:
        raise ValueError("terrain arrays and landmask must be matching 2D arrays")

    ny = before.shape[0] // block_cells * block_cells
    nx = before.shape[1] // block_cells * block_cells
    if ny == 0 or nx == 0:
        raise ValueError("block size is larger than the terrain array")
    delta = (after[:ny, :nx] - before[:ny, :nx]) * land[:ny, :nx]
    weights = land[:ny, :nx].astype(np.float64)
    block_shape = (ny // block_cells, block_cells, nx // block_cells, block_cells)
    sums = delta.reshape(block_shape).sum(axis=(1, 3))
    counts = weights.reshape(block_shape).sum(axis=(1, 3))
    valid = counts > 0.0
    means = sums[valid] / counts[valid]
    return {
        "block_cells": block_cells,
        "blocks_with_land": int(means.size),
        "mean_change_m": float(np.mean(means)),
        "rms_change_m": float(np.sqrt(np.mean(means * means))),
        "p99_absolute_change_m": float(np.percentile(np.abs(means), 99.0)),
        "max_absolute_change_m": float(np.max(np.abs(means))),
    }
