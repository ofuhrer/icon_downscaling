"""Compiled, low-allocation kernels for target vertical velocity."""

from __future__ import annotations

import numba
import numpy as np


def _gradient_stencil(coordinate: np.ndarray, edge_order: int) -> tuple[np.ndarray, np.ndarray]:
    """Return three-point indices and coefficients equivalent to ``np.gradient``."""
    coordinate = np.asarray(coordinate, dtype=np.float64)
    count = coordinate.size
    index = np.empty((count, 3), dtype=np.int64)
    coefficient = np.zeros((count, 3), dtype=np.float64)
    if edge_order == 1:
        index[0] = (0, 1, 1)
        coefficient[0, :2] = (
            -1.0 / (coordinate[1] - coordinate[0]),
            1.0 / (coordinate[1] - coordinate[0]),
        )
        index[-1] = (count - 2, count - 1, count - 1)
        coefficient[-1, :2] = (
            -1.0 / (coordinate[-1] - coordinate[-2]),
            1.0 / (coordinate[-1] - coordinate[-2]),
        )
    else:
        first = coordinate[1] - coordinate[0]
        second = coordinate[2] - coordinate[1]
        index[0] = (0, 1, 2)
        coefficient[0] = (
            -(2.0 * first + second) / (first * (first + second)),
            (first + second) / (first * second),
            -first / (second * (first + second)),
        )
        first = coordinate[-2] - coordinate[-3]
        second = coordinate[-1] - coordinate[-2]
        index[-1] = (count - 3, count - 2, count - 1)
        coefficient[-1] = (
            second / (first * (first + second)),
            -(first + second) / (first * second),
            (2.0 * second + first) / (second * (first + second)),
        )
    for position in range(1, count - 1):
        first = coordinate[position] - coordinate[position - 1]
        second = coordinate[position + 1] - coordinate[position]
        index[position] = (position - 1, position, position + 1)
        coefficient[position] = (
            -second / (first * (first + second)),
            (second - first) / (first * second),
            first / (second * (first + second)),
        )
    return index, coefficient


@numba.njit(inline="always")
def _interface_w(
    level: int,
    row: int,
    column: int,
    hhl: np.ndarray,
    interpolated_w: np.ndarray,
    u: np.ndarray,
    v: np.ndarray,
    sine: np.ndarray,
    cosine: np.ndarray,
    x_index: np.ndarray,
    x_coefficient: np.ndarray,
    y_index: np.ndarray,
    y_coefficient: np.ndarray,
    blend_depth_m: float,
) -> float:
    dzdx = 0.0
    dzdy = 0.0
    for point in range(3):
        dzdx += x_coefficient[column, point] * hhl[level, row, x_index[column, point]]
        dzdy += y_coefficient[row, point] * hhl[level, y_index[row, point], column]

    mass_count = u.shape[0]
    if level == 0:
        u_grid = u[0, row, column] * cosine[row, column] - v[0, row, column] * sine[row, column]
        v_grid = v[0, row, column] * cosine[row, column] + u[0, row, column] * sine[row, column]
    elif level == mass_count:
        u_grid = (
            u[mass_count - 1, row, column] * cosine[row, column]
            - v[mass_count - 1, row, column] * sine[row, column]
        )
        v_grid = (
            v[mass_count - 1, row, column] * cosine[row, column]
            + u[mass_count - 1, row, column] * sine[row, column]
        )
    else:
        lower_mass_z = 0.5 * (hhl[level - 1, row, column] + hhl[level, row, column])
        upper_mass_z = 0.5 * (hhl[level, row, column] + hhl[level + 1, row, column])
        fraction = (hhl[level, row, column] - lower_mass_z) / (upper_mass_z - lower_mass_z)
        lower_u = (
            u[level - 1, row, column] * cosine[row, column]
            - v[level - 1, row, column] * sine[row, column]
        )
        lower_v = (
            v[level - 1, row, column] * cosine[row, column]
            + u[level - 1, row, column] * sine[row, column]
        )
        upper_u = (
            u[level, row, column] * cosine[row, column] - v[level, row, column] * sine[row, column]
        )
        upper_v = (
            v[level, row, column] * cosine[row, column] + u[level, row, column] * sine[row, column]
        )
        u_grid = lower_u + fraction * (upper_u - lower_u)
        v_grid = lower_v + fraction * (upper_v - lower_v)

    terrain_w = u_grid * dzdx + v_grid * dzdy
    if level == 0:
        return terrain_w
    if level == mass_count:
        return 0.0

    distance = hhl[level, row, column] - hhl[0, row, column]
    if distance > blend_depth_m:
        distance = blend_depth_m
    elif distance < -blend_depth_m:
        distance = -blend_depth_m
    c = 3.0 * blend_depth_m
    external_fraction = (9.0 * c * c * distance + 27.0 * distance**3) / (
        c**3 + 27.0 * c * distance**2
    )
    adjusted = (
        external_fraction * interpolated_w[level, row, column]
        + (1.0 - external_fraction) * terrain_w
    )
    if level == mass_count - 1 and mass_count + 1 >= 3:
        adjusted *= 0.33
    elif level == mass_count - 2 and mass_count + 1 >= 4:
        adjusted *= 0.66
    return adjusted


