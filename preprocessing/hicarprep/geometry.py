"""HICAR SLEVE geometry construction using the model's discrete definition."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class SleveConfig:
    nz: int = 80
    model_top_m: float = 12_000.0
    lowest_layer_m: float = 15.0
    stretch_factor: float = 0.65
    decay_rate_large: float = 2.0
    decay_rate_small: float = 6.0
    exponent: float = 1.35
    smooth_window_radius: int = 5
    smooth_cycles: int = 10
    minimum_jacobian: float = 0.0
    minimum_layer_thickness_m: float = 0.0


def auto_level_one(config: SleveConfig) -> np.ndarray:
    """Reproduce HICAR ``auto_level=1`` interface spacing."""
    n = config.nz
    x1 = (2.0 * config.stretch_factor - 1.0) * config.lowest_layer_m
    b = (config.model_top_m - (x1 / 6.0) * n**3 - (config.lowest_layer_m - x1 / 6.0) * n) / (
        n**2 - n**3 / 3.0 - 2.0 * n / 3.0
    )
    a = (x1 - 2.0 * b) / 6.0
    c = config.lowest_layer_m - (a + b)
    index = np.arange(n + 1, dtype=np.float64)
    interfaces = a * index**3 + b * index**2 + c * index
    thickness = np.diff(interfaces)
    if not np.isclose(interfaces[-1], config.model_top_m, rtol=0.01):
        raise ValueError("auto-level grid does not reach the requested model top")
    if np.any(thickness <= 0.0):
        raise ValueError("auto-level grid contains a non-positive layer")
    return thickness


def smooth_large_scale(terrain: np.ndarray, radius: int, cycles: int) -> np.ndarray:
    """Apply HICAR's truncated square-window smoothing to obtain SLEVE h1."""
    terrain = np.asarray(terrain, dtype=np.float64)
    if terrain.ndim != 2 or not np.isfinite(terrain).all():
        raise ValueError("terrain must be a finite two-dimensional array")
    if radius < 0 or cycles < 0:
        raise ValueError("smoothing radius and cycles must be non-negative")
    if radius == 0 or cycles == 0:
        return terrain.copy()
    size = 2 * radius + 1
    ny, nx = terrain.shape
    row_count = np.minimum(np.arange(ny) + radius + 1, ny) - np.maximum(np.arange(ny) - radius, 0)
    col_count = np.minimum(np.arange(nx) + radius + 1, nx) - np.maximum(np.arange(nx) - radius, 0)
    counts = row_count[:, None] * col_count[None, :]
    result = terrain.copy()
    for _ in range(cycles):
        padded = np.pad(result, radius, mode="constant")
        integral = np.pad(padded, ((1, 0), (1, 0)), mode="constant").cumsum(0).cumsum(1)
        sums = (
            integral[size:, size:]
            - integral[:-size, size:]
            - integral[size:, :-size]
            + integral[:-size, :-size]
        )
        result = sums / counts
    return result


def _basis(zeta: float, top: float, decay_rate: float, exponent: float) -> tuple[float, float]:
    scale = top / decay_rate
    top_term = (top / scale) ** exponent
    denominator = np.sinh(top_term)
    coordinate = (zeta / scale) ** exponent
    value = np.sinh(top_term - coordinate) / denominator
    derivative = (
        -exponent
        / scale**exponent
        * zeta ** (exponent - 1.0)
        * np.cosh(top_term - coordinate)
        / denominator
    )
    return float(value), float(derivative)


def build_sleve_geometry(
    terrain: np.ndarray, config: SleveConfig = SleveConfig()
) -> dict[str, np.ndarray]:
    """Construct bottom-to-top HHL/HFL and reject inverted target geometry."""
    terrain = np.asarray(terrain, dtype=np.float64)
    dz = auto_level_one(config)
    h1 = smooth_large_scale(terrain, config.smooth_window_radius, config.smooth_cycles)
    h2 = terrain - h1
    hhl = np.empty((config.nz + 1, *terrain.shape), dtype=np.float64)
    hfl = np.empty((config.nz, *terrain.shape), dtype=np.float64)
    jacobian = np.empty_like(hfl)
    hhl[0] = terrain
    cumulative = 0.0
    for level, layer in enumerate(dz):
        mass_zeta = cumulative + 0.5 * layer
        interface_zeta = cumulative + layer
        b1m, db1m = _basis(mass_zeta, config.model_top_m, config.decay_rate_large, config.exponent)
        b2m, db2m = _basis(mass_zeta, config.model_top_m, config.decay_rate_small, config.exponent)
        b1i, _ = _basis(
            interface_zeta, config.model_top_m, config.decay_rate_large, config.exponent
        )
        b2i, _ = _basis(
            interface_zeta, config.model_top_m, config.decay_rate_small, config.exponent
        )
        hfl[level] = mass_zeta + h1 * b1m + h2 * b2m
        hhl[level + 1] = interface_zeta + h1 * b1i + h2 * b2i
        jacobian[level] = 1.0 + h1 * db1m + h2 * db2m
        cumulative = interface_zeta

    layer_thickness = np.diff(hhl, axis=0)
    min_jacobian = float(np.min(jacobian))
    min_thickness = float(np.min(layer_thickness))
    if min_jacobian <= config.minimum_jacobian:
        raise ValueError(
            f"SLEVE minimum Jacobian {min_jacobian:.6g} is not above {config.minimum_jacobian:.6g}"
        )
    if min_thickness <= config.minimum_layer_thickness_m:
        raise ValueError(
            f"SLEVE minimum layer thickness {min_thickness:.6g} m is not above "
            f"{config.minimum_layer_thickness_m:.6g} m"
        )
    return {
        "terrain_large_scale": h1,
        "terrain_small_scale": h2,
        "HHL": hhl,
        "HFL": hfl,
        "SLEVE_JACOBIAN": jacobian,
        "LAYER_THICKNESS": layer_thickness,
        "reference_layer_thickness": dz,
    }
