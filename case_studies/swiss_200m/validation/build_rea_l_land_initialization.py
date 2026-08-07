#!/usr/bin/env python3
"""Create a published HICAR static file with REA-L land and snow state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from tempfile import NamedTemporaryFile

import netCDF4
import numpy as np


REA_L_T_SO_DEPTHS_M = np.array((0.0, 0.005, 0.02, 0.06, 0.18, 0.54, 1.62, 4.86))
REA_L_W_SO_BOUNDS_M = np.array((0.0, 0.01, 0.03, 0.09, 0.27, 0.81, 2.43, 7.29, 21.87))
HICAR_SOIL_BOUNDS_M = np.array((0.0, 0.1, 0.3, 0.7, 1.5))
HICAR_SOIL_MIDPOINTS_M = 0.5 * (HICAR_SOIL_BOUNDS_M[:-1] + HICAR_SOIL_BOUNDS_M[1:])
WATER_DENSITY_KG_M3 = 1000.0

# TERRA soil hydraulic constants used by ICON. Indices are the native SOILTYP
# values 1..10 (ice, rock, sand, sandy loam, loam, clay loam, clay, peat,
# sea water, sea ice). Source: sfc_terra_data.f90 in ICON; the values below
# were verified against public ICON source commit
# 734248a8c5d0ef27a35949d546a8fb7394a19192.
ICON_TERRA_FIELD_CAPACITY = np.array(
    (1.0e-10, 1.0e-10, 0.196, 0.260, 0.340, 0.370, 0.463, 0.763, 1.0e-10, 1.0e-10)
)
ICON_TERRA_WILTING_POINT = np.array((0.0, 0.0, 0.042, 0.100, 0.110, 0.185, 0.257, 0.265, 0.0, 0.0))


def parse_noahmp_stas_hydraulics(path: Path) -> dict[str, np.ndarray]:
    """Read the exact STAS hydraulic table used by the HICAR executable."""
    text = path.read_text(encoding="utf-8")
    match = re.search(r"&noahmp_soil_stas_parameters\b(.*?)(?:\n\s*/)", text, re.DOTALL)
    if match is None:
        raise ValueError(f"missing noahmp_soil_stas_parameters in {path}")
    section = match.group(1)
    result: dict[str, np.ndarray] = {}
    for name in ("DRYSMC", "MAXSMC", "REFSMC", "WLTSMC"):
        value_match = re.search(rf"^\s*{name}\s*=\s*(.*)$", section, re.MULTILINE)
        if value_match is None:
            raise ValueError(f"missing {name} in Noah-MP STAS table {path}")
        values = np.array(
            [float(item.strip()) for item in value_match.group(1).split(",") if item.strip()],
            dtype=np.float64,
        )
        if values.size != 19:
            raise ValueError(f"Noah-MP STAS {name} has {values.size} values instead of 19")
        result[name] = values
    return result


def icon_soil_water_to_smi(
    mass_kg_m2: np.ndarray,
    soil_type: np.ndarray,
    source_bounds_m: np.ndarray = REA_L_W_SO_BOUNDS_M,
) -> np.ndarray:
    """Convert layer-integrated ICON W_SO to TERRA's dimensionless SMI."""
    mass_kg_m2 = np.asarray(mass_kg_m2, dtype=np.float64)
    soil_type = np.asarray(soil_type)
    source_bounds_m = np.asarray(source_bounds_m, dtype=np.float64)
    if mass_kg_m2.shape[0] + 1 != source_bounds_m.size:
        raise ValueError("W_SO layer count disagrees with source bounds")
    if soil_type.shape != mass_kg_m2.shape[1:]:
        raise ValueError("ICON SOILTYP shape disagrees with W_SO horizontal shape")
    if np.any(~np.isfinite(soil_type)):
        raise ValueError("ICON SOILTYP contains non-finite values")
    soil_type_int = soil_type.astype(np.int64)
    if not np.allclose(soil_type, soil_type_int):
        raise ValueError("ICON SOILTYP contains non-integer values")
    if np.min(soil_type_int) < 1 or np.max(soil_type_int) > 10:
        raise ValueError("ICON SOILTYP lies outside the documented 1..10 range")

    index = soil_type_int - 1
    field_capacity = ICON_TERRA_FIELD_CAPACITY[index]
    wilting_point = ICON_TERRA_WILTING_POINT[index]
    thickness = np.diff(source_bounds_m)
    layer_shape = (thickness.size,) + (1,) * (mass_kg_m2.ndim - 1)
    vwc = mass_kg_m2 / (WATER_DENSITY_KG_M3 * thickness.reshape(layer_shape))
    denominator = field_capacity - wilting_point
    active_soil = (soil_type_int >= 3) & (soil_type_int <= 8)
    smi = np.zeros_like(vwc)
    np.divide(
        vwc - wilting_point[np.newaxis, ...],
        denominator[np.newaxis, ...],
        out=smi,
        where=active_soil[np.newaxis, ...],
    )
    smi[:, soil_type_int >= 9] = np.nan
    return smi


