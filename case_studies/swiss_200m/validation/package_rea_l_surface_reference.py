#!/usr/bin/env python3
"""Package one regridded REA-L surface-reference record for HICAR validation."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import netCDF4
import numpy as np


PRECIPITATION_ROUNDOFF_TOLERANCE_KG_M2 = 0.01


def _read(dataset: netCDF4.Dataset, name: str) -> np.ndarray:
    values = np.squeeze(np.ma.asarray(dataset.variables[name][:]))
    if np.ma.count_masked(values):
        values = values.filled(np.nan)
    return np.asarray(values, dtype=np.float64)


def _read_2d(dataset: netCDF4.Dataset, name: str) -> np.ndarray:
    values = _read(dataset, name)
    if values.ndim != 2:
        raise ValueError(f"{name} must reduce to 2-D, got {values.shape}")
    return values


def _coordinate(
    dataset: netCDF4.Dataset, names: tuple[str, ...], axis: int
) -> np.ndarray:
    for name in names:
        if name in dataset.variables:
            values = _read(dataset, name)
            if values.ndim == 1:
                return values
            if values.ndim == 2:
                coordinate = values[:, 0] if axis == 0 else values[0, :]
                reconstructed = (
                    coordinate[:, None] if axis == 0 else coordinate[None, :]
                )
                if np.allclose(values, reconstructed, rtol=0.0, atol=1.0e-8):
                    return coordinate
                raise ValueError(f"{name} is a non-rectilinear 2-D coordinate")
    raise ValueError(f"none of coordinate variables {names} is usable")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def _specific_humidity_from_dewpoint(
    dewpoint_k: np.ndarray, pressure_pa: np.ndarray
) -> np.ndarray:
    dewpoint_c = dewpoint_k - 273.15
    vapor_pressure = 611.2 * np.exp(
        17.67 * dewpoint_c / np.maximum(dewpoint_k - 29.65, 1.0)
    )
    return 0.622 * vapor_pressure / (pressure_pa - 0.378 * vapor_pressure)


def _require_range(
    name: str, values: np.ndarray, lower: float, upper: float
) -> list[float]:
    if not np.all(np.isfinite(values)):
        raise ValueError(f"{name} contains non-finite values")
    value_range = [float(np.min(values)), float(np.max(values))]
    if value_range[0] < lower or value_range[1] > upper:
        raise ValueError(
            f"{name} range {value_range[0]}..{value_range[1]} "
            f"is outside {lower}..{upper}"
        )
    return value_range


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--geometry", type=Path, required=True)
    parser.add_argument("--precip-start", type=Path)
    parser.add_argument("--precip-end", type=Path)
    parser.add_argument("--valid-time", required=True)
    parser.add_argument("--interval-start", required=True)
    parser.add_argument("--initial-record", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source-cycle", required=True)
    parser.add_argument("--source-step", required=True)
    args = parser.parse_args()

    valid_time = datetime.fromisoformat(args.valid_time)
    interval_start = datetime.fromisoformat(args.interval_start)
    if valid_time.tzinfo is None:
        valid_time = valid_time.replace(tzinfo=timezone.utc)
    if interval_start.tzinfo is None:
        interval_start = interval_start.replace(tzinfo=timezone.utc)
    if interval_start > valid_time:
        raise SystemExit("interval start is later than valid time")
    if args.initial_record:
        if args.precip_start or args.precip_end:
            raise SystemExit("initial record must not provide precipitation endpoints")
    elif args.precip_start is None or args.precip_end is None:
        raise SystemExit("non-initial record requires precipitation endpoints")

    with netCDF4.Dataset(args.current) as current:
        latitude = _coordinate(current, ("lat_1", "lat", "latitude"), axis=0)
        longitude = _coordinate(current, ("lon_1", "lon", "longitude"), axis=1)
        pressure = _read_2d(current, "PS")
        temperature = _read_2d(current, "T_2M")
        dewpoint = _read_2d(current, "TD_2M")
        wind_u = _read_2d(current, "U_10M")
        wind_v = _read_2d(current, "V_10M")
        snow_height = _read_2d(current, "H_SNOW")
        snow_water_equivalent = _read_2d(current, "W_SNOW")
    with netCDF4.Dataset(args.geometry) as geometry:
        terrain = _read_2d(geometry, "HSURF")

    shape = (len(latitude), len(longitude))
    for name, values in (
        ("PS", pressure),
        ("T_2M", temperature),
        ("TD_2M", dewpoint),
        ("U_10M", wind_u),
        ("V_10M", wind_v),
        ("H_SNOW", snow_height),
        ("W_SNOW", snow_water_equivalent),
        ("HSURF", terrain),
    ):
        if values.shape != shape:
            raise SystemExit(f"{name} has shape {values.shape}, expected {shape}")

    if args.initial_record:
        precipitation = np.zeros(shape, dtype=np.float64)
        precipitation_roundoff = {
            "minimum_raw_interval_kg_m2": 0.0,
            "clipped_negative_cells": 0,
            "tolerance_kg_m2": PRECIPITATION_ROUNDOFF_TOLERANCE_KG_M2,
        }
    else:
        assert args.precip_start is not None and args.precip_end is not None
        with netCDF4.Dataset(args.precip_start) as start:
            precipitation_start = _read_2d(start, "TOT_PREC")
        with netCDF4.Dataset(args.precip_end) as end:
            precipitation_end = _read_2d(end, "TOT_PREC")
        precipitation = precipitation_end - precipitation_start
        minimum_precipitation = float(np.min(precipitation))
        if minimum_precipitation < -PRECIPITATION_ROUNDOFF_TOLERANCE_KG_M2:
            raise SystemExit(
                "REA-L interval precipitation decreases by more than tolerance: "
                f"{minimum_precipitation}"
            )
        precipitation_roundoff = {
            "minimum_raw_interval_kg_m2": minimum_precipitation,
            "clipped_negative_cells": int(np.count_nonzero(precipitation < 0.0)),
            "tolerance_kg_m2": PRECIPITATION_ROUNDOFF_TOLERANCE_KG_M2,
        }
        precipitation = np.maximum(precipitation, 0.0)

    humidity = _specific_humidity_from_dewpoint(dewpoint, pressure)
    ranges = {
        "surface_pressure_pa": _require_range("PS", pressure, 20_000.0, 120_000.0),
        "temperature_2m_k": _require_range("T_2M", temperature, 180.0, 340.0),
        "dewpoint_2m_k": _require_range("TD_2M", dewpoint, 170.0, 330.0),
        "specific_humidity_2m": _require_range(
            "derived_hus2m", humidity, 0.0, 0.1
        ),
        "wind_u_10m_m_s": _require_range("U_10M", wind_u, -150.0, 150.0),
        "wind_v_10m_m_s": _require_range("V_10M", wind_v, -150.0, 150.0),
        "precipitation_interval_kg_m2": _require_range(
            "TOT_PREC interval", precipitation, 0.0, 1000.0
        ),
        "snow_height_m": _require_range("H_SNOW", snow_height, 0.0, 20.0),
        "snow_water_equivalent_kg_m2": _require_range(
            "W_SNOW", snow_water_equivalent, 0.0, 10_000.0
        ),
        "source_terrain_m": _require_range("HSURF", terrain, -1000.0, 6000.0),
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    partial = args.output.with_suffix(args.output.suffix + f".partial.{os.getpid()}")
    with netCDF4.Dataset(partial, "w", format="NETCDF4") as target:
        target.createDimension("time", 1)
        target.createDimension("bounds", 2)
        target.createDimension("latitude", len(latitude))
        target.createDimension("longitude", len(longitude))
        time = target.createVariable("time", "f8", ("time",))
        time.units = "seconds since 1970-01-01 00:00:00 UTC"
        time.calendar = "standard"
        time.bounds = "time_bounds"
        time[:] = netCDF4.date2num([valid_time], time.units, time.calendar)
        bounds = target.createVariable("time_bounds", "f8", ("time", "bounds"))
        bounds[:] = np.asarray(
            [
                netCDF4.date2num(
                    [interval_start, valid_time], time.units, time.calendar
                )
            ],
            dtype=np.float64,
        )
        lat = target.createVariable("latitude", "f8", ("latitude",))
        lat.standard_name = "latitude"
        lat.units = "degrees_north"
        lat[:] = latitude
        lon = target.createVariable("longitude", "f8", ("longitude",))
        lon.standard_name = "longitude"
        lon.units = "degrees_east"
        lon[:] = longitude

        def write(
            name: str,
            values: np.ndarray,
            standard_name: str | None,
            long_name: str,
            units: str,
            cell_methods: str | None = None,
        ) -> None:
            variable = target.createVariable(
                name,
                "f4",
                ("time", "latitude", "longitude"),
                zlib=True,
                complevel=1,
                shuffle=True,
            )
            if standard_name:
                variable.standard_name = standard_name
            variable.long_name = long_name
            variable.units = units
            variable.coordinates = "latitude longitude"
            if cell_methods:
                variable.cell_methods = cell_methods
            variable[0, :, :] = values.astype(np.float32)

        write(
            "psfc_ref",
            pressure,
            "surface_air_pressure",
            "REA-L surface pressure",
            "Pa",
        )
        write(
            "ta2m_ref",
            temperature,
            "air_temperature",
            "REA-L 2 m air temperature",
            "K",
        )
        write(
            "td2m_ref",
            dewpoint,
            "dew_point_temperature",
            "REA-L 2 m dew-point temperature",
            "K",
        )
        write(
            "hus2m_ref",
            humidity,
            "specific_humidity",
            "REA-L 2 m specific humidity derived from dew point and pressure",
            "1",
        )
        write(
            "u10m_ref",
            wind_u,
            "eastward_wind",
            "REA-L instantaneous 10 m eastward wind",
            "m s-1",
        )
        write(
            "v10m_ref",
            wind_v,
            "northward_wind",
            "REA-L instantaneous 10 m northward wind",
            "m s-1",
        )
        write(
            "precipitation_interval_ref",
            precipitation,
            "precipitation_amount",
            "REA-L precipitation accumulated over time_bounds",
            "kg m-2",
            "time: sum",
        )
        write(
            "snow_height_ref",
            snow_height,
            "surface_snow_thickness",
            "REA-L snow height",
            "m",
        )
        write(
            "swe_ref",
            snow_water_equivalent,
            "lwe_thickness_of_surface_snow_amount",
            "REA-L snow water equivalent",
            "kg m-2",
        )
        write(
            "source_terrain",
            terrain,
            "surface_altitude",
            "REA-L source-grid terrain elevation",
            "m",
        )
        target.title = "REA-L surface reference for HICAR scientific validation"
        target.source = "ICON REA-L-CH1 FDB"
        target.source_cycle = args.source_cycle
        target.source_step = args.source_step
        target.history = "Regridded with operational fieldextra and packaged by icon_hicar"

    os.replace(partial, args.output)
    payload = {
        "schema_version": 1,
        "status": "PASS",
        "source": "ICON REA-L-CH1 FDB",
        "source_cycle": args.source_cycle,
        "source_step": args.source_step,
        "valid_time": valid_time.isoformat(),
        "interval_start": interval_start.isoformat(),
        "initial_record": args.initial_record,
        "current_regridded": str(args.current.resolve()),
        "geometry_regridded": str(args.geometry.resolve()),
        "precip_start_regridded": (
            str(args.precip_start.resolve()) if args.precip_start else None
        ),
        "precip_end_regridded": (
            str(args.precip_end.resolve()) if args.precip_end else None
        ),
        "output": str(args.output.resolve()),
        "output_size_bytes": args.output.stat().st_size,
        "output_sha256": _sha256(args.output),
        "ranges": ranges,
        "precipitation_roundoff": precipitation_roundoff,
        "transformations": {
            "specific_humidity": (
                "Bolton saturation vapor pressure at TD_2M; "
                "q=0.622*e/(PS-0.378*e)"
            ),
            "precipitation": (
                "difference of cycle-cumulative TOT_PREC endpoints; "
                "negative roundoff clipped only within 0.01 kg m-2"
            ),
        },
    }
    _write_json_atomic(args.manifest, payload)
    Path(f"{args.output}.ready").touch()
    Path(f"{args.manifest}.ready").touch()
    print(f"PASS: published {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
