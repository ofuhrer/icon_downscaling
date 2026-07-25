#!/usr/bin/env python3
"""Create a published HICAR static file with REA-L land and snow state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
from tempfile import NamedTemporaryFile

import netCDF4
import numpy as np


REA_L_T_SO_DEPTHS_M = np.array((0.0, 0.005, 0.02, 0.06, 0.18, 0.54, 1.62, 4.86))
REA_L_W_SO_BOUNDS_M = np.array(
    (0.0, 0.01, 0.03, 0.09, 0.27, 0.81, 2.43, 7.29, 21.87)
)
HICAR_SOIL_BOUNDS_M = np.array((0.0, 0.1, 0.3, 0.7, 1.5))
HICAR_SOIL_MIDPOINTS_M = 0.5 * (
    HICAR_SOIL_BOUNDS_M[:-1] + HICAR_SOIL_BOUNDS_M[1:]
)
WATER_DENSITY_KG_M3 = 1000.0


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def remap_soil_temperature(
    values: np.ndarray,
    source_depths_m: np.ndarray = REA_L_T_SO_DEPTHS_M,
    target_depths_m: np.ndarray = HICAR_SOIL_MIDPOINTS_M,
) -> np.ndarray:
    """Linearly interpolate point temperatures with constant end extrapolation."""
    values = np.asarray(values, dtype=np.float64)
    source_depths_m = np.asarray(source_depths_m, dtype=np.float64)
    target_depths_m = np.asarray(target_depths_m, dtype=np.float64)
    if values.shape[0] != source_depths_m.size:
        raise ValueError("T_SO layer count disagrees with source depths")
    if np.any(np.diff(source_depths_m) <= 0):
        raise ValueError("T_SO source depths must be strictly increasing")
    flat = values.reshape(values.shape[0], -1)
    result = np.empty((target_depths_m.size, flat.shape[1]), dtype=np.float64)
    for column in range(flat.shape[1]):
        result[:, column] = np.interp(
            target_depths_m,
            source_depths_m,
            flat[:, column],
            left=flat[0, column],
            right=flat[-1, column],
        )
    return result.reshape((target_depths_m.size,) + values.shape[1:])


def remap_layer_integrated_soil_water(
    mass_kg_m2: np.ndarray,
    source_bounds_m: np.ndarray = REA_L_W_SO_BOUNDS_M,
    target_bounds_m: np.ndarray = HICAR_SOIL_BOUNDS_M,
) -> tuple[np.ndarray, np.ndarray]:
    """Conservatively map layer water mass and return target VWC and mass."""
    mass_kg_m2 = np.asarray(mass_kg_m2, dtype=np.float64)
    source_bounds_m = np.asarray(source_bounds_m, dtype=np.float64)
    target_bounds_m = np.asarray(target_bounds_m, dtype=np.float64)
    if mass_kg_m2.shape[0] + 1 != source_bounds_m.size:
        raise ValueError("W_SO layer count disagrees with source bounds")
    if np.any(np.diff(source_bounds_m) <= 0) or np.any(np.diff(target_bounds_m) <= 0):
        raise ValueError("soil-layer bounds must be strictly increasing")
    source_thickness = np.diff(source_bounds_m)
    target_thickness = np.diff(target_bounds_m)
    target_mass = np.zeros((target_thickness.size,) + mass_kg_m2.shape[1:])
    for target_index, (target_top, target_bottom) in enumerate(
        zip(target_bounds_m[:-1], target_bounds_m[1:])
    ):
        for source_index, (source_top, source_bottom) in enumerate(
            zip(source_bounds_m[:-1], source_bounds_m[1:])
        ):
            overlap = max(
                0.0, min(target_bottom, source_bottom) - max(target_top, source_top)
            )
            if overlap:
                target_mass[target_index] += (
                    mass_kg_m2[source_index] * overlap / source_thickness[source_index]
                )
    target_vwc = target_mass / (
        WATER_DENSITY_KG_M3 * target_thickness[:, np.newaxis, np.newaxis]
    )
    return target_vwc, target_mass


def derive_snow_height(
    swe_kg_m2: np.ndarray,
    density_kg_m3: np.ndarray,
    fallback_density_kg_m3: float | None = None,
) -> np.ndarray:
    """Convert SWE mass per area to snow height, failing on invalid snowy density."""
    swe = np.asarray(swe_kg_m2, dtype=np.float64)
    density = np.asarray(density_kg_m3, dtype=np.float64).copy()
    invalid = ~np.isfinite(density) | (density <= 0)
    invalid_snow = invalid & np.isfinite(swe) & (swe > 0)
    if np.any(invalid_snow):
        if fallback_density_kg_m3 is None:
            raise ValueError("positive W_SNOW has missing or non-positive RHO_SNOW")
        if fallback_density_kg_m3 <= 0:
            raise ValueError("snow-density fallback must be positive")
        density[invalid_snow] = fallback_density_kg_m3
    height = np.zeros_like(swe)
    snowy = np.isfinite(swe) & (swe > 0)
    height[snowy] = swe[snowy] / density[snowy]
    return height


def regular_grid_axes(
    latitude: np.ndarray, longitude: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    latitude = np.asarray(latitude, dtype=np.float64)
    longitude = np.asarray(longitude, dtype=np.float64)
    if latitude.ndim == longitude.ndim == 1:
        return latitude, longitude
    if latitude.ndim != 2 or longitude.ndim != 2 or latitude.shape != longitude.shape:
        raise ValueError("source latitude/longitude must be matching 1-D or 2-D arrays")
    lat_axis = latitude[:, 0]
    lon_axis = longitude[0, :]
    if not np.allclose(latitude, lat_axis[:, None], atol=1.0e-7):
        raise ValueError("source latitude is not a rectilinear grid")
    if not np.allclose(longitude, lon_axis[None, :], atol=1.0e-7):
        raise ValueError("source longitude is not a rectilinear grid")
    return lat_axis, lon_axis


def bilinear_regrid(
    values: np.ndarray,
    source_latitude: np.ndarray,
    source_longitude: np.ndarray,
    target_latitude: np.ndarray,
    target_longitude: np.ndarray,
) -> np.ndarray:
    """Bilinearly sample a rectilinear latitude/longitude field."""
    values = np.asarray(values, dtype=np.float64)
    lat_axis, lon_axis = regular_grid_axes(source_latitude, source_longitude)
    if values.shape[-2:] != (lat_axis.size, lon_axis.size):
        raise ValueError("field shape disagrees with source latitude/longitude grid")
    if lat_axis[0] > lat_axis[-1]:
        lat_axis = lat_axis[::-1]
        values = values[..., ::-1, :]
    if lon_axis[0] > lon_axis[-1]:
        lon_axis = lon_axis[::-1]
        values = values[..., :, ::-1]
    if np.any(np.diff(lat_axis) <= 0) or np.any(np.diff(lon_axis) <= 0):
        raise ValueError("source latitude/longitude axes must be strictly monotonic")

    target_latitude = np.asarray(target_latitude, dtype=np.float64)
    target_longitude = np.asarray(target_longitude, dtype=np.float64)
    if (
        np.min(target_latitude) < lat_axis[0]
        or np.max(target_latitude) > lat_axis[-1]
        or np.min(target_longitude) < lon_axis[0]
        or np.max(target_longitude) > lon_axis[-1]
    ):
        raise ValueError("target domain lies outside the source land-state grid")

    lat_hi = np.searchsorted(lat_axis, target_latitude, side="right")
    lon_hi = np.searchsorted(lon_axis, target_longitude, side="right")
    lat_hi = np.clip(lat_hi, 1, lat_axis.size - 1)
    lon_hi = np.clip(lon_hi, 1, lon_axis.size - 1)
    lat_lo = lat_hi - 1
    lon_lo = lon_hi - 1
    lat_weight = (target_latitude - lat_axis[lat_lo]) / (
        lat_axis[lat_hi] - lat_axis[lat_lo]
    )
    lon_weight = (target_longitude - lon_axis[lon_lo]) / (
        lon_axis[lon_hi] - lon_axis[lon_lo]
    )
    weights = (
        (1.0 - lat_weight) * (1.0 - lon_weight),
        (1.0 - lat_weight) * lon_weight,
        lat_weight * (1.0 - lon_weight),
        lat_weight * lon_weight,
    )
    corners = (
        values[..., lat_lo, lon_lo],
        values[..., lat_lo, lon_hi],
        values[..., lat_hi, lon_lo],
        values[..., lat_hi, lon_hi],
    )
    result = np.zeros(corners[0].shape, dtype=np.float64)
    valid_weight = np.zeros(corners[0].shape, dtype=np.float64)
    for corner, weight in zip(corners, weights):
        valid = np.isfinite(corner)
        result += np.where(valid, corner, 0.0) * weight
        valid_weight += valid * weight
    result = np.divide(
        result,
        valid_weight,
        out=np.full_like(result, np.nan),
        where=valid_weight > 0,
    )
    return result


def as_yx(variable: netCDF4.Variable, ny: int, nx: int) -> np.ndarray:
    values = np.asarray(variable[:])
    if values.shape == (ny, nx):
        return values
    if values.shape == (nx, ny):
        return values.T
    raise ValueError(f"{variable.name} shape {values.shape} is not {(ny, nx)}")


def as_layer_yx(variable: netCDF4.Variable, ny: int, nx: int) -> np.ndarray:
    values = np.asarray(variable[:])
    if values.ndim != 3:
        raise ValueError(f"{variable.name} must have three dimensions")
    if values.shape[1:] == (ny, nx):
        return values
    if values.shape[:2] == (ny, nx):
        return np.moveaxis(values, 2, 0)
    if values.shape[1:] == (nx, ny):
        return values.transpose(0, 2, 1)
    raise ValueError(
        f"{variable.name} shape {values.shape} does not contain target y/x shape"
    )


def assert_land_range(
    name: str, values: np.ndarray, land: np.ndarray, lower: float, upper: float
) -> None:
    selected = values[..., land]
    if selected.size == 0:
        raise ValueError("static domain contains no land cells")
    if not np.all(np.isfinite(selected)):
        raise ValueError(f"{name} contains non-finite values over land")
    if np.min(selected) < lower or np.max(selected) > upper:
        raise ValueError(
            f"{name} range over land {np.min(selected)}..{np.max(selected)} "
            f"is outside {lower}..{upper}"
        )


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--static-template", required=True, type=Path)
    parser.add_argument("--land-state", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--valid-time", required=True)
    parser.add_argument("--surface-temperature-var", default="SKT")
    parser.add_argument("--soil-temperature-var", default="T_SO")
    parser.add_argument("--soil-water-var", default="W_SO")
    parser.add_argument("--swe-var", default="W_SNOW")
    parser.add_argument("--snow-density-var", default="RHO_SNOW")
    parser.add_argument("--source-lat-var", default="lat_1")
    parser.add_argument("--source-lon-var", default="lon_1")
    parser.add_argument("--snow-density-fallback", type=float)
    args = parser.parse_args()

    for path in (args.static_template, args.land_state):
        if not path.is_file() or not path.stat().st_size:
            raise SystemExit(f"missing or empty input: {path}")
    if args.output.exists() or Path(f"{args.output}.ready").exists():
        raise SystemExit(f"refusing to overwrite published output: {args.output}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    partial = args.output.with_name(f".{args.output.name}.partial.{os.getpid()}")
    if partial.exists():
        partial.unlink()
    shutil.copy2(args.static_template, partial)

    try:
        with netCDF4.Dataset(partial, "r+") as static, netCDF4.Dataset(
            args.land_state
        ) as source:
            landmask = np.asarray(static.variables["landmask"][:]) > 0
            ny, nx = landmask.shape
            target_latitude = np.asarray(static.variables["lat"][:])
            target_longitude = np.asarray(static.variables["lon"][:])
            source_latitude = np.asarray(source.variables[args.source_lat_var][:])
            source_longitude = np.asarray(source.variables[args.source_lon_var][:])
            source_lat_axis, source_lon_axis = regular_grid_axes(
                source_latitude, source_longitude
            )
            source_ny, source_nx = source_lat_axis.size, source_lon_axis.size
            surface = as_yx(
                source.variables[args.surface_temperature_var], source_ny, source_nx
            )
            source_temperature = as_layer_yx(
                source.variables[args.soil_temperature_var], source_ny, source_nx
            )
            source_water = as_layer_yx(
                source.variables[args.soil_water_var], source_ny, source_nx
            )
            swe = as_yx(source.variables[args.swe_var], source_ny, source_nx)
            snow_density = as_yx(
                source.variables[args.snow_density_var], source_ny, source_nx
            )
            same_grid = (
                (source_ny, source_nx) == (ny, nx)
                and source_latitude.shape == target_latitude.shape
                and source_longitude.shape == target_longitude.shape
                and np.allclose(source_latitude, target_latitude, atol=1.0e-7)
                and np.allclose(source_longitude, target_longitude, atol=1.0e-7)
            )
            if not same_grid:
                surface = bilinear_regrid(
                    surface,
                    source_latitude,
                    source_longitude,
                    target_latitude,
                    target_longitude,
                )
                source_temperature = bilinear_regrid(
                    source_temperature,
                    source_latitude,
                    source_longitude,
                    target_latitude,
                    target_longitude,
                )
                source_water = bilinear_regrid(
                    source_water,
                    source_latitude,
                    source_longitude,
                    target_latitude,
                    target_longitude,
                )
                swe = bilinear_regrid(
                    swe,
                    source_latitude,
                    source_longitude,
                    target_latitude,
                    target_longitude,
                )
                snow_density = bilinear_regrid(
                    snow_density,
                    source_latitude,
                    source_longitude,
                    target_latitude,
                    target_longitude,
                )

            soil_temperature = remap_soil_temperature(source_temperature)
            soil_vwc, target_water_mass = remap_layer_integrated_soil_water(
                source_water
            )
            snow_height = derive_snow_height(
                swe, snow_density, args.snow_density_fallback
            )

            assert_land_range("surface temperature", surface, landmask, 180.0, 330.0)
            assert_land_range(
                "soil temperature", soil_temperature, landmask, 180.0, 330.0
            )
            assert_land_range("soil VWC", soil_vwc, landmask, 0.0, 1.0)
            assert_land_range("SWE", swe, landmask, 0.0, 5000.0)
            assert_land_range("snow height", snow_height, landmask, 0.0, 20.0)

            static.variables["surface_temperature"][:] = surface
            static.variables["soil_temperature"][:] = soil_temperature
            static.variables["soil_vwc"][:] = soil_vwc
            static.variables["soil_deep_temperature"][:] = soil_temperature[-1]
            if "swe" not in static.variables:
                variable = static.createVariable(
                    "swe", "f4", static.variables["landmask"].dimensions, zlib=True
                )
                variable.units = "kg m-2"
                variable.long_name = "snow water equivalent"
            if "snow_height" not in static.variables:
                variable = static.createVariable(
                    "snow_height",
                    "f4",
                    static.variables["landmask"].dimensions,
                    zlib=True,
                )
                variable.units = "m"
                variable.long_name = "snow height"
            static.variables["swe"][:] = np.where(np.isfinite(swe), np.maximum(swe, 0), 0)
            static.variables["snow_height"][:] = np.where(
                np.isfinite(snow_height), np.maximum(snow_height, 0), 0
            )
            static.setncattr("land_state_source", str(args.land_state.resolve()))
            static.setncattr("land_state_valid_time", args.valid_time)
            static.setncattr(
                "land_state_transform",
                "REA-L T_SO point interpolation; W_SO overlap-conservative "
                "layer remap to NoahMP 0-0.1/0.1-0.3/0.3-0.7/0.7-1.5 m; "
                "snow height W_SNOW/RHO_SNOW",
            )

            target_column_mass = np.sum(target_water_mass, axis=0)
            stats = {
                "land_cells": int(np.count_nonzero(landmask)),
                "surface_temperature_k": [
                    float(np.min(surface[landmask])),
                    float(np.max(surface[landmask])),
                ],
                "soil_temperature_k": [
                    float(np.min(soil_temperature[..., landmask])),
                    float(np.max(soil_temperature[..., landmask])),
                ],
                "soil_vwc_m3_m3": [
                    float(np.min(soil_vwc[..., landmask])),
                    float(np.max(soil_vwc[..., landmask])),
                ],
                "target_column_water_kg_m2": [
                    float(np.min(target_column_mass[landmask])),
                    float(np.max(target_column_mass[landmask])),
                ],
                "swe_kg_m2": [
                    float(np.min(swe[landmask])),
                    float(np.max(swe[landmask])),
                ],
                "snow_height_m": [
                    float(np.min(snow_height[landmask])),
                    float(np.max(snow_height[landmask])),
                ],
            }
        os.replace(partial, args.output)
    except Exception:
        if partial.exists():
            partial.unlink()
        raise

    payload = {
        "schema_version": 1,
        "status": "PASS",
        "valid_time": args.valid_time,
        "static_template": str(args.static_template.resolve()),
        "static_template_sha256": sha256(args.static_template),
        "land_state": str(args.land_state.resolve()),
        "land_state_sha256": sha256(args.land_state),
        "output": str(args.output.resolve()),
        "output_sha256": sha256(args.output),
        "source_temperature_depths_m": REA_L_T_SO_DEPTHS_M.tolist(),
        "source_water_layer_bounds_m": REA_L_W_SO_BOUNDS_M.tolist(),
        "target_soil_layer_bounds_m": HICAR_SOIL_BOUNDS_M.tolist(),
        "spatial_regridding": (
            "none" if same_grid else "bilinear from rectilinear latitude/longitude"
        ),
        "snow_density_fallback_kg_m3": args.snow_density_fallback,
        "statistics": stats,
    }
    write_json_atomic(args.manifest, payload)
    Path(f"{args.manifest}.ready").touch()
    Path(f"{args.output}.ready").touch()
    print(f"PASS: published {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