def remap_layer_mean(
    values: np.ndarray,
    source_bounds_m: np.ndarray = REA_L_W_SO_BOUNDS_M,
    target_bounds_m: np.ndarray = HICAR_SOIL_BOUNDS_M,
) -> np.ndarray:
    """Overlap-average layer-mean values onto a new vertical discretization."""
    values = np.asarray(values, dtype=np.float64)
    source_bounds_m = np.asarray(source_bounds_m, dtype=np.float64)
    target_bounds_m = np.asarray(target_bounds_m, dtype=np.float64)
    if values.shape[0] + 1 != source_bounds_m.size:
        raise ValueError("layer count disagrees with source bounds")
    if np.any(np.diff(source_bounds_m) <= 0) or np.any(np.diff(target_bounds_m) <= 0):
        raise ValueError("soil-layer bounds must be strictly increasing")
    result = np.zeros((target_bounds_m.size - 1,) + values.shape[1:])
    result_weight = np.zeros(result.shape, dtype=np.float64)
    for target_index, (target_top, target_bottom) in enumerate(
        zip(target_bounds_m[:-1], target_bounds_m[1:])
    ):
        for source_index, (source_top, source_bottom) in enumerate(
            zip(source_bounds_m[:-1], source_bounds_m[1:])
        ):
            overlap = max(0.0, min(target_bottom, source_bottom) - max(target_top, source_top))
            if overlap:
                valid = np.isfinite(values[source_index])
                result[target_index] += np.where(valid, values[source_index] * overlap, 0.0)
                result_weight[target_index] += valid * overlap
    return np.divide(
        result,
        result_weight,
        out=np.full_like(result, np.nan),
        where=result_weight > 0,
    )


def noahmp_smi_to_vwc(
    smi: np.ndarray,
    soil_type: np.ndarray,
    hydraulics: dict[str, np.ndarray],
) -> np.ndarray:
    """Reconstruct Noah-MP VWC while retaining ICON's relative water state."""
    smi = np.asarray(smi, dtype=np.float64)
    soil_type = np.asarray(soil_type)
    if soil_type.ndim == 2:
        soil_type = np.broadcast_to(soil_type, smi.shape)
    if soil_type.shape != smi.shape:
        raise ValueError("target soil type must be 2-D or match the target SMI layers")
    if np.any(~np.isfinite(soil_type)):
        raise ValueError("target soil type contains non-finite values")
    soil_type_int = soil_type.astype(np.int64)
    if not np.allclose(soil_type, soil_type_int):
        raise ValueError("target soil type contains non-integer values")
    if np.min(soil_type_int) < 1 or np.max(soil_type_int) > 19:
        raise ValueError("target soil type lies outside Noah-MP STAS range 1..19")
    index = soil_type_int - 1
    wilting_point = hydraulics["WLTSMC"][index]
    field_capacity = hydraulics["REFSMC"][index]
    dry_limit = hydraulics["DRYSMC"][index]
    saturation = hydraulics["MAXSMC"][index]
    vwc = wilting_point + smi * (field_capacity - wilting_point)
    return np.clip(vwc, dry_limit, saturation)


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
            overlap = max(0.0, min(target_bottom, source_bottom) - max(target_top, source_top))
            if overlap:
                target_mass[target_index] += (
                    mass_kg_m2[source_index] * overlap / source_thickness[source_index]
                )
    target_vwc = target_mass / (WATER_DENSITY_KG_M3 * target_thickness[:, np.newaxis, np.newaxis])
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


