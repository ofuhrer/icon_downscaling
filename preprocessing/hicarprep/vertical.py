"""Terrain-aware vertical reconstruction and hydrostatic target-column balance."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


GRAVITY = 9.80665
RD = 287.05
RV_OVER_RD_MINUS_ONE = 0.608


@dataclass(frozen=True)
class ColumnDiagnostics:
    terrain_difference_m: float
    terrain_case: str
    anchor_height_m: float
    below_source_level_count: int
    buried_source_level_count: int


def _blend_function(distance: np.ndarray, transition_distance: float) -> np.ndarray:
    """Blahak (2010) compact smooth transition used by int2lm."""
    if transition_distance <= 0.0:
        raise ValueError("transition distance must be positive")
    clipped = np.clip(
        np.asarray(distance, dtype=np.float64), -transition_distance, transition_distance
    )
    c = 3.0 * transition_distance
    return (9.0 * c * c * clipped + 27.0 * clipped**3) / (c**3 + 27.0 * c * clipped**2)


def adjust_vertical_velocity(
    *,
    target_hhl_m: np.ndarray,
    interpolated_w_ms: np.ndarray,
    u_ms: np.ndarray,
    v_ms: np.ndarray,
    x_m: np.ndarray,
    y_m: np.ndarray,
    blend_depth_m: float = 4_000.0,
) -> np.ndarray:
    """Blend W toward ``u dh/dx + v dh/dy`` and impose a quiet model top.

    This follows int2lm's terrain-following blend and ICON's explicit top
    boundary damping. Arrays use HICAR's bottom-to-top vertical ordering.
    """
    hhl = np.asarray(target_hhl_m, dtype=np.float64)
    w = np.asarray(interpolated_w_ms, dtype=np.float64).copy()
    u = np.asarray(u_ms, dtype=np.float64)
    v = np.asarray(v_ms, dtype=np.float64)
    x = np.asarray(x_m, dtype=np.float64)
    y = np.asarray(y_m, dtype=np.float64)
    if hhl.shape != w.shape or hhl.shape[0] != u.shape[0] + 1 or u.shape != v.shape:
        raise ValueError("W/HHL interfaces and U/V mass levels are inconsistent")
    if hhl.shape[1:] != u.shape[1:] or hhl.shape[1:] != (y.size, x.size):
        raise ValueError("target coordinate sizes do not match atmospheric fields")
    if x.size < 2 or y.size < 2 or np.any(np.diff(x) <= 0.0) or np.any(np.diff(y) <= 0.0):
        raise ValueError("terrain-W adjustment requires increasing x/y coordinates")

    mass_z = 0.5 * (hhl[:-1] + hhl[1:])
    u_interface = np.empty_like(hhl)
    v_interface = np.empty_like(hhl)
    u_interface[0], u_interface[-1] = u[0], u[-1]
    v_interface[0], v_interface[-1] = v[0], v[-1]
    fraction = (hhl[1:-1] - mass_z[:-1]) / (mass_z[1:] - mass_z[:-1])
    u_interface[1:-1] = u[:-1] + fraction * (u[1:] - u[:-1])
    v_interface[1:-1] = v[:-1] + fraction * (v[1:] - v[:-1])

    w_terrain = np.empty_like(hhl)
    edge_order = 2 if min(x.size, y.size) >= 3 else 1
    for level in range(hhl.shape[0]):
        dzdy, dzdx = np.gradient(hhl[level], y, x, edge_order=edge_order)
        w_terrain[level] = u_interface[level] * dzdx + v_interface[level] * dzdy
    agl = hhl - hhl[0]
    external_fraction = _blend_function(agl, blend_depth_m)
    w = external_fraction * w + (1.0 - external_fraction) * w_terrain
    w[0] = w_terrain[0]
    w[-1] = 0.0
    if w.shape[0] >= 3:
        w[-2] *= 0.33
    if w.shape[0] >= 4:
        w[-3] *= 0.66
    return w


def interpolate_interface_w_to_hfl(
    *,
    target_hhl_m: np.ndarray,
    target_hfl_m: np.ndarray,
    interface_w_ms: np.ndarray,
) -> np.ndarray:
    """Interpolate terrain-adjusted interface W to authoritative HFL heights."""
    hhl = np.asarray(target_hhl_m, dtype=np.float64)
    hfl = np.asarray(target_hfl_m, dtype=np.float64)
    w = np.asarray(interface_w_ms, dtype=np.float64)
    if hhl.shape != w.shape or hfl.shape != (hhl.shape[0] - 1, *hhl.shape[1:]):
        raise ValueError("W/HHL interfaces and HFL mass levels are inconsistent")
    if hhl.ndim != 3 or not np.isfinite(hfl).all():
        raise ValueError("target W interpolation requires finite three-dimensional geometry")
    if np.any((hfl <= hhl[:-1]) | (hfl >= hhl[1:])):
        raise ValueError("every HFL level must lie strictly between its HHL interfaces")
    result = np.empty_like(hfl)
    for row in range(hhl.shape[1]):
        for col in range(hhl.shape[2]):
            result[:, row, col] = interpolate_height_profile(
                hhl[:, row, col],
                w[:, row, col],
                hfl[:, row, col],
                monotone=True,
            )
    return result


def saturation_specific_humidity(temperature_k: np.ndarray, pressure_pa: np.ndarray) -> np.ndarray:
    """Liquid-water saturation specific humidity with safe pressure bounds."""
    temperature_k = np.asarray(temperature_k, dtype=np.float64)
    pressure_pa = np.asarray(pressure_pa, dtype=np.float64)
    tc = temperature_k - 273.15
    vapor_pressure = 610.94 * np.exp(17.625 * tc / (tc + 243.04))
    vapor_pressure = np.minimum(vapor_pressure, 0.99 * pressure_pa)
    epsilon = 0.622
    return (
        epsilon * vapor_pressure / np.maximum(pressure_pa - (1.0 - epsilon) * vapor_pressure, 1.0)
    )


def _ordered_profile(z: np.ndarray, value: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    z = np.asarray(z, dtype=np.float64)
    value = np.asarray(value, dtype=np.float64)
    if z.ndim != 1 or value.shape != z.shape:
        raise ValueError("each source profile must be one-dimensional and shape-compatible")
    order = np.argsort(z)
    z = z[order]
    value = value[order]
    if not np.isfinite(z).all() or not np.isfinite(value).all() or np.any(np.diff(z) <= 0.0):
        raise ValueError("source profile must be finite and strictly ordered in height")
    return z, value


def interpolate_height_profile(
    source_z: np.ndarray,
    source_value: np.ndarray,
    target_z: np.ndarray,
    *,
    lower_gradient_bounds: tuple[float, float] | None = None,
    nonnegative: bool = False,
    monotone: bool = True,
) -> np.ndarray:
    """Interpolate in geometric height with explicit bounded valley extrapolation."""
    source_z, source_value = _ordered_profile(source_z, source_value)
    target_z = np.asarray(target_z, dtype=np.float64)
    if not np.isfinite(target_z).all() or np.any(np.diff(target_z) <= 0.0):
        raise ValueError("target profile must be finite and strictly ordered in height")
    if target_z[-1] > source_z[-1] + 1.0e-6:
        raise ValueError(f"target top {target_z[-1]:.3f} m exceeds source top {source_z[-1]:.3f} m")
    result = np.interp(target_z, source_z, source_value)
    below = target_z < source_z[0]
    if np.any(below):
        gradient = (source_value[1] - source_value[0]) / (source_z[1] - source_z[0])
        if lower_gradient_bounds is not None:
            gradient = float(np.clip(gradient, *lower_gradient_bounds))
        result[below] = source_value[0] + gradient * (target_z[below] - source_z[0])
    if monotone:
        result = np.clip(result, np.min(source_value), np.max(source_value))
    if nonnegative:
        result = np.maximum(result, 0.0)
    return result


def hydrostatic_pressure(
    target_z: np.ndarray,
    temperature_k: np.ndarray,
    qv: np.ndarray,
    *,
    anchor_height_m: float,
    anchor_pressure_pa: float,
    condensate: np.ndarray | None = None,
) -> np.ndarray:
    """Integrate pressure both ways from an overlap anchor on target thermodynamics."""
    target_z = np.asarray(target_z, dtype=np.float64)
    temperature_k = np.asarray(temperature_k, dtype=np.float64)
    qv = np.asarray(qv, dtype=np.float64)
    condensate = (
        np.zeros_like(qv) if condensate is None else np.asarray(condensate, dtype=np.float64)
    )
    if not (target_z.shape == temperature_k.shape == qv.shape == condensate.shape):
        raise ValueError("target z, temperature, humidity, and condensate must have the same shape")
    if np.any(condensate < 0.0):
        raise ValueError("condensate loading must be nonnegative")
    if np.any(np.diff(target_z) <= 0.0) or anchor_pressure_pa <= 0.0:
        raise ValueError("invalid hydrostatic integration coordinate or anchor pressure")
    if anchor_height_m < target_z[0] or anchor_height_m > target_z[-1]:
        raise ValueError("hydrostatic anchor must lie within the target mass-level range")
    virtual_temperature = temperature_k * (1.0 + RV_OVER_RD_MINUS_ONE * qv - condensate)
    anchor_tv = np.interp(anchor_height_m, target_z, virtual_temperature)
    insert = int(np.searchsorted(target_z, anchor_height_m))
    if insert < target_z.size and np.isclose(target_z[insert], anchor_height_m):
        z = target_z.copy()
        tv = virtual_temperature
        anchor_index = insert
        target_indices = np.arange(target_z.size)
    else:
        z = np.insert(target_z, insert, anchor_height_m)
        tv = np.insert(virtual_temperature, insert, anchor_tv)
        anchor_index = insert
        target_indices = np.delete(np.arange(z.size), insert)
    logp = np.empty_like(z)
    logp[anchor_index] = np.log(anchor_pressure_pa)
    for level in range(anchor_index + 1, z.size):
        tv_mid = 0.5 * (tv[level - 1] + tv[level])
        logp[level] = logp[level - 1] - GRAVITY * (z[level] - z[level - 1]) / (RD * tv_mid)
    for level in range(anchor_index - 1, -1, -1):
        tv_mid = 0.5 * (tv[level + 1] + tv[level])
        logp[level] = logp[level + 1] + GRAVITY * (z[level + 1] - z[level]) / (RD * tv_mid)
    return np.exp(logp[target_indices])


def hydrostatic_residual(
    height_m: np.ndarray,
    pressure_pa: np.ndarray,
    temperature_k: np.ndarray,
    qv: np.ndarray,
    condensate: np.ndarray | None = None,
) -> np.ndarray:
    condensate = 0.0 if condensate is None else np.asarray(condensate)
    tv = np.asarray(temperature_k) * (1.0 + RV_OVER_RD_MINUS_ONE * np.asarray(qv) - condensate)
    expected = -GRAVITY * np.diff(height_m) / (RD * 0.5 * (tv[:-1] + tv[1:]))
    return np.diff(np.log(pressure_pa)) - expected


def reconstruct_column_state(
    *,
    source_hhl_m: np.ndarray,
    target_hhl_m: np.ndarray,
    temperature_k: np.ndarray,
    pressure_pa: np.ndarray,
    qv: np.ndarray,
    u_ms: np.ndarray,
    v_ms: np.ndarray,
    hydrometeors: dict[str, np.ndarray] | None = None,
) -> tuple[dict[str, np.ndarray], ColumnDiagnostics]:
    """Transform one horizontally remapped ICON column to final HICAR geometry."""
    source_hhl_m = np.asarray(source_hhl_m, dtype=np.float64)
    target_hhl_m = np.asarray(target_hhl_m, dtype=np.float64)
    if np.any(np.diff(source_hhl_m) <= 0.0) or np.any(np.diff(target_hhl_m) <= 0.0):
        raise ValueError("source and target HHL must be bottom-to-top and strictly increasing")
    source_z = 0.5 * (source_hhl_m[:-1] + source_hhl_m[1:])
    target_z = 0.5 * (target_hhl_m[:-1] + target_hhl_m[1:])
    fields = (temperature_k, pressure_pa, qv, u_ms, v_ms)
    if any(np.asarray(field).shape != source_z.shape for field in fields):
        raise ValueError("source full-level fields must match source HHL")
    if not all(np.isfinite(np.asarray(field)).all() for field in fields):
        raise ValueError("source column contains non-finite atmospheric values")
    if np.any(np.asarray(temperature_k) <= 100.0) or np.any(np.asarray(pressure_pa) <= 0.0):
        raise ValueError("source temperature or pressure is outside its physical domain")
    if np.any(np.asarray(qv) < 0.0):
        raise ValueError("source water-vapor mixing ratio is negative")
    if target_z[-1] > source_z[-1] + 1.0e-6:
        raise ValueError("target model top is above the usable source model top")

    source_surface = float(source_hhl_m[0])
    target_surface = float(target_hhl_m[0])
    delta = target_surface - source_surface
    terrain_case = "higher" if delta > 1.0 else "lower" if delta < -1.0 else "matched"
    # For a target mountain, do not let source boundary-layer points buried
    # below the new surface influence the reconstructed atmosphere. int2lm
    # performs a more elaborate PBL shrink; pruning plus bounded continuation
    # is the deliberately conservative linear counterpart used here.
    if terrain_case == "higher":
        usable = source_z > target_surface
        if np.count_nonzero(usable) < 2:
            raise ValueError("fewer than two source levels survive above target terrain")
    else:
        usable = np.ones(source_z.shape, dtype=bool)
    profile_z = source_z[usable]
    target_t = interpolate_height_profile(
        profile_z,
        np.asarray(temperature_k)[usable],
        target_z,
        lower_gradient_bounds=(-0.012, 0.003),
    )
    target_u = interpolate_height_profile(
        profile_z, np.asarray(u_ms)[usable], target_z, lower_gradient_bounds=(-0.02, 0.02)
    )
    target_v = interpolate_height_profile(
        profile_z, np.asarray(v_ms)[usable], target_z, lower_gradient_bounds=(-0.02, 0.02)
    )

    provisional_p = np.exp(
        interpolate_height_profile(
            profile_z, np.log(np.asarray(pressure_pa)[usable]), target_z, monotone=True
        )
    )
    source_hydrometeors: dict[str, np.ndarray] = {}
    for name, profile in (hydrometeors or {}).items():
        normalized = np.asarray(profile, dtype=np.float64)
        if normalized.shape != source_z.shape or not np.isfinite(normalized).all():
            raise ValueError(f"{name}: source hydrometeor profile is invalid")
        if np.any(normalized < 0.0):
            raise ValueError(f"{name}: source hydrometeor profile is negative")
        source_hydrometeors[name] = normalized

    has_qc = "QC" in source_hydrometeors
    source_qc = source_hydrometeors.get("QC", np.zeros_like(np.asarray(qv)))
    # int2lm's generalized RH transports total non-ice cloud water through the
    # terrain transform and splits it against saturation only after pressure is
    # reconstructed. This avoids independently interpolating QV and QC.
    source_rh = (np.asarray(qv) + source_qc) / np.maximum(
        saturation_specific_humidity(np.asarray(temperature_k), np.asarray(pressure_pa)),
        1.0e-12,
    )
    source_rh = np.clip(source_rh, 0.0, float(np.max(source_rh)) if has_qc else 1.0)
    target_rh = interpolate_height_profile(
        profile_z, source_rh[usable], target_z, lower_gradient_bounds=(0.0, 0.0)
    )
    target_other_hydrometeors = {
        name: interpolate_height_profile(
            profile_z,
            profile[usable],
            target_z,
            lower_gradient_bounds=(0.0, 0.0),
            nonnegative=True,
        )
        for name, profile in source_hydrometeors.items()
        if name != "QC"
    }
    target_qsat = saturation_specific_humidity(target_t, provisional_p)
    target_qv = np.minimum(target_rh, 1.0) * target_qsat
    target_qc = (
        np.maximum(target_rh - 1.0, 0.0) * target_qsat if has_qc else np.zeros_like(target_qv)
    )

    common_bottom = max(source_surface, target_surface)
    candidates = np.flatnonzero(
        (target_z >= common_bottom) & (target_z >= source_z[0]) & (target_z <= source_z[-1])
    )
    if candidates.size == 0:
        raise ValueError("source and target columns have no hydrostatic overlap anchor")
    target_anchor_index = int(candidates[0])
    anchor_z = float(target_z[target_anchor_index])
    anchor_p = float(np.exp(np.interp(anchor_z, source_z, np.log(np.asarray(pressure_pa)))))
    target_p = provisional_p
    for _ in range(8):
        target_qsat = saturation_specific_humidity(target_t, target_p)
        updated_qv = np.minimum(target_rh, 1.0) * target_qsat
        updated_qc = (
            np.maximum(target_rh - 1.0, 0.0) * target_qsat if has_qc else np.zeros_like(updated_qv)
        )
        condensate = updated_qc + sum(
            target_other_hydrometeors.values(), start=np.zeros_like(updated_qc)
        )
        updated_p = hydrostatic_pressure(
            target_z,
            target_t,
            updated_qv,
            anchor_height_m=anchor_z,
            anchor_pressure_pa=anchor_p,
            condensate=condensate,
        )
        converged = np.allclose(updated_p, target_p, rtol=1.0e-10, atol=1.0e-5)
        target_qv, target_qc, target_p = updated_qv, updated_qc, updated_p
        if converged:
            break

    result = {"T": target_t, "P": target_p, "QV": target_qv, "U": target_u, "V": target_v}
    if has_qc:
        result["QC"] = target_qc
    result.update(target_other_hydrometeors)
    result["THETA"] = target_t * np.power(100_000.0 / target_p, RD / 1004.5)
    condensate = target_qc + sum(target_other_hydrometeors.values(), start=np.zeros_like(target_qv))
    result["RHO"] = target_p / (
        RD * target_t * (1.0 + RV_OVER_RD_MINUS_ONE * target_qv - condensate)
    )
    diagnostics = ColumnDiagnostics(
        terrain_difference_m=delta,
        terrain_case=terrain_case,
        anchor_height_m=anchor_z,
        below_source_level_count=int(np.sum(target_z < source_z[0])),
        buried_source_level_count=int(np.sum(~usable)),
    )
    return result, diagnostics
