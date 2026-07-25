#!/usr/bin/env python3
"""Create a compact, fixed-height wind-energy product from HICAR output.

This is the reference implementation for the HICAR-native online diagnostic.
It deliberately derives no quantity from an external-model gust field.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Iterable

import netCDF4
import numpy as np


DEFAULT_HEIGHTS_M = (50.0, 75.0, 100.0, 125.0, 150.0, 200.0)
PRODUCT_VARIABLES = (
    "eastward_wind",
    "northward_wind",
    "wind_speed",
    "wind_from_direction",
    "air_density",
    "wind_power_density",
)


def parse_heights(value: str) -> tuple[float, ...]:
    """Parse a strictly increasing comma-separated AGL height list."""
    try:
        heights = tuple(float(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid height list: {value!r}") from exc
    if not heights:
        raise argparse.ArgumentTypeError("at least one height is required")
    if not np.all(np.isfinite(heights)) or any(height <= 0.0 for height in heights):
        raise argparse.ArgumentTypeError("heights must be finite and positive")
    if any(right <= left for left, right in zip(heights, heights[1:])):
        raise argparse.ArgumentTypeError("heights must be strictly increasing")
    return heights


def _as_float64(values: object) -> np.ndarray:
    return np.asarray(np.ma.filled(np.ma.asarray(values), np.nan), dtype=np.float64)


def _copy_attributes(source: netCDF4.Variable, target: netCDF4.Variable) -> None:
    for name in source.ncattrs():
        if name != "_FillValue":
            target.setncattr(name, source.getncattr(name))


def _require_shape(
    variable: netCDF4.Variable,
    expected: tuple[int, ...],
    description: str,
) -> None:
    if variable.shape != expected:
        raise ValueError(
            f"{description} {variable.name!r} has shape {variable.shape}, "
            f"expected {expected}"
        )


def _read_mass_winds(
    dataset: netCDF4.Dataset,
    time_index: int,
    y_slice: slice,
    nz: int,
    ny_block: int,
    nx: int,
) -> tuple[np.ndarray, np.ndarray, str]:
    if "u_mass" in dataset.variables and "v_mass" in dataset.variables:
        u_variable = dataset.variables["u_mass"]
        v_variable = dataset.variables["v_mass"]
        _require_shape(
            u_variable,
            (len(dataset.dimensions["time"]), nz, len(dataset.dimensions["lat_y"]), nx),
            "mass-grid eastward wind",
        )
        _require_shape(
            v_variable,
            (len(dataset.dimensions["time"]), nz, len(dataset.dimensions["lat_y"]), nx),
            "mass-grid northward wind",
        )
        u_mass = _as_float64(u_variable[time_index, :, y_slice, :])
        v_mass = _as_float64(v_variable[time_index, :, y_slice, :])
        return u_mass, v_mass, "u_mass/v_mass"

    for name in ("u", "v"):
        if name not in dataset.variables:
            raise ValueError(
                "input must contain either u_mass/v_mass or staggered u/v winds"
            )
    u_staggered = _as_float64(dataset.variables["u"][time_index, :, y_slice, :])
    v_staggered = _as_float64(
        dataset.variables["v"][
            time_index,
            :,
            slice(y_slice.start, y_slice.stop + 1),
            :,
        ]
    )
    if u_staggered.shape != (nz, ny_block, nx + 1):
        raise ValueError(
            f"staggered u block has shape {u_staggered.shape}, "
            f"expected {(nz, ny_block, nx + 1)}"
        )
    if v_staggered.shape != (nz, ny_block + 1, nx):
        raise ValueError(
            f"staggered v block has shape {v_staggered.shape}, "
            f"expected {(nz, ny_block + 1, nx)}"
        )
    u_mass = 0.5 * (u_staggered[:, :, :-1] + u_staggered[:, :, 1:])
    v_mass = 0.5 * (v_staggered[:, :-1, :] + v_staggered[:, 1:, :])
    return u_mass, v_mass, "staggered u/v, arithmetic de-staggering"


def interpolate_columns(
    z_agl: np.ndarray,
    field: np.ndarray,
    heights_m: Iterable[float],
    *,
    field_name: str,
) -> np.ndarray:
    """Linearly interpolate complete vertical columns without extrapolation."""
    z_agl = _as_float64(z_agl)
    field = _as_float64(field)
    if z_agl.ndim != 3 or field.shape != z_agl.shape:
        raise ValueError(
            f"{field_name}: field and z_agl must have matching (level, y, x) shapes"
        )
    if not np.all(np.isfinite(z_agl)):
        raise ValueError("z_agl contains non-finite values")
    if not np.all(np.isfinite(field)):
        raise ValueError(f"{field_name} contains non-finite values")
    if np.any(np.diff(z_agl, axis=0) <= 0.0):
        raise ValueError("z_agl is not strictly increasing in every column")

    heights = np.asarray(tuple(heights_m), dtype=np.float64)
    result = np.empty((len(heights), *z_agl.shape[1:]), dtype=np.float64)
    for height_index, height in enumerate(heights):
        upper = np.sum(z_agl <= height, axis=0)
        outside = (upper == 0) | (upper >= z_agl.shape[0])
        if np.any(outside):
            count = int(np.count_nonzero(outside))
            raise ValueError(
                f"{field_name}: {height:g} m AGL is outside the model-level "
                f"bracket in {count} columns; extrapolation is forbidden"
            )
        lower = upper - 1
        lower_3d = lower[None, :, :]
        upper_3d = upper[None, :, :]
        z_lower = np.take_along_axis(z_agl, lower_3d, axis=0)[0]
        z_upper = np.take_along_axis(z_agl, upper_3d, axis=0)[0]
        value_lower = np.take_along_axis(field, lower_3d, axis=0)[0]
        value_upper = np.take_along_axis(field, upper_3d, axis=0)[0]
        weight = (height - z_lower) / (z_upper - z_lower)
        result[height_index] = value_lower + weight * (value_upper - value_lower)
    return result


def _read_z_block(
    variable: netCDF4.Variable,
    time_index: int,
    y_slice: slice,
    nt: int,
    nz: int,
    ny: int,
    nx: int,
) -> np.ndarray:
    if variable.shape == (nz, ny, nx):
        return _as_float64(variable[:, y_slice, :])
    if variable.shape == (nt, nz, ny, nx):
        return _as_float64(variable[time_index, :, y_slice, :])
    raise ValueError(
        f"z has shape {variable.shape}; expected {(nz, ny, nx)} "
        f"or {(nt, nz, ny, nx)}"
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_product(
    path: Path,
    *,
    expected_heights: tuple[float, ...],
    expected_shape: tuple[int, int, int, int],
) -> dict[str, object]:
    """Validate the completed reference product before publication."""
    failures: list[str] = []
    summaries: dict[str, dict[str, float]] = {}
    with netCDF4.Dataset(path) as dataset:
        for name in ("time", "height_agl", "lat", "lon", *PRODUCT_VARIABLES):
            if name not in dataset.variables:
                failures.append(f"missing variable {name}")
        if failures:
            return {"status": "FAIL", "failures": failures, "variables": summaries}

        actual_heights = _as_float64(dataset.variables["height_agl"][:])
        if actual_heights.shape != (len(expected_heights),) or not np.allclose(
            actual_heights, expected_heights, rtol=0.0, atol=1.0e-6
        ):
            failures.append(
                f"height_agl is {actual_heights.tolist()}, expected "
                f"{list(expected_heights)}"
            )

        for name in PRODUCT_VARIABLES:
            variable = dataset.variables[name]
            if variable.shape != expected_shape:
                failures.append(
                    f"{name} has shape {variable.shape}, expected {expected_shape}"
                )
                continue
            minimum = np.inf
            maximum = -np.inf
            nonfinite = 0
            for time_index in range(expected_shape[0]):
                values = _as_float64(variable[time_index, ...])
                nonfinite += int(np.count_nonzero(~np.isfinite(values)))
                if np.any(np.isfinite(values)):
                    minimum = min(minimum, float(np.nanmin(values)))
                    maximum = max(maximum, float(np.nanmax(values)))
            summaries[name] = {
                "minimum": minimum,
                "maximum": maximum,
                "nonfinite": nonfinite,
            }
            if nonfinite:
                failures.append(f"{name} contains {nonfinite} non-finite values")

        bounds = {
            "wind_speed": (0.0, 150.0),
            "wind_from_direction": (0.0, 360.0),
            "air_density": (0.2, 1.7),
            "wind_power_density": (0.0, 6_000_000.0),
        }
        for name, (lower, upper) in bounds.items():
            if name not in summaries or summaries[name]["nonfinite"]:
                continue
            minimum = summaries[name]["minimum"]
            maximum = summaries[name]["maximum"]
            upper_violation = maximum >= upper if name == "wind_from_direction" else maximum > upper
            if minimum < lower or upper_violation:
                failures.append(
                    f"{name} range [{minimum:g}, {maximum:g}] is outside "
                    f"[{lower:g}, {upper:g}{')' if name == 'wind_from_direction' else ']'}"
                )

    return {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "variables": summaries,
    }


def create_product(
    input_path: Path,
    static_path: Path,
    output_path: Path,
    *,
    heights_m: tuple[float, ...] = DEFAULT_HEIGHTS_M,
    y_block_size: int = 128,
    report_path: Path | None = None,
    publish_ready: bool = True,
) -> dict[str, object]:
    """Create, validate, and atomically publish one fixed-height product."""
    input_path = input_path.resolve()
    static_path = static_path.resolve()
    output_path = output_path.resolve()
    report_path = (
        report_path.resolve()
        if report_path is not None
        else output_path.with_suffix(output_path.suffix + ".json")
    )
    if not input_path.is_file():
        raise FileNotFoundError(input_path)
    if not static_path.is_file():
        raise FileNotFoundError(static_path)
    if output_path in (input_path, static_path):
        raise ValueError("output must differ from input and static-domain files")
    if y_block_size <= 0:
        raise ValueError("y_block_size must be positive")
    heights_m = parse_heights(",".join(str(height) for height in heights_m))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    ready_path = Path(f"{output_path}.ready")
    if ready_path.exists():
        ready_path.unlink()
    temporary_output = output_path.with_name(f".{output_path.name}.tmp-{os.getpid()}")
    temporary_report = report_path.with_name(f".{report_path.name}.tmp-{os.getpid()}")

    wind_source = ""
    nt = nz = ny = nx = 0
    try:
        with (
            netCDF4.Dataset(input_path) as source,
            netCDF4.Dataset(static_path) as static,
        ):
            for dimension in ("time", "level", "lat_y", "lon_x"):
                if dimension not in source.dimensions:
                    raise ValueError(f"input is missing dimension {dimension}")
            for variable in ("time", "z", "density", "lat", "lon"):
                if variable not in source.variables:
                    raise ValueError(f"input is missing variable {variable}")
            if "topo" not in static.variables:
                raise ValueError("static-domain file is missing topo")

            nt = len(source.dimensions["time"])
            nz = len(source.dimensions["level"])
            ny = len(source.dimensions["lat_y"])
            nx = len(source.dimensions["lon_x"])
            _require_shape(source.variables["density"], (nt, nz, ny, nx), "density")
            _require_shape(source.variables["lat"], (ny, nx), "latitude")
            _require_shape(source.variables["lon"], (ny, nx), "longitude")
            _require_shape(static.variables["topo"], (ny, nx), "static topography")
            if nt == 0 or nz < 2 or ny == 0 or nx == 0:
                raise ValueError(
                    f"input dimensions are not usable: time={nt}, level={nz}, "
                    f"y={ny}, x={nx}"
                )

            with netCDF4.Dataset(temporary_output, "w", format="NETCDF4") as target:
                target.createDimension("time", None)
                target.createDimension("height_agl", len(heights_m))
                target.createDimension("lat_y", ny)
                target.createDimension("lon_x", nx)

                time = target.createVariable("time", source.variables["time"].dtype, ("time",))
                _copy_attributes(source.variables["time"], time)
                time[:] = source.variables["time"][:]

                height = target.createVariable("height_agl", "f4", ("height_agl",))
                height[:] = heights_m
                height.standard_name = "height"
                height.long_name = "height above ground"
                height.units = "m"
                height.positive = "up"
                height.axis = "Z"

                for name in ("lat", "lon"):
                    source_variable = source.variables[name]
                    target_variable = target.createVariable(
                        name, source_variable.dtype, ("lat_y", "lon_x")
                    )
                    _copy_attributes(source_variable, target_variable)
                    target_variable[:, :] = source_variable[:, :]

                chunks = (1, 1, min(y_block_size, ny), min(128, nx))
                attributes = {
                    "eastward_wind": (
                        "eastward_wind",
                        "eastward wind at fixed height above ground",
                        "m s-1",
                    ),
                    "northward_wind": (
                        "northward_wind",
                        "northward wind at fixed height above ground",
                        "m s-1",
                    ),
                    "wind_speed": (
                        "wind_speed",
                        "horizontal wind speed at fixed height above ground",
                        "m s-1",
                    ),
                    "wind_from_direction": (
                        "wind_from_direction",
                        "meteorological wind direction at fixed height above ground",
                        "degree",
                    ),
                    "air_density": (
                        "air_density",
                        "air density at fixed height above ground",
                        "kg m-3",
                    ),
                    "wind_power_density": (
                        None,
                        "density-adjusted wind power density at fixed height above ground",
                        "W m-2",
                    ),
                }
                outputs: dict[str, netCDF4.Variable] = {}
                for name, (standard_name, long_name, units) in attributes.items():
                    variable = target.createVariable(
                        name,
                        "f4",
                        ("time", "height_agl", "lat_y", "lon_x"),
                        zlib=True,
                        complevel=4,
                        shuffle=True,
                        chunksizes=chunks,
                    )
                    if standard_name is not None:
                        variable.standard_name = standard_name
                    variable.long_name = long_name
                    variable.units = units
                    variable.coordinates = "height_agl lat lon"
                    variable.interpolation = (
                        "linear in geometric height above static-domain topography; "
                        "no extrapolation"
                    )
                    outputs[name] = variable

                target.Conventions = "CF-1.10"
                target.title = "HICAR fixed-height wind climatology reference product"
                target.institution = "MeteoSwiss"
                target.source = f"HICAR output {input_path.name}"
                target.history = (
                    f"{datetime.now(timezone.utc).isoformat()} created by "
                    "derive_hicar_wind_climatology.py"
                )
                target.product_contract = "hicar-wind-climatology-reference-v1"
                target.gust_policy = (
                    "No external-model VMAX is preserved and no instantaneous gust "
                    "is inferred by this reference extractor."
                )

                topo = _as_float64(static.variables["topo"][:, :])
                if not np.all(np.isfinite(topo)):
                    raise ValueError("static topography contains non-finite values")
                for time_index in range(nt):
                    for y_start in range(0, ny, y_block_size):
                        y_stop = min(y_start + y_block_size, ny)
                        y_slice = slice(y_start, y_stop)
                        z = _read_z_block(
                            source.variables["z"],
                            time_index,
                            y_slice,
                            nt,
                            nz,
                            ny,
                            nx,
                        )
                        z_agl = z - topo[y_slice, :][None, :, :]
                        u_mass, v_mass, wind_source = _read_mass_winds(
                            source,
                            time_index,
                            y_slice,
                            nz,
                            y_stop - y_start,
                            nx,
                        )
                        density = _as_float64(
                            source.variables["density"][time_index, :, y_slice, :]
                        )
                        u_height = interpolate_columns(
                            z_agl, u_mass, heights_m, field_name="eastward wind"
                        )
                        v_height = interpolate_columns(
                            z_agl, v_mass, heights_m, field_name="northward wind"
                        )
                        density_height = interpolate_columns(
                            z_agl, density, heights_m, field_name="air density"
                        )
                        speed = np.hypot(u_height, v_height)
                        direction = (
                            270.0 - np.degrees(np.arctan2(v_height, u_height))
                        ) % 360.0
                        power_density = 0.5 * density_height * speed**3

                        index = (time_index, slice(None), y_slice, slice(None))
                        outputs["eastward_wind"][index] = u_height.astype(np.float32)
                        outputs["northward_wind"][index] = v_height.astype(np.float32)
                        outputs["wind_speed"][index] = speed.astype(np.float32)
                        outputs["wind_from_direction"][index] = direction.astype(np.float32)
                        outputs["air_density"][index] = density_height.astype(np.float32)
                        outputs["wind_power_density"][index] = power_density.astype(
                            np.float32
                        )

                target.wind_source = wind_source
                target.static_topography_source = str(static_path)

        expected_shape = (nt, len(heights_m), ny, nx)
        validation = validate_product(
            temporary_output,
            expected_heights=heights_m,
            expected_shape=expected_shape,
        )
        if validation["status"] != "PASS":
            raise ValueError(
                "derived product validation failed: "
                + "; ".join(validation["failures"])
            )

        output_sha256 = _sha256(temporary_output)
        os.replace(temporary_output, output_path)
        report: dict[str, object] = {
            "status": "PASS",
            "contract": "hicar-wind-climatology-reference-v1",
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "input": str(input_path),
            "static_domain": str(static_path),
            "output": str(output_path),
            "output_sha256": output_sha256,
            "dimensions": {
                "time": nt,
                "height_agl": len(heights_m),
                "lat_y": ny,
                "lon_x": nx,
            },
            "heights_agl_m": list(heights_m),
            "wind_source": wind_source,
            "interpolation": (
                "arithmetic de-staggering when needed, followed by column-wise "
                "linear interpolation in geometric height AGL; no extrapolation"
            ),
            "gust_policy": (
                "ICON VMAX is not copied; gust diagnostics require a separately "
                "validated HICAR-native method."
            ),
            "validation": validation,
        }
        temporary_report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        os.replace(temporary_report, report_path)
        if publish_ready:
            ready_path.touch()
        return report
    finally:
        for temporary in (temporary_output, temporary_report):
            if temporary.exists():
                temporary.unlink()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="HICAR output NetCDF")
    parser.add_argument(
        "--static-domain",
        type=Path,
        required=True,
        help="matching HICAR static-domain NetCDF containing topo",
    )
    parser.add_argument("--output", type=Path, required=True, help="output NetCDF")
    parser.add_argument(
        "--report",
        type=Path,
        help="JSON report (default: <output>.json)",
    )
    parser.add_argument(
        "--heights",
        type=parse_heights,
        default=DEFAULT_HEIGHTS_M,
        help="strictly increasing comma-separated AGL heights in metres",
    )
    parser.add_argument(
        "--y-block-size",
        type=int,
        default=128,
        help="number of mass-grid rows processed at once",
    )
    parser.add_argument(
        "--no-ready-marker",
        action="store_true",
        help="do not publish <output>.ready after successful validation",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        report = create_product(
            args.input,
            args.static_domain,
            args.output,
            heights_m=args.heights,
            y_block_size=args.y_block_size,
            report_path=args.report,
            publish_ready=not args.no_ready_marker,
        )
    except Exception as exc:
        print(f"FAIL: {exc}")
        return 1
    print(
        f"PASS: published {report['output']} at "
        f"{report['dimensions']['height_agl']} AGL heights"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