def regular_grid_axes(latitude: np.ndarray, longitude: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
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


def coordinate_grid_shape(latitude: np.ndarray, longitude: np.ndarray) -> tuple[int, int]:
    """Return the field shape represented by matching grid coordinates."""
    latitude = np.asarray(latitude)
    longitude = np.asarray(longitude)
    if latitude.ndim == longitude.ndim == 1:
        return latitude.size, longitude.size
    if latitude.ndim == longitude.ndim == 2 and latitude.shape == longitude.shape:
        return latitude.shape
    raise ValueError("source latitude/longitude must be matching 1-D or 2-D arrays")


def same_coordinate_grid(
    source_latitude: np.ndarray,
    source_longitude: np.ndarray,
    target_latitude: np.ndarray,
    target_longitude: np.ndarray,
) -> bool:
    """Whether source fields are already sampled at the target grid points."""
    return (
        source_latitude.shape == target_latitude.shape
        and source_longitude.shape == target_longitude.shape
        and np.allclose(source_latitude, target_latitude, atol=1.0e-7)
        and np.allclose(source_longitude, target_longitude, atol=1.0e-7)
    )


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
    lat_weight = (target_latitude - lat_axis[lat_lo]) / (lat_axis[lat_hi] - lat_axis[lat_lo])
    lon_weight = (target_longitude - lon_axis[lon_lo]) / (lon_axis[lon_hi] - lon_axis[lon_lo])
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


def nearest_regrid(
    values: np.ndarray,
    source_latitude: np.ndarray,
    source_longitude: np.ndarray,
    target_latitude: np.ndarray,
    target_longitude: np.ndarray,
) -> np.ndarray:
    """Nearest-neighbour sample a categorical rectilinear lat/lon field."""
    values = np.asarray(values)
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
    lat_hi = np.clip(np.searchsorted(lat_axis, target_latitude), 1, lat_axis.size - 1)
    lon_hi = np.clip(np.searchsorted(lon_axis, target_longitude), 1, lon_axis.size - 1)
    lat_index = np.where(
        np.abs(target_latitude - lat_axis[lat_hi - 1])
        <= np.abs(target_latitude - lat_axis[lat_hi]),
        lat_hi - 1,
        lat_hi,
    )
    lon_index = np.where(
        np.abs(target_longitude - lon_axis[lon_hi - 1])
        <= np.abs(target_longitude - lon_axis[lon_hi]),
        lon_hi - 1,
        lon_hi,
    )
    return values[..., lat_index, lon_index]


def elevation_correct_temperature(
    temperature_k: np.ndarray,
    source_elevation_m: np.ndarray,
    target_elevation_m: np.ndarray,
    lapse_rate_k_m: float,
) -> np.ndarray:
    """Apply a stated linear lapse rate after horizontal interpolation."""
    temperature_k = np.asarray(temperature_k, dtype=np.float64)
    source_elevation_m = np.asarray(source_elevation_m, dtype=np.float64)
    target_elevation_m = np.asarray(target_elevation_m, dtype=np.float64)
    if source_elevation_m.shape != target_elevation_m.shape:
        raise ValueError("source and target elevation shapes differ")
    if temperature_k.shape[-2:] != target_elevation_m.shape:
        raise ValueError("temperature horizontal shape disagrees with elevation")
    return temperature_k + lapse_rate_k_m * (target_elevation_m - source_elevation_m)


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
    raise ValueError(f"{variable.name} shape {values.shape} does not contain target y/x shape")


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
    parser.add_argument("--source-smi-var")
    parser.add_argument("--source-soil-type-var", default="SOILTYP")
    parser.add_argument("--target-soil-type-var", default="soil_type")
    parser.add_argument("--soil-water-mapping", choices=("absolute", "smi"), default="smi")
    parser.add_argument("--noahmp-table", type=Path)
    parser.add_argument("--swe-var", default="W_SNOW")
    parser.add_argument("--snow-density-var", default="RHO_SNOW")
    parser.add_argument("--source-lat-var", default="lat_1")
    parser.add_argument("--source-lon-var", default="lon_1")
    parser.add_argument("--source-topography-var")
    parser.add_argument("--target-topography-var", default="topo")
    parser.add_argument("--surface-temperature-lapse-rate-k-m", type=float)
    parser.add_argument("--snow-density-fallback", type=float)
    args = parser.parse_args()

    if args.source_smi_var is not None and args.soil_water_mapping != "smi":
        raise SystemExit("--source-smi-var requires --soil-water-mapping=smi")

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
        with netCDF4.Dataset(partial, "r+") as static, netCDF4.Dataset(args.land_state) as source:
            landmask = np.asarray(static.variables["landmask"][:]) > 0
            ny, nx = landmask.shape
            target_latitude = np.asarray(static.variables["lat"][:])
            target_longitude = np.asarray(static.variables["lon"][:])
            source_latitude = np.asarray(source.variables[args.source_lat_var][:])
            source_longitude = np.asarray(source.variables[args.source_lon_var][:])
            source_ny, source_nx = coordinate_grid_shape(source_latitude, source_longitude)
            same_grid = (source_ny, source_nx) == (ny, nx) and same_coordinate_grid(
                source_latitude,
                source_longitude,
                target_latitude,
                target_longitude,
            )
            if not same_grid:
                # The legacy fieldextra intermediate is rectilinear. Direct
                # native ICON remapping is already on the curvilinear HICAR
                # points and deliberately bypasses this second interpolation.
                regular_grid_axes(source_latitude, source_longitude)
            surface = as_yx(source.variables[args.surface_temperature_var], source_ny, source_nx)
            source_temperature = as_layer_yx(
                source.variables[args.soil_temperature_var], source_ny, source_nx
            )
            source_water = None
            if args.source_smi_var is None:
                source_water = as_layer_yx(
                    source.variables[args.soil_water_var], source_ny, source_nx
                )
            source_smi = None
            hydraulics = None
            noahmp_table_sha256 = None
            if args.soil_water_mapping == "smi":
                if args.noahmp_table is None:
                    raise ValueError("--soil-water-mapping=smi requires --noahmp-table")
                if not args.noahmp_table.is_file():
                    raise ValueError(f"missing Noah-MP table: {args.noahmp_table}")
                if args.source_smi_var is not None:
                    source_smi = as_layer_yx(
                        source.variables[args.source_smi_var], source_ny, source_nx
                    )
                else:
                    source_soil_type = as_yx(
                        source.variables[args.source_soil_type_var],
                        source_ny,
                        source_nx,
                    )
                    assert source_water is not None
                    source_smi = icon_soil_water_to_smi(source_water, source_soil_type)
                hydraulics = parse_noahmp_stas_hydraulics(args.noahmp_table)
                noahmp_table_sha256 = sha256(args.noahmp_table)
            swe = as_yx(source.variables[args.swe_var], source_ny, source_nx)
            snow_density = as_yx(source.variables[args.snow_density_var], source_ny, source_nx)
            source_topography = None
            if args.surface_temperature_lapse_rate_k_m is not None:
                if args.source_topography_var is None:
                    raise ValueError(
                        "surface temperature lapse correction requires --source-topography-var"
                    )
                source_topography = as_yx(
                    source.variables[args.source_topography_var], source_ny, source_nx
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
                if source_smi is None:
                    assert source_water is not None
                    source_water = bilinear_regrid(
                        source_water,
                        source_latitude,
                        source_longitude,
                        target_latitude,
                        target_longitude,
                    )
                else:
                    source_smi = bilinear_regrid(
                        source_smi,
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
                if source_topography is not None:
                    source_topography = bilinear_regrid(
                        source_topography,
                        source_latitude,
                        source_longitude,
                        target_latitude,
                        target_longitude,
                    )

            if source_topography is not None:
                target_topography = as_yx(static.variables[args.target_topography_var], ny, nx)
                surface = elevation_correct_temperature(
                    surface,
                    source_topography,
                    target_topography,
                    args.surface_temperature_lapse_rate_k_m,
                )

            soil_temperature = remap_soil_temperature(source_temperature)
            target_smi = None
            if source_smi is None:
                assert source_water is not None
                soil_vwc, target_water_mass = remap_layer_integrated_soil_water(source_water)
            else:
                target_smi = remap_layer_mean(source_smi)
                target_soil_type = as_yx(static.variables[args.target_soil_type_var], ny, nx)
                assert hydraulics is not None
                soil_vwc = noahmp_smi_to_vwc(target_smi, target_soil_type, hydraulics)
                target_water_mass = soil_vwc * (
                    WATER_DENSITY_KG_M3 * np.diff(HICAR_SOIL_BOUNDS_M)[:, np.newaxis, np.newaxis]
                )
            snow_height = derive_snow_height(swe, snow_density, args.snow_density_fallback)

            assert_land_range("surface temperature", surface, landmask, 180.0, 330.0)
            assert_land_range("soil temperature", soil_temperature, landmask, 180.0, 330.0)
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
                "REA-L T_SO point interpolation; "
                + (
                    "W_SO overlap-conservative absolute-water layer remap"
                    if source_smi is None
                    else "W_SO -> ICON/TERRA SMI -> NoahMP STAS VWC layer remap"
                )
                + "; snow height W_SNOW/RHO_SNOW",
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
            if target_smi is not None:
                stats["target_smi"] = [
                    float(np.min(target_smi[..., landmask])),
                    float(np.max(target_smi[..., landmask])),
                ]
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
        "soil_water_mapping": args.soil_water_mapping,
        "source_smi_var": args.source_smi_var,
        "icon_terra_hydraulics_source_commit": (
            "734248a8c5d0ef27a35949d546a8fb7394a19192" if args.soil_water_mapping == "smi" else None
        ),
        "noahmp_table": (str(args.noahmp_table.resolve()) if args.noahmp_table else None),
        "noahmp_table_sha256": noahmp_table_sha256,
        "spatial_regridding": (
            "none" if same_grid else "bilinear from rectilinear latitude/longitude"
        ),
        "surface_temperature_lapse_rate_k_m": (args.surface_temperature_lapse_rate_k_m),
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