@numba.njit(cache=True, parallel=True)
def _adjust_vertical_velocity_to_hfl(
    hhl: np.ndarray,
    hfl: np.ndarray,
    interpolated_w: np.ndarray,
    u: np.ndarray,
    v: np.ndarray,
    sine: np.ndarray,
    cosine: np.ndarray,
    x_index: np.ndarray,
    x_coefficient: np.ndarray,
    y_index: np.ndarray,
    y_coefficient: np.ndarray,
    blend_depth_m: float,
) -> np.ndarray:
    mass_count, row_count, column_count = hfl.shape
    result = np.empty_like(hfl)
    for level in numba.prange(mass_count):
        for row in range(row_count):
            for column in range(column_count):
                lower_w = _interface_w(
                    level,
                    row,
                    column,
                    hhl,
                    interpolated_w,
                    u,
                    v,
                    sine,
                    cosine,
                    x_index,
                    x_coefficient,
                    y_index,
                    y_coefficient,
                    blend_depth_m,
                )
                upper_w = _interface_w(
                    level + 1,
                    row,
                    column,
                    hhl,
                    interpolated_w,
                    u,
                    v,
                    sine,
                    cosine,
                    x_index,
                    x_coefficient,
                    y_index,
                    y_coefficient,
                    blend_depth_m,
                )
                fraction = (hfl[level, row, column] - hhl[level, row, column]) / (
                    hhl[level + 1, row, column] - hhl[level, row, column]
                )
                result[level, row, column] = lower_w + fraction * (upper_w - lower_w)
    return result


def adjust_vertical_velocity_to_hfl(
    *,
    target_hhl_m: np.ndarray,
    target_hfl_m: np.ndarray,
    interpolated_w_ms: np.ndarray,
    u_ms: np.ndarray,
    v_ms: np.ndarray,
    grid_sintheta: np.ndarray,
    grid_costheta: np.ndarray,
    x_m: np.ndarray,
    y_m: np.ndarray,
    blend_depth_m: float = 4_000.0,
) -> np.ndarray:
    """Fuse terrain-W adjustment and HFL interpolation without 3-D temporaries."""
    hhl = np.ascontiguousarray(target_hhl_m, dtype=np.float64)
    hfl = np.ascontiguousarray(target_hfl_m, dtype=np.float64)
    interpolated_w = np.ascontiguousarray(interpolated_w_ms, dtype=np.float64)
    u = np.ascontiguousarray(u_ms, dtype=np.float64)
    v = np.ascontiguousarray(v_ms, dtype=np.float64)
    sine = np.ascontiguousarray(grid_sintheta, dtype=np.float64)
    cosine = np.ascontiguousarray(grid_costheta, dtype=np.float64)
    x = np.asarray(x_m, dtype=np.float64)
    y = np.asarray(y_m, dtype=np.float64)
    if hhl.ndim != 3 or hhl.shape != interpolated_w.shape:
        raise ValueError("W and HHL must be shape-compatible three-dimensional arrays")
    if (
        hfl.shape != (hhl.shape[0] - 1, *hhl.shape[1:])
        or u.shape != hfl.shape
        or v.shape != hfl.shape
    ):
        raise ValueError("HFL and U/V must match the HHL mass-level geometry")
    if sine.shape != hhl.shape[1:] or cosine.shape != sine.shape:
        raise ValueError("grid-rotation components must match the horizontal target grid")
    if hhl.shape[1:] != (y.size, x.size):
        raise ValueError("target coordinate sizes do not match atmospheric fields")
    if x.size < 2 or y.size < 2 or np.any(np.diff(x) <= 0.0) or np.any(np.diff(y) <= 0.0):
        raise ValueError("terrain-W adjustment requires increasing x/y coordinates")
    if not np.isfinite(hfl).all() or np.any((hfl <= hhl[:-1]) | (hfl >= hhl[1:])):
        raise ValueError("every HFL level must be finite and strictly between its HHL interfaces")
    if not np.isfinite(blend_depth_m) or blend_depth_m <= 0.0:
        raise ValueError("transition distance must be positive")

    edge_order = 2 if min(x.size, y.size) >= 3 else 1
    x_index, x_coefficient = _gradient_stencil(x, edge_order)
    y_index, y_coefficient = _gradient_stencil(y, edge_order)
    return _adjust_vertical_velocity_to_hfl(
        hhl,
        hfl,
        interpolated_w,
        u,
        v,
        sine,
        cosine,
        x_index,
        x_coefficient,
        y_index,
        y_coefficient,
        float(blend_depth_m),
    )
