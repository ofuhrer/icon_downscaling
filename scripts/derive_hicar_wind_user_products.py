#!/usr/bin/env python3
"""Derive user-facing direction, variability, shear, and veer diagnostics."""

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

from reduce_hicar_wind_climatology import (
    EXPECTED_HEIGHTS_M,
    _set_static_domain_identity,
    _static_domain_identity,
)


SHEAR_PAIRS = (
    (50.0, 100.0),
    (100.0, 150.0),
    (100.0, 200.0),
)
FILL_VALUE = np.float32(9.96921e36)
MIN_DIRECTION_SPEED_M_S = 0.1
MIN_SHEAR_SPEED_M_S = 1.0


def _as_float64(values: object) -> np.ndarray:
    return np.asarray(np.ma.filled(np.ma.asarray(values), np.nan), dtype=np.float64)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
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


def _copy_attributes(
    source: netCDF4.Variable,
    target: netCDF4.Variable,
) -> None:
    for attribute in source.ncattrs():
        if attribute != "_FillValue":
            target.setncattr(attribute, source.getncattr(attribute))


def _wind_from_direction(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    return np.mod(np.degrees(np.arctan2(-u, -v)), 360.0)


def _wrapped_difference_degrees(
    upper: np.ndarray,
    lower: np.ndarray,
) -> np.ndarray:
    return np.mod(upper - lower + 180.0, 360.0) - 180.0


def create_product(
    input_path: Path,
    output_path: Path,
    *,
    y_block_size: int = 128,
    report_path: Path | None = None,
    publish_ready: bool = True,
) -> dict[str, object]:
    """Create a derived supplement from a reduced or merged compact product."""
    input_path = input_path.resolve()
    output_path = output_path.resolve()
    report_path = (
        report_path.resolve()
        if report_path is not None
        else output_path.with_suffix(output_path.suffix + ".json")
    )
    if not input_path.is_file() or not Path(f"{input_path}.ready").is_file():
        raise ValueError(f"input publication is incomplete: {input_path}")
    if input_path == output_path:
        raise ValueError("output must differ from input")
    if y_block_size <= 0:
        raise ValueError("y_block_size must be positive")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    ready_path = Path(f"{output_path}.ready")
    if ready_path.exists():
        ready_path.unlink()
    temporary_output = output_path.with_name(f".{output_path.name}.tmp-{os.getpid()}")

    try:
        with netCDF4.Dataset(input_path) as source:
            for dimension in ("time", "bounds", "height_agl", "lat_y", "lon_x"):
                if dimension not in source.dimensions:
                    raise ValueError(f"input is missing dimension {dimension}")
            for name in (
                "time",
                "time_bounds",
                "height_agl",
                "height_10m",
                "lat",
                "lon",
                "eastward_wind_mean",
                "northward_wind_mean",
                "wind_speed_mean",
                "wind_speed_standard_deviation",
                "eastward_wind_10m_mean",
                "northward_wind_10m_mean",
            ):
                if name not in source.variables:
                    raise ValueError(f"input is missing variable {name}")
            heights = _as_float64(source["height_agl"][:])
            if not np.allclose(
                heights,
                EXPECTED_HEIGHTS_M,
                rtol=0.0,
                atol=1.0e-6,
            ):
                raise ValueError("input has an unexpected height_agl coordinate")
            nt = len(source.dimensions["time"])
            nh = len(source.dimensions["height_agl"])
            ny = len(source.dimensions["lat_y"])
            nx = len(source.dimensions["lon_x"])
            pair_indices = [
                (
                    int(np.flatnonzero(np.isclose(heights, lower))[0]),
                    int(np.flatnonzero(np.isclose(heights, upper))[0]),
                )
                for lower, upper in SHEAR_PAIRS
            ]
            static_identity = _static_domain_identity(source)

            with netCDF4.Dataset(temporary_output, "w", format="NETCDF4") as target:
                target.createDimension("time", nt)
                target.createDimension("bounds", 2)
                target.createDimension("height_agl", nh)
                target.createDimension("height_pair", len(SHEAR_PAIRS))
                target.createDimension("lat_y", ny)
                target.createDimension("lon_x", nx)

                for name, dimensions in (
                    ("time", ("time",)),
                    ("time_bounds", ("time", "bounds")),
                    ("height_agl", ("height_agl",)),
                    ("height_10m", ()),
                    ("lat", ("lat_y", "lon_x")),
                    ("lon", ("lat_y", "lon_x")),
                ):
                    source_variable = source[name]
                    variable = target.createVariable(
                        name,
                        source_variable.dtype,
                        dimensions,
                        zlib=bool(dimensions),
                        complevel=1,
                        shuffle=bool(dimensions),
                    )
                    _copy_attributes(source_variable, variable)
                    variable[...] = source_variable[...]

                lower_height = target.createVariable(
                    "lower_height_agl",
                    "f4",
                    ("height_pair",),
                )
                upper_height = target.createVariable(
                    "upper_height_agl",
                    "f4",
                    ("height_pair",),
                )
                for variable, long_name, values in (
                    (
                        lower_height,
                        "lower height above ground for the vertical pair",
                        [pair[0] for pair in SHEAR_PAIRS],
                    ),
                    (
                        upper_height,
                        "upper height above ground for the vertical pair",
                        [pair[1] for pair in SHEAR_PAIRS],
                    ),
                ):
                    variable.standard_name = "height"
                    variable.long_name = long_name
                    variable.units = "m"
                    variable.positive = "up"
                    variable[:] = np.asarray(values, dtype=np.float32)

                fixed_direction = target.createVariable(
                    "wind_from_direction_of_vector_mean",
                    "f4",
                    ("time", "height_agl", "lat_y", "lon_x"),
                    zlib=True,
                    complevel=1,
                    shuffle=True,
                    fill_value=FILL_VALUE,
                )
                fixed_direction.standard_name = "wind_from_direction"
                fixed_direction.long_name = "Wind-from direction of the vector-mean wind"
                fixed_direction.units = "degree"
                fixed_direction.coordinates = "height_agl lat lon"
                fixed_direction.comment = (
                    "Direction is derived from interval-mean eastward and "
                    "northward components; it is not a scalar mean direction."
                )
                fixed_direction.minimum_vector_mean_speed = (
                    MIN_DIRECTION_SPEED_M_S
                )
                variability = target.createVariable(
                    "wind_speed_coefficient_of_variation",
                    "f4",
                    ("time", "height_agl", "lat_y", "lon_x"),
                    zlib=True,
                    complevel=1,
                    shuffle=True,
                    fill_value=FILL_VALUE,
                )
                variability.long_name = (
                    "Coefficient of variation of sampled wind speed"
                )
                variability.units = "1"
                variability.coordinates = "height_agl lat lon"
                variability.minimum_mean_speed = MIN_DIRECTION_SPEED_M_S
                surface_direction = target.createVariable(
                    "wind_from_direction_10m_of_vector_mean",
                    "f4",
                    ("time", "lat_y", "lon_x"),
                    zlib=True,
                    complevel=1,
                    shuffle=True,
                    fill_value=FILL_VALUE,
                )
                surface_direction.standard_name = "wind_from_direction"
                surface_direction.long_name = (
                    "10 m wind-from direction of the vector-mean wind"
                )
                surface_direction.units = "degree"
                surface_direction.coordinates = "height_10m lat lon"
                surface_direction.comment = fixed_direction.comment
                surface_direction.minimum_vector_mean_speed = (
                    MIN_DIRECTION_SPEED_M_S
                )
                shear = target.createVariable(
                    "wind_shear_exponent_of_mean_speed",
                    "f4",
                    ("time", "height_pair", "lat_y", "lon_x"),
                    zlib=True,
                    complevel=1,
                    shuffle=True,
                    fill_value=FILL_VALUE,
                )
                shear.long_name = "Power-law shear exponent of scalar mean wind speed"
                shear.units = "1"
                shear.coordinates = "lower_height_agl upper_height_agl lat lon"
                shear.formula = "log(U_upper/U_lower) / log(z_upper/z_lower)"
                shear.minimum_mean_speed_at_each_height = MIN_SHEAR_SPEED_M_S
                veer = target.createVariable(
                    "wind_directional_veer_of_vector_mean",
                    "f4",
                    ("time", "height_pair", "lat_y", "lon_x"),
                    zlib=True,
                    complevel=1,
                    shuffle=True,
                    fill_value=FILL_VALUE,
                )
                veer.long_name = (
                    "Clockwise wind-from directional veer of vector-mean wind"
                )
                veer.units = "degree"
                veer.coordinates = "lower_height_agl upper_height_agl lat lon"
                veer.valid_min = np.float32(-180.0)
                veer.valid_max = np.float32(180.0)
                veer.minimum_vector_mean_speed_at_each_height = (
                    MIN_SHEAR_SPEED_M_S
                )

                for time_index in range(nt):
                    for y_start in range(0, ny, y_block_size):
                        y_stop = min(y_start + y_block_size, ny)
                        y_slice = slice(y_start, y_stop)
                        u = _as_float64(
                            source["eastward_wind_mean"][
                                time_index, :, y_slice, :
                            ]
                        )
                        v = _as_float64(
                            source["northward_wind_mean"][
                                time_index, :, y_slice, :
                            ]
                        )
                        speed = _as_float64(
                            source["wind_speed_mean"][
                                time_index, :, y_slice, :
                            ]
                        )
                        std = _as_float64(
                            source["wind_speed_standard_deviation"][
                                time_index, :, y_slice, :
                            ]
                        )
                        direction = _wind_from_direction(u, v)
                        vector_magnitude = np.hypot(u, v)
                        direction[
                            vector_magnitude < MIN_DIRECTION_SPEED_M_S
                        ] = np.nan
                        cv = np.divide(
                            std,
                            speed,
                            out=np.full_like(std, np.nan),
                            where=speed >= MIN_DIRECTION_SPEED_M_S,
                        )
                        fixed_direction[time_index, :, y_slice, :] = np.ma.masked_invalid(
                            direction.astype(np.float32)
                        )
                        variability[time_index, :, y_slice, :] = np.ma.masked_invalid(
                            cv.astype(np.float32)
                        )

                        u10 = _as_float64(
                            source["eastward_wind_10m_mean"][
                                time_index, y_slice, :
                            ]
                        )
                        v10 = _as_float64(
                            source["northward_wind_10m_mean"][
                                time_index, y_slice, :
                            ]
                        )
                        direction10 = _wind_from_direction(u10, v10)
                        direction10[
                            np.hypot(u10, v10) < MIN_DIRECTION_SPEED_M_S
                        ] = np.nan
                        surface_direction[time_index, y_slice, :] = (
                            np.ma.masked_invalid(direction10.astype(np.float32))
                        )

                        shear_values = np.full(
                            (len(SHEAR_PAIRS), y_stop - y_start, nx),
                            np.nan,
                            dtype=np.float64,
                        )
                        veer_values = np.full_like(shear_values, np.nan)
                        for pair_index, (lower_index, upper_index) in enumerate(
                            pair_indices
                        ):
                            valid_speed = (
                                (speed[lower_index] >= MIN_SHEAR_SPEED_M_S)
                                & (speed[upper_index] >= MIN_SHEAR_SPEED_M_S)
                            )
                            shear_values[pair_index] = np.divide(
                                np.log(
                                    np.divide(
                                        speed[upper_index],
                                        speed[lower_index],
                                        out=np.ones_like(speed[lower_index]),
                                        where=valid_speed,
                                    )
                                ),
                                np.log(
                                    heights[upper_index] / heights[lower_index]
                                ),
                                out=np.full_like(speed[lower_index], np.nan),
                                where=valid_speed,
                            )
                            valid_direction = (
                                np.isfinite(direction[lower_index])
                                & np.isfinite(direction[upper_index])
                                & (
                                    vector_magnitude[lower_index]
                                    >= MIN_SHEAR_SPEED_M_S
                                )
                                & (
                                    vector_magnitude[upper_index]
                                    >= MIN_SHEAR_SPEED_M_S
                                )
                            )
                            veer_values[pair_index] = np.where(
                                valid_direction,
                                _wrapped_difference_degrees(
                                    direction[upper_index],
                                    direction[lower_index],
                                ),
                                np.nan,
                            )
                        shear[time_index, :, y_slice, :] = np.ma.masked_invalid(
                            shear_values.astype(np.float32)
                        )
                        veer[time_index, :, y_slice, :] = np.ma.masked_invalid(
                            veer_values.astype(np.float32)
                        )

                target.Conventions = "CF-1.10"
                target.title = "HICAR wind-climatology user-product supplement"
                target.source = str(input_path)
                target.history = (
                    f"Created {datetime.now(timezone.utc).isoformat()} by "
                    "derive_hicar_wind_user_products.py"
                )
                target.product_scope = (
                    "Direction and veer of vector means, variability of sampled "
                    "speed, and shear of scalar mean speed."
                )
                if static_identity is not None:
                    _set_static_domain_identity(target, static_identity)

        with netCDF4.Dataset(temporary_output) as product:
            for name in (
                "wind_from_direction_of_vector_mean",
                "wind_speed_coefficient_of_variation",
                "wind_from_direction_10m_of_vector_mean",
                "wind_shear_exponent_of_mean_speed",
                "wind_directional_veer_of_vector_mean",
            ):
                values = np.ma.asarray(product[name][:])
                if np.ma.count(values) and not np.all(
                    np.isfinite(values.compressed())
                ):
                    raise ValueError(f"derived product contains invalid {name}")
        os.replace(temporary_output, output_path)

        report = {
            "status": "PASS",
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "input": {
                "path": str(input_path),
                "size_bytes": input_path.stat().st_size,
                "sha256": _sha256(input_path),
            },
            "output": str(output_path),
            "output_sha256": _sha256(output_path),
            "shear_pairs_m": [list(pair) for pair in SHEAR_PAIRS],
            "minimum_direction_speed_m_s": MIN_DIRECTION_SPEED_M_S,
            "minimum_shear_speed_m_s": MIN_SHEAR_SPEED_M_S,
            "direction_definition": "wind-from direction of vector mean",
            "shear_definition": "power-law exponent of scalar mean speeds",
            "static_domain": static_identity,
        }
        _write_json_atomic(report_path, report)
        if publish_ready:
            ready_path.touch()
        return report
    finally:
        if temporary_output.exists():
            temporary_output.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--y-block-size", type=int, default=128)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--no-ready", action="store_true")
    args = parser.parse_args()
    try:
        report = create_product(
            args.input,
            args.output,
            y_block_size=args.y_block_size,
            report_path=args.report,
            publish_ready=not args.no_ready,
        )
    except Exception as exc:
        print(f"FAIL: {exc}")
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
