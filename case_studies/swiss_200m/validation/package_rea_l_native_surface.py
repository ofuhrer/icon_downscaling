#!/usr/bin/env python3
"""Package REA-L native GRIB plus matching EXTPAR as canonical hicarprep surface input."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re

import netCDF4
import numpy as np

from remap_rea_l_native_land_state import (
    metadata,
    normalized_uuid,
    read_grib_fields,
    sha256,
    soil_stack,
)


REA_L_T_SO_DEPTHS_M = np.array((0.0, 0.005, 0.02, 0.06, 0.18, 0.54, 1.62, 4.86))
REA_L_W_SO_BOUNDS_M = np.array((0.0, 0.01, 0.03, 0.09, 0.27, 0.81, 2.43, 7.29, 21.87))


def _cell_variable(dataset: netCDF4.Dataset, name: str) -> np.ndarray:
    values = np.asarray(np.ma.asarray(dataset[name][:]).filled(np.nan))
    values = np.squeeze(values)
    if values.ndim != 1:
        raise ValueError(f"EXTPAR {name} is not a one-dimensional cell field after squeeze")
    return values


def validate_extpar_inventory(
    clat: np.ndarray,
    clon: np.ndarray,
    soil_type: np.ndarray,
    land_fraction: np.ndarray,
    source_topography: np.ndarray,
) -> int:
    """Validate the tuned EXTPAR fields used to define native-grid support."""
    arrays = {
        "clat": np.asarray(clat),
        "clon": np.asarray(clon),
        "SOILTYP": np.asarray(soil_type),
        "FR_LAND": np.asarray(land_fraction),
        "topography_c": np.asarray(source_topography),
    }
    sizes = {name: values.size for name, values in arrays.items()}
    if len(set(sizes.values())) != 1:
        raise ValueError(f"EXTPAR cell fields have inconsistent sizes: {sizes}")
    if not all(np.isfinite(values).all() for values in arrays.values()):
        raise ValueError("EXTPAR support fields contain non-finite values")
    if np.any((arrays["clat"] < -np.pi / 2.0) | (arrays["clat"] > np.pi / 2.0)):
        raise ValueError("EXTPAR clat is outside the valid radian latitude range")
    if np.any((arrays["clon"] < -2.0 * np.pi) | (arrays["clon"] > 2.0 * np.pi)):
        raise ValueError("EXTPAR clon is outside the supported radian longitude range")
    rounded_soil = np.rint(arrays["SOILTYP"])
    if not np.allclose(arrays["SOILTYP"], rounded_soil, atol=1.0e-6):
        raise ValueError("EXTPAR SOILTYP is not integer-valued")
    if np.any((rounded_soil < 1) | (rounded_soil > 9)):
        raise ValueError("EXTPAR SOILTYP contains values outside ICON TERRA classes 1..9")
    if np.any((arrays["FR_LAND"] < 0.0) | (arrays["FR_LAND"] > 1.0)):
        raise ValueError("EXTPAR FR_LAND contains values outside 0..1")
    return next(iter(sizes.values()))


def validate_native_surface_values(
    t_so: np.ndarray,
    w_so: np.ndarray,
    surface_values: dict[str, np.ndarray],
    source_topography: np.ndarray,
) -> None:
    """Reject non-finite or physically impossible decoded native states."""
    arrays = {
        "T_SO": np.asarray(t_so, dtype=np.float64),
        "W_SO": np.asarray(w_so, dtype=np.float64),
        "SKT": np.asarray(surface_values["SKT"], dtype=np.float64),
        "W_SNOW": np.asarray(surface_values["W_SNOW"], dtype=np.float64),
        "RHO_SNOW": np.asarray(surface_values["RHO_SNOW"], dtype=np.float64),
        "HSURF": np.asarray(source_topography, dtype=np.float64),
    }
    if not all(np.isfinite(values).all() for values in arrays.values()):
        raise ValueError("decoded native surface state contains non-finite values")
    for name in ("T_SO", "SKT"):
        if np.any((arrays[name] < 180.0) | (arrays[name] > 350.0)):
            raise ValueError(f"{name} lies outside the conservative 180..350 K range")
    if np.any(arrays["W_SO"] < 0.0) or np.any(arrays["W_SO"] > 10_000.0):
        raise ValueError("W_SO is negative or implausibly large")
    if np.any(arrays["W_SNOW"] < 0.0) or np.any(arrays["W_SNOW"] > 10_000.0):
        raise ValueError("W_SNOW is negative or implausibly large")
    snow = arrays["W_SNOW"] > 1.0e-9
    if np.any(snow & ((arrays["RHO_SNOW"] <= 0.0) | (arrays["RHO_SNOW"] > 917.0))):
        raise ValueError("positive W_SNOW has invalid RHO_SNOW")
    if np.any((arrays["HSURF"] < -500.0) | (arrays["HSURF"] > 9_000.0)):
        raise ValueError("EXTPAR topography is outside -500..9000 m")


EXPECTED_UNITS = {
    "SKT": "k",
    "T_SO": "k",
    "W_SNOW": "kgm-2",
    "W_SO": "kgm-2",
    "RHO_SNOW": "kgm-3",
}


def _normalized_units(value: object) -> str:
    text = str(value).strip().lower().replace("**", "").replace("^", "")
    return re.sub(r"[\s*/]+", "", text)


def _iso_utc(value: str) -> str:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _grib_valid_time(field) -> str:
    date = metadata(field, "validityDate")
    time = metadata(field, "validityTime")
    if date is None or time is None:
        raise ValueError("GRIB message lacks validityDate/validityTime")
    parsed = dt.datetime.strptime(f"{int(date):08d}{int(time):04d}", "%Y%m%d%H%M")
    return parsed.replace(tzinfo=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_grib_field(field, name: str, valid_time: str, *, soil: bool) -> dict[str, object]:
    short_name = str(metadata(field, "shortName") or "").upper()
    if short_name != name:
        raise ValueError(f"expected {name} GRIB message, found shortName={short_name!r}")
    units = metadata(field, "units")
    normalized_units = _normalized_units(units)
    if normalized_units != EXPECTED_UNITS[name]:
        raise ValueError(
            f"{name} has units {units!r}, expected {EXPECTED_UNITS[name]!r} after normalization"
        )
    actual_time = _grib_valid_time(field)
    if actual_time != valid_time:
        raise ValueError(f"{name} valid time {actual_time} differs from requested {valid_time}")
    step_type = str(metadata(field, "stepType") or "").lower()
    if step_type != "instant":
        raise ValueError(f"{name} is stepType={step_type!r}, expected instantaneous state")
    level_type = str(metadata(field, "typeOfLevel") or "")
    if soil:
        if not level_type.lower().startswith("depthbelowland"):
            raise ValueError(f"{name} has incompatible typeOfLevel={level_type!r}")
    elif level_type.lower() != "surface":
        raise ValueError(f"{name} has incompatible typeOfLevel={level_type!r}")
    return {
        "valid_time": actual_time,
        "step": metadata(field, "step"),
        "step_range": str(metadata(field, "stepRange") or ""),
        "step_type": step_type,
        "type_of_level": level_type,
        "units": str(units),
    }


def validate_grib_inventory(
    surface_fields: list,
    soil_temperature_fields: list,
    soil_water_fields: list,
    requested_valid_time: str,
) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    """Reject duplicate, stale, accumulated, mis-levelled, or mis-unit land-state messages."""
    valid_time = _iso_utc(requested_valid_time)
    grouped: dict[str, list] = {}
    for field in surface_fields:
        grouped.setdefault(str(metadata(field, "shortName") or "").upper(), []).append(field)
    surface_by_name: dict[str, object] = {}
    contract: dict[str, dict[str, object]] = {}
    for name in ("SKT", "W_SNOW", "RHO_SNOW"):
        matches = grouped.get(name, [])
        if len(matches) != 1:
            raise ValueError(f"surface GRIB requires exactly one {name} message, found {len(matches)}")
        surface_by_name[name] = matches[0]
        contract[name] = _validate_grib_field(matches[0], name, valid_time, soil=False)
    for name, fields in (("T_SO", soil_temperature_fields), ("W_SO", soil_water_fields)):
        if not fields:
            raise ValueError(f"{name} GRIB contains no messages")
        for field in fields:
            _validate_grib_field(field, name, valid_time, soil=True)
        contract[name] = {
            "message_count": len(fields),
            "valid_time": valid_time,
            "units": str(metadata(fields[0], "units")),
            "step_type": str(metadata(fields[0], "stepType")),
            "type_of_level": str(metadata(fields[0], "typeOfLevel")),
        }
    return surface_by_name, contract


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--icon-extpar", type=Path, required=True)
    parser.add_argument("--surface-grib", type=Path, required=True)
    parser.add_argument("--soil-temperature-grib", type=Path, required=True)
    parser.add_argument("--soil-water-grib", type=Path, required=True)
    parser.add_argument("--valid-time", required=True, help="ISO-8601 valid time")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    for path in (
        args.icon_extpar,
        args.surface_grib,
        args.soil_temperature_grib,
        args.soil_water_grib,
    ):
        if not path.is_file() or path.stat().st_size == 0:
            raise SystemExit(f"missing or empty input: {path}")
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite {args.output}")

    surface_fields = read_grib_fields(args.surface_grib)
    t_so_fields = read_grib_fields(args.soil_temperature_grib)
    w_so_fields = read_grib_fields(args.soil_water_grib)
    normalized_valid_time = _iso_utc(args.valid_time)
    surface_by_name, grib_contract = validate_grib_inventory(
        surface_fields, t_so_fields, w_so_fields, normalized_valid_time
    )
    t_so, t_grid = soil_stack(
        t_so_fields, REA_L_T_SO_DEPTHS_M, "T_SO"
    )
    w_so, w_grid = soil_stack(
        w_so_fields, REA_L_W_SO_BOUNDS_M[1:], "W_SO"
    )
    surface_grid = dict(surface_by_name["SKT"].geography.grid_spec())
    if surface_grid != t_grid or surface_grid != w_grid:
        raise ValueError("surface, T_SO and W_SO do not share one ICON native grid")
    grid_uid = normalized_uuid(str(surface_grid.get("uid", surface_grid.get("uuid", ""))))

    with netCDF4.Dataset(args.icon_extpar) as extpar:
        extpar_uid = normalized_uuid(str(extpar.getncattr("uuidOfHGrid")))
        if grid_uid and grid_uid != extpar_uid:
            raise ValueError(f"EXTPAR grid UUID {extpar_uid} differs from GRIB {grid_uid}")
        clat = _cell_variable(extpar, "clat")
        clon = _cell_variable(extpar, "clon")
        soil_type = _cell_variable(extpar, "SOILTYP")
        land_fraction = _cell_variable(extpar, "FR_LAND")
        source_topography = _cell_variable(extpar, "topography_c")
    cells = validate_extpar_inventory(
        clat, clon, soil_type, land_fraction, source_topography
    )
    if any(values.shape[-1] != cells for values in (t_so, w_so)):
        raise ValueError("EXTPAR cell count differs from REA-L GRIB")

    surface_values = {
        name: np.asarray(surface_by_name[name].to_numpy(flatten=True), dtype=np.float64)
        for name in ("SKT", "W_SNOW", "RHO_SNOW")
    }
    if any(values.size != cells for values in surface_values.values()):
        raise ValueError("surface GRIB cell count differs from EXTPAR")
    validate_native_surface_values(t_so, w_so, surface_values, source_topography)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    partial = args.output.with_name(f".{args.output.name}.partial.{os.getpid()}")
    try:
        with netCDF4.Dataset(partial, "w") as output:
            output.createDimension("cell", cells)
            output.createDimension("t_so_level", REA_L_T_SO_DEPTHS_M.size)
            output.createDimension("w_so_layer", REA_L_W_SO_BOUNDS_M.size - 1)
            output.createDimension("w_so_interface", REA_L_W_SO_BOUNDS_M.size)
            for name, values, units in (
                ("clat", clat, "radian"),
                ("clon", clon, "radian"),
                ("SOILTYP", soil_type, "1"),
                ("FR_LAND", land_fraction, "1"),
                ("HSURF", source_topography, "m"),
                ("SKT", surface_values["SKT"], "K"),
                ("W_SNOW", surface_values["W_SNOW"], "kg m-2"),
                ("RHO_SNOW", surface_values["RHO_SNOW"], "kg m-3"),
            ):
                variable = output.createVariable(name, "f8", ("cell",), zlib=True)
                variable[:] = values
                variable.units = units
            variable = output.createVariable("T_SO", "f8", ("t_so_level", "cell"), zlib=True)
            variable[:] = t_so
            variable.units = "K"
            variable = output.createVariable("W_SO", "f8", ("w_so_layer", "cell"), zlib=True)
            variable[:] = w_so
            variable.units = "kg m-2"
            output.createVariable("t_so_depth", "f8", ("t_so_level",))[:] = REA_L_T_SO_DEPTHS_M
            output.createVariable("w_so_bounds", "f8", ("w_so_interface",))[:] = REA_L_W_SO_BOUNDS_M
            output.valid_time = normalized_valid_time
            output.horizontal_grid_uuid = extpar_uid
            output.source_model = "ICON REA-L-CH1"
            output.surface_source = "native GRIB plus matching tuned EXTPAR"
            output.grib_contract = json.dumps(grib_contract, sort_keys=True)
            output.icon_extpar_sha256 = sha256(args.icon_extpar)
            output.surface_grib_sha256 = sha256(args.surface_grib)
            output.soil_temperature_grib_sha256 = sha256(args.soil_temperature_grib)
            output.soil_water_grib_sha256 = sha256(args.soil_water_grib)
        os.replace(partial, args.output)
    finally:
        partial.unlink(missing_ok=True)
    print(f"PASS: wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
