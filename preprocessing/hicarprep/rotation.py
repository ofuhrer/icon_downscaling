"""HICAR-compatible rotations between geographic and target-grid winds."""

from __future__ import annotations

import numpy as np


def _truncated_box_mean(values: np.ndarray, half_width: int) -> np.ndarray:
    """Average a square window, truncating it at the array boundaries."""
    if half_width < 0:
        raise ValueError("smoothing half-width must be nonnegative")
    if half_width == 0:
        return values.copy()
    ny, nx = values.shape
    y = np.arange(ny)
    x = np.arange(nx)
    y0, y1 = np.maximum(y - half_width, 0), np.minimum(y + half_width, ny - 1)
    x0, x1 = np.maximum(x - half_width, 0), np.minimum(x + half_width, nx - 1)
    integral = np.pad(values, ((1, 0), (1, 0))).cumsum(axis=0).cumsum(axis=1)
    totals = (
        integral[y1[:, None] + 1, x1[None, :] + 1]
        - integral[y0[:, None], x1[None, :] + 1]
        - integral[y1[:, None] + 1, x0[None, :]]
        + integral[y0[:, None], x0[None, :]]
    )
    counts = (y1 - y0 + 1)[:, None] * (x1 - x0 + 1)[None, :]
    return totals / counts


def hicar_grid_rotation(
    latitude_deg: np.ndarray,
    longitude_deg: np.ndarray,
    *,
    dx_m: float,
    smoothing_distance_m: float = 1_000.0,
    smoothing_half_width_cells: int = 4,
) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(sintheta, costheta)`` using HICAR's target-grid convention.

    This is the NumPy equivalent of ``domain_obj.setup_grid_rotations``:
    orientation is diagnosed from the latitude/longitude change over two
    target-grid cells in either x direction, then each component receives the
    same repeated truncated-window smoothing used by HICAR.
    """
    latitude = np.asarray(latitude_deg, dtype=np.float64)
    longitude = np.asarray(longitude_deg, dtype=np.float64)
    if latitude.ndim != 2 or longitude.shape != latitude.shape:
        raise ValueError("target latitude/longitude must share a two-dimensional shape")
    if latitude.shape[1] < 2 or not np.isfinite(latitude).all() or not np.isfinite(
        longitude
    ).all():
        raise ValueError("target latitude/longitude must be finite with at least two x cells")
    if not np.isfinite(dx_m) or dx_m <= 0.0:
        raise ValueError("target grid spacing must be positive and finite")
    if not np.isfinite(smoothing_distance_m) or smoothing_distance_m < 0.0:
        raise ValueError("rotation smoothing distance must be nonnegative and finite")
    if smoothing_half_width_cells < 0:
        raise ValueError("rotation smoothing half-width must be nonnegative")

    nx = latitude.shape[1]
    center = np.arange(nx)
    left = np.maximum(center - 2, 0)
    right = np.minimum(center + 2, nx - 1)
    dlat = latitude[:, right] - latitude[:, left]
    dlon = (longitude[:, right] - longitude[:, left]) * np.cos(
        np.deg2rad(latitude)
    )
    distance = np.hypot(dlat, dlon)
    if np.any(distance <= 0.0) or not np.isfinite(distance).all():
        raise ValueError("target x direction is degenerate in latitude/longitude space")
    sintheta = -dlat / distance
    costheta = np.abs(dlon / distance)

    smoothing_passes = int(smoothing_distance_m / dx_m)
    for _ in range(smoothing_passes):
        sintheta = _truncated_box_mean(sintheta, smoothing_half_width_cells)
        costheta = _truncated_box_mean(costheta, smoothing_half_width_cells)
    return sintheta, costheta


def earth_to_grid_wind(
    u_east_ms: np.ndarray,
    v_north_ms: np.ndarray,
    sintheta: np.ndarray,
    costheta: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Rotate mass-grid earth-relative components with HICAR's sign convention."""
    u_east = np.asarray(u_east_ms, dtype=np.float64)
    v_north = np.asarray(v_north_ms, dtype=np.float64)
    sine = np.asarray(sintheta, dtype=np.float64)
    cosine = np.asarray(costheta, dtype=np.float64)
    if u_east.shape != v_north.shape or sine.shape != cosine.shape:
        raise ValueError("wind components and grid-rotation components must have matching shapes")
    if u_east.shape[-2:] != sine.shape:
        raise ValueError("wind horizontal shape differs from the target-grid rotation")
    return u_east * cosine - v_north * sine, v_north * cosine + u_east * sine


def grid_to_earth_wind(
    u_grid_ms: np.ndarray,
    v_grid_ms: np.ndarray,
    sintheta: np.ndarray,
    costheta: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Invert :func:`earth_to_grid_wind`, including smoothed non-unit angles."""
    u_grid = np.asarray(u_grid_ms, dtype=np.float64)
    v_grid = np.asarray(v_grid_ms, dtype=np.float64)
    sine = np.asarray(sintheta, dtype=np.float64)
    cosine = np.asarray(costheta, dtype=np.float64)
    if u_grid.shape != v_grid.shape or sine.shape != cosine.shape:
        raise ValueError("wind components and grid-rotation components must have matching shapes")
    if u_grid.shape[-2:] != sine.shape:
        raise ValueError("wind horizontal shape differs from the target-grid rotation")
    determinant = cosine * cosine + sine * sine
    if np.any(determinant <= np.finfo(np.float64).eps):
        raise ValueError("target-grid rotation is singular")
    return (
        (u_grid * cosine + v_grid * sine) / determinant,
        (v_grid * cosine - u_grid * sine) / determinant,
    )
