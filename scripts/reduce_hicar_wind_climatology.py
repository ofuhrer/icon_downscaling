#!/usr/bin/env python3
"""Reduce fixed-height HICAR wind samples to compact interval statistics.

The resolved maximum is calculated only from HICAR samples. It is deliberately
not named or described as a short-duration wind gust.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Sequence

import netCDF4
import numpy as np


EXPECTED_HEIGHTS_M = np.asarray(
    (50.0, 75.0, 100.0, 125.0, 150.0, 200.0),
    dtype=np.float64,
)

FIXED_HEIGHT_STATISTICS = {
    "eastward_wind_mean": (
        "eastward_wind",
        "Mean eastward wind at fixed height above ground",
        "m s-1",
        "time: mean",
    ),
    "northward_wind_mean": (
        "northward_wind",
        "Mean northward wind at fixed height above ground",
        "m s-1",
        "time: mean",
    ),
    "wind_speed_mean": (
        "wind_speed",
        "Mean wind speed at fixed height above ground",
        "m s-1",
        "time: mean",
    ),
    "wind_speed_standard_deviation": (
        None,
        "Population standard deviation of sampled wind speed",
        "m s-1",
        "time: standard_deviation",
    ),
    "air_density_mean": (
        "air_density",
        "Mean air density at fixed height above ground",
        "kg m-3",
        "time: mean",
    ),
    "wind_power_density_mean": (
        None,
        "Mean density-adjusted wind power density",
        "W m-2",
        "time: mean",
    ),
    "resolved_wind_speed_max": (
        "wind_speed",
        "Maximum resolved wind speed sampled during the interval",
        "m s-1",
        "time: maximum",
    ),
}

TEN_METRE_STATISTICS = {
    "eastward_wind_10m_mean": (
        "eastward_wind",
        "Mean eastward wind at 10 m above ground",
        "m s-1",
        "time: mean",
    ),
    "northward_wind_10m_mean": (
        "northward_wind",
        "Mean northward wind at 10 m above ground",
        "m s-1",
        "time: mean",
    ),
    "wind_speed_10m_mean": (
        "wind_speed",
        "Mean wind speed at 10 m above ground",
        "m s-1",
        "time: mean",
    ),
    "resolved_wind_speed_10m_max": (
        "wind_speed",
        "Maximum resolved 10 m wind speed sampled during the interval",
        "m s-1",
        "time: maximum",
    ),
}

PBL_SOURCE_VARIABLES = (
    "ustar",
    "surface_roughness",
    "sfc_Ri",
    "hpbl",
)

PBL_MEAN_STATISTICS = {
    "friction_velocity_mean": (
        "ustar",
        (
            "magnitude_of_surface_friction_velocity_in_air",
            "Mean magnitude of surface friction velocity in air",
            "m s-1",
            "time: mean",
        ),
    ),
    "surface_roughness_length_mean": (
        "surface_roughness",
        (
            "surface_roughness_length_for_momentum_in_air",
            "Mean surface roughness length for momentum in air",
            "m",
            "time: mean",
        ),
    ),
    "surface_bulk_richardson_number_mean": (
        "sfc_Ri",
        (
            None,
            "Mean surface-layer bulk Richardson number",
            "1",
            "time: mean",
        ),
    ),
    "boundary_layer_height_mean": (
        "hpbl",
        (
            "atmosphere_boundary_layer_thickness",
            "Mean height of planetary boundary layer",
            "m",
            "time: mean",
        ),
    ),
}

PBL_MAX_STATISTICS = {
    "friction_velocity_max": (
        "ustar",
        (
            "magnitude_of_surface_friction_velocity_in_air",
            "Maximum magnitude of surface friction velocity in air",
            "m s-1",
            "time: maximum",
        ),
    ),
    "boundary_layer_height_max": (
        "hpbl",
        (
            "atmosphere_boundary_layer_thickness",
            "Maximum height of planetary boundary layer",
            "m",
            "time: maximum",
        ),
    ),
}

PBL_STATISTICS = {
    name: metadata
    for name, (_, metadata) in (
        *PBL_MEAN_STATISTICS.items(),
        *PBL_MAX_STATISTICS.items(),
    )
}

DISTRIBUTION_HEIGHTS_M = np.concatenate(
    (np.asarray((10.0,), dtype=np.float64), EXPECTED_HEIGHTS_M)
)
WIND_FROM_DIRECTION_SECTORS_DEG = np.arange(
    0.0,
    360.0,
    30.0,
    dtype=np.float64,
)
WIND_SPEED_THRESHOLDS_M_S = np.asarray(
    (3.0, 5.0, 10.0, 15.0, 20.0, 25.0),
    dtype=np.float64,
)
CALM_WIND_SPEED_THRESHOLD_M_S = 0.5
DISTRIBUTION_VARIABLES = (
    "wind_from_direction_sector_count",
    "wind_speed_threshold_exceedance_count",
    "calm_wind_count",
)

STATIC_DOMAIN_ATTRIBUTES = (
    "static_domain_path",
    "static_domain_sha256",
    "static_domain_size_bytes",
    "static_domain_grid_ny",
    "static_domain_grid_nx",
    "static_domain_dx_m",
    "static_domain_dy_m",
    "static_domain_projection",
)

STATIC_DOMAIN_REQUIRED_VARIABLES = (
    "x",
    "y",
    "lat",
    "lon",
    "topo",
    "landmask",
    "landuse",
)


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


def _static_domain_identity(
    dataset: netCDF4.Dataset,
) -> dict[str, str | int | float] | None:
    """Read a complete static-domain identity from a compact wind product."""
    present = [attribute in dataset.ncattrs() for attribute in STATIC_DOMAIN_ATTRIBUTES]
    if any(present) and not all(present):
        missing = [
            attribute
            for attribute, is_present in zip(STATIC_DOMAIN_ATTRIBUTES, present)
            if not is_present
        ]
        raise ValueError(
            "wind product has an incomplete static-domain identity; missing "
            + ", ".join(missing)
        )
    if not any(present):
        return None
    return {
        "static_domain_path": str(dataset.static_domain_path),
        "static_domain_sha256": str(dataset.static_domain_sha256),
        "static_domain_size_bytes": int(dataset.static_domain_size_bytes),
        "static_domain_grid_ny": int(dataset.static_domain_grid_ny),
        "static_domain_grid_nx": int(dataset.static_domain_grid_nx),
        "static_domain_dx_m": float(dataset.static_domain_dx_m),
        "static_domain_dy_m": float(dataset.static_domain_dy_m),
        "static_domain_projection": str(dataset.static_domain_projection),
    }


def _set_static_domain_identity(
    dataset: netCDF4.Dataset,
    identity: dict[str, object],
) -> None:
    for attribute in STATIC_DOMAIN_ATTRIBUTES:
        dataset.setncattr(attribute, identity[attribute])
    dataset.static_domain_companion_policy = (
        "The external HICAR static-domain NetCDF is an immutable companion "
        "identified by SHA-256. Terrain slope, aspect, and complexity are "
        "deterministic derivatives of its projected x/y and topo fields."
    )


def _validate_static_domain(
    static_path: Path,
    wind_source: netCDF4.Dataset,
    *,
    y_block_size: int,
) -> dict[str, object]:
    """Validate and identify the static companion for a wind source grid."""
    static_path = static_path.resolve()
    ready_path = Path(f"{static_path}.ready")
    if not static_path.is_file() or not ready_path.is_file():
        raise ValueError(f"static-domain publication is incomplete: {static_path}")

    ny = len(wind_source.dimensions["lat_y"])
    nx = len(wind_source.dimensions["lon_x"])
    with netCDF4.Dataset(static_path) as static:
        for dimension, expected in (("y", ny), ("x", nx)):
            if dimension not in static.dimensions:
                raise ValueError(f"static domain is missing dimension {dimension}")
            if len(static.dimensions[dimension]) != expected:
                raise ValueError(
                    f"static-domain {dimension} size differs from the wind grid"
                )
        for name in STATIC_DOMAIN_REQUIRED_VARIABLES:
            if name not in static.variables:
                raise ValueError(f"static domain is missing variable {name}")
        if static["x"].dimensions != ("x",) or static["y"].dimensions != ("y",):
            raise ValueError("static-domain projected coordinates have invalid dimensions")
        for name in ("lat", "lon", "topo", "landmask", "landuse"):
            if static[name].dimensions != ("y", "x"):
                raise ValueError(f"static-domain {name} has invalid dimensions")

        x = _as_float64(static["x"][:])
        y = _as_float64(static["y"][:])
        if not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)):
            raise ValueError("static-domain projected coordinates are non-finite")
        if x.size < 2 or y.size < 2:
            raise ValueError("static-domain projected grid is too small")
        x_steps = np.diff(x)
        y_steps = np.diff(y)
        dx_m = float(np.median(x_steps))
        dy_m = float(np.median(y_steps))
        if dx_m <= 0.0 or dy_m <= 0.0:
            raise ValueError("static-domain projected coordinates must increase")
        if not np.allclose(x_steps, dx_m, rtol=0.0, atol=1.0e-3):
            raise ValueError("static-domain x coordinate is not regular")
        if not np.allclose(y_steps, dy_m, rtol=0.0, atol=1.0e-3):
            raise ValueError("static-domain y coordinate is not regular")
        if "hicar_dx_m" in static.ncattrs() and not np.isclose(
            float(static.hicar_dx_m),
            dx_m,
            rtol=0.0,
            atol=1.0e-3,
        ):
            raise ValueError("static-domain hicar_dx_m disagrees with x spacing")

        for y_start in range(0, ny, y_block_size):
            y_stop = min(y_start + y_block_size, ny)
            y_slice = slice(y_start, y_stop)
            for name in ("lat", "lon"):
                static_values = _as_float64(static[name][y_slice, :])
                wind_values = _as_float64(wind_source[name][y_slice, :])
                if (
                    not np.all(np.isfinite(static_values))
                    or not np.allclose(
                        static_values,
                        wind_values,
                        rtol=0.0,
                        atol=1.0e-5,
                    )
                ):
                    raise ValueError(
                        f"static-domain {name} does not match the wind grid"
                    )
            topo = _as_float64(static["topo"][y_slice, :])
            landmask = _as_float64(static["landmask"][y_slice, :])
            landuse = _as_float64(static["landuse"][y_slice, :])
            if not (
                np.all(np.isfinite(topo))
                and np.all(np.isfinite(landmask))
                and np.all(np.isfinite(landuse))
            ):
                raise ValueError("static-domain context fields contain non-finite values")
            if np.any((landmask != 0.0) & (landmask != 1.0)):
                raise ValueError("static-domain landmask must contain only 0 and 1")

        projection = getattr(static, "hicar_projection", "")
        if not projection:
            x_zero = int(np.argmin(np.abs(x)))
            y_zero = int(np.argmin(np.abs(y)))
            if abs(float(x[x_zero])) > dx_m * 1.0e-6 or abs(
                float(y[y_zero])
            ) > dy_m * 1.0e-6:
                raise ValueError(
                    "static domain lacks projection metadata and has no x=y=0 "
                    "cell from which to recover its AEQD origin"
                )
            center_lat = float(static["lat"][y_zero, x_zero])
            center_lon = float(static["lon"][y_zero, x_zero])
            projection = (
                f"+proj=aeqd +lat_0={center_lat:.8f} "
                f"+lon_0={center_lon:.8f} +x_0=0 +y_0=0 "
                "+datum=WGS84 +units=m +no_defs"
            )

    return {
        "static_domain_path": str(static_path),
        "static_domain_sha256": _sha256(static_path),
        "static_domain_size_bytes": static_path.stat().st_size,
        "static_domain_grid_ny": ny,
        "static_domain_grid_nx": nx,
        "static_domain_dx_m": dx_m,
        "static_domain_dy_m": dy_m,
        "static_domain_projection": projection,
        "required_variables": list(STATIC_DOMAIN_REQUIRED_VARIABLES),
        "terrain_derivatives": {
            "source": "topo on projected x/y coordinates",
            "slope": "atan(hypot(dz/dx, dz/dy))",
            "aspect": "clockwise bearing of downslope gradient from north",
            "complexity": (
                "application-selected neighbourhood statistics of topo; "
                "window and metric must be recorded"
            ),
        },
    }


def _validate_source(
    dataset: netCDF4.Dataset,
) -> tuple[int, int, int, int, bool]:
    for dimension in ("time", "height_agl", "lat_y", "lon_x"):
        if dimension not in dataset.dimensions:
            raise ValueError(f"input is missing dimension {dimension}")
    for variable in (
        "time",
        "height_agl",
        "lat",
        "lon",
        "u10m",
        "v10m",
        "u_agl",
        "v_agl",
        "rho_agl",
    ):
        if variable not in dataset.variables:
            raise ValueError(f"input is missing variable {variable}")

    nt = len(dataset.dimensions["time"])
    nh = len(dataset.dimensions["height_agl"])
    ny = len(dataset.dimensions["lat_y"])
    nx = len(dataset.dimensions["lon_x"])
    if nt < 1:
        raise ValueError("input contains no time records")
    heights = _as_float64(dataset.variables["height_agl"][:])
    if heights.shape != EXPECTED_HEIGHTS_M.shape or not np.allclose(
        heights,
        EXPECTED_HEIGHTS_M,
        rtol=0.0,
        atol=1.0e-6,
    ):
        raise ValueError(
            f"height_agl is {heights.tolist()}, expected "
            f"{EXPECTED_HEIGHTS_M.tolist()}"
        )
    expected_fixed = (nt, nh, ny, nx)
    for name in ("u_agl", "v_agl", "rho_agl"):
        variable = dataset.variables[name]
        if variable.dimensions != ("time", "height_agl", "lat_y", "lon_x"):
            raise ValueError(f"{name} has unsupported dimensions {variable.dimensions}")
        if variable.shape != expected_fixed:
            raise ValueError(f"{name} has shape {variable.shape}, expected {expected_fixed}")
    expected_surface = (nt, ny, nx)
    for name in ("u10m", "v10m"):
        variable = dataset.variables[name]
        if variable.dimensions != ("time", "lat_y", "lon_x"):
            raise ValueError(f"{name} has unsupported dimensions {variable.dimensions}")
        if variable.shape != expected_surface:
            raise ValueError(
                f"{name} has shape {variable.shape}, expected {expected_surface}"
            )
    for name in ("lat", "lon"):
        if dataset.variables[name].shape != (ny, nx):
            raise ValueError(f"{name} does not match the mass grid")
    pbl_present = [
        name in dataset.variables
        for name in PBL_SOURCE_VARIABLES
    ]
    if any(pbl_present) and not all(pbl_present):
        missing = [
            name
            for name, present in zip(PBL_SOURCE_VARIABLES, pbl_present)
            if not present
        ]
        raise ValueError(
            "input has an incomplete surface/PBL diagnostic set; missing "
            + ", ".join(missing)
        )
    has_pbl = all(pbl_present)
    if has_pbl:
        for name in PBL_SOURCE_VARIABLES:
            variable = dataset.variables[name]
            if variable.dimensions != ("time", "lat_y", "lon_x"):
                raise ValueError(
                    f"{name} has unsupported dimensions {variable.dimensions}"
                )
            if variable.shape != expected_surface:
                raise ValueError(
                    f"{name} has shape {variable.shape}, expected {expected_surface}"
                )
    return nt, nh, ny, nx, has_pbl


def _time_groups(
    values: np.ndarray,
    units: str,
    calendar: str,
    interval_seconds: int,
) -> tuple[list[tuple[int, int]], float]:
    values = _as_float64(values)
    if values.size < 2:
        raise ValueError("at least two time records are required")
    if not np.all(np.isfinite(values)):
        raise ValueError("time contains non-finite values")
    dates = netCDF4.num2date(values, units, calendar=calendar)
    seconds = np.asarray(
        netCDF4.date2num(
            dates,
            "seconds since 1970-01-01 00:00:00",
            calendar=calendar,
        ),
        dtype=np.float64,
    )
    differences = np.diff(seconds)
    if np.any(differences <= 0.0) or not np.allclose(
        differences,
        differences[0],
        rtol=0.0,
        atol=1.0e-3,
    ):
        raise ValueError("input time must be strictly increasing and regular")
    sample_seconds = float(differences[0])
    ratio = interval_seconds / sample_seconds
    samples_per_interval = int(round(ratio))
    if (
        samples_per_interval < 1
        or not np.isclose(ratio, samples_per_interval, rtol=0.0, atol=1.0e-6)
    ):
        raise ValueError("reduction interval must be a multiple of the sample interval")
    if (len(values) - 1) % samples_per_interval:
        raise ValueError(
            "input duration must contain a whole number of reduction intervals"
        )
    groups = [
        (start, start + samples_per_interval)
        for start in range(0, len(values) - 1, samples_per_interval)
    ]
    return groups, sample_seconds


def _canonicalize_interval_time(
    values: np.ndarray,
    units: str,
    calendar: str,
    interval_seconds: int,
    interval_start: str,
) -> tuple[np.ndarray, bool]:
    """Return an exact regular axis and whether a boundary record was inserted."""
    values = _as_float64(values)
    if values.size < 2:
        raise ValueError(
            "an explicit-start reduction needs at least two samples "
            "to establish cadence"
        )
    if not np.all(np.isfinite(values)):
        raise ValueError("time contains non-finite values")

    start_datetime = datetime.fromisoformat(
        interval_start.strip().replace("T", " ").replace("Z", "")
    )
    start_value = float(
        netCDF4.date2num(start_datetime, units, calendar=calendar)
    )
    numeric_second = float(
        netCDF4.date2num(
            start_datetime + timedelta(seconds=1),
            units,
            calendar=calendar,
        )
        - start_value
    )
    if numeric_second <= 0.0:
        raise ValueError("could not determine the source time unit")

    offsets_seconds = (values - start_value) / numeric_second
    differences = np.diff(offsets_seconds)
    if np.any(differences <= 0.0):
        raise ValueError("input time must be strictly increasing and regular")
    source_step_seconds = float(np.median(differences))
    samples_per_interval = int(round(interval_seconds / source_step_seconds))
    if samples_per_interval < 1:
        raise ValueError("reduction interval must be a multiple of the sample interval")
    sample_seconds = interval_seconds / samples_per_interval

    if abs(source_step_seconds - sample_seconds) > 1.0:
        raise ValueError("reduction interval must be a multiple of the sample interval")
    first_sample_index = 0 if abs(offsets_seconds[0]) <= 1.0 else 1
    expected_offsets = (
        np.arange(values.size, dtype=np.float64) + first_sample_index
    ) * sample_seconds
    if np.any(np.abs(offsets_seconds - expected_offsets) > 1.0):
        if first_sample_index:
            raise ValueError(
                "first sample is neither the interval start nor "
                "one regular sample after it"
            )
        raise ValueError(
            "input time differs from the explicit regular cadence by more than 1 s"
        )

    inserted_boundary = bool(first_sample_index)
    canonical_size = values.size + int(inserted_boundary)
    canonical_values = np.asarray(
        netCDF4.date2num(
            [
                start_datetime + timedelta(seconds=index * sample_seconds)
                for index in range(canonical_size)
            ],
            units,
            calendar=calendar,
        ),
        dtype=np.float64,
    )
    return canonical_values, inserted_boundary


def _create_statistic(
    dataset: netCDF4.Dataset,
    name: str,
    dimensions: tuple[str, ...],
    metadata: tuple[str | None, str, str, str],
    *,
    sampling_comment: str | None = None,
    coordinates: str | None = None,
) -> netCDF4.Variable:
    standard_name, long_name, units, cell_methods = metadata
    variable = dataset.createVariable(
        name,
        "f4",
        dimensions,
        zlib=True,
        complevel=1,
        shuffle=True,
        fill_value=np.float32(9.96921e36),
    )
    if standard_name is not None:
        variable.standard_name = standard_name
    variable.long_name = long_name
    variable.units = units
    variable.cell_methods = cell_methods
    variable.coordinates = coordinates or (
        "height_agl lat lon"
        if "height_agl" in dimensions
        else "height_10m lat lon"
    )
    if sampling_comment is not None:
        variable.comment = sampling_comment
    return variable


def _validate_distribution_counts(
    dataset: netCDF4.Dataset,
    *,
    y_block_size: int,
) -> None:
    nt = len(dataset.dimensions["time"])
    ny = len(dataset.dimensions["lat_y"])
    sample_count = np.asarray(dataset["sample_count"][:], dtype=np.int64)
    for time_index in range(nt):
        expected_count = int(sample_count[time_index])
        for y_start in range(0, ny, y_block_size):
            y_stop = min(y_start + y_block_size, ny)
            y_slice = slice(y_start, y_stop)
            directions = np.asarray(
                dataset["wind_from_direction_sector_count"][
                    time_index, :, :, y_slice, :
                ],
                dtype=np.int64,
            )
            thresholds = np.asarray(
                dataset["wind_speed_threshold_exceedance_count"][
                    time_index, :, :, y_slice, :
                ],
                dtype=np.int64,
            )
            calm = np.asarray(
                dataset["calm_wind_count"][time_index, :, y_slice, :],
                dtype=np.int64,
            )
            if (
                np.any(directions < 0)
                or np.any(thresholds < 0)
                or np.any(calm < 0)
            ):
                raise ValueError("published distribution product has negative counts")
            if np.any(np.sum(directions, axis=1) + calm != expected_count):
                raise ValueError(
                    "direction-sector and calm counts do not sum to sample_count"
                )
            if np.any(thresholds > expected_count):
                raise ValueError(
                    "speed-threshold count exceeds sample_count"
                )
            if np.any(np.diff(thresholds, axis=1) > 0):
                raise ValueError(
                    "speed-threshold exceedance counts are not nested"
                )


def reduce_product(
    input_path: Path | Sequence[Path],
    output_path: Path,
    *,
    interval_seconds: int,
    interval_start: str | None = None,
    static_file: Path | None = None,
    y_block_size: int = 128,
    report_path: Path | None = None,
    publish_ready: bool = True,
) -> dict[str, object]:
    """Reduce regular fixed-height HICAR files and publish them atomically."""
    if isinstance(input_path, (str, Path)):
        input_paths = (Path(input_path).resolve(),)
    else:
        input_paths = tuple(Path(path).resolve() for path in input_path)
    if not input_paths:
        raise ValueError("at least one input file is required")
    output_path = output_path.resolve()
    report_path = (
        report_path.resolve()
        if report_path is not None
        else output_path.with_suffix(output_path.suffix + ".json")
    )
    for path in input_paths:
        if not path.is_file():
            raise FileNotFoundError(path)
    if output_path in input_paths:
        raise ValueError("output must differ from every input")
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    if y_block_size <= 0:
        raise ValueError("y_block_size must be positive")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    ready_path = Path(f"{output_path}.ready")
    report_ready_path = Path(f"{report_path}.ready")
    if ready_path.exists():
        ready_path.unlink()
    if report_ready_path.exists():
        report_ready_path.unlink()
    temporary_output = output_path.with_name(f".{output_path.name}.tmp-{os.getpid()}")

    try:
        with ExitStack() as stack:
            sources = [
                stack.enter_context(netCDF4.Dataset(path))
                for path in input_paths
            ]
            source = sources[0]
            _, nh, ny, nx, has_pbl = _validate_source(source)
            static_identity = (
                _validate_static_domain(
                    Path(static_file),
                    source,
                    y_block_size=y_block_size,
                )
                if static_file is not None
                else None
            )
            time_source = source.variables["time"]
            time_units = time_source.units
            time_calendar = getattr(time_source, "calendar", "standard")
            time_values: list[np.ndarray] = []
            record_map: list[tuple[int, int]] = []
            for source_index, current in enumerate(sources):
                (
                    current_nt,
                    current_nh,
                    current_ny,
                    current_nx,
                    current_has_pbl,
                ) = _validate_source(current)
                if (current_nh, current_ny, current_nx) != (nh, ny, nx):
                    raise ValueError(
                        f"input grid differs in {input_paths[source_index]}"
                    )
                if current_has_pbl != has_pbl:
                    raise ValueError(
                        "input files disagree on surface/PBL diagnostic presence"
                    )
                current_time = current.variables["time"]
                if (
                    current_time.units != time_units
                    or getattr(current_time, "calendar", "standard")
                    != time_calendar
                ):
                    raise ValueError(
                        f"input time metadata differs in {input_paths[source_index]}"
                    )
                time_values.append(_as_float64(current_time[:]))
                record_map.extend(
                    (source_index, local_index)
                    for local_index in range(current_nt)
                )
                if source_index:
                    for coordinate_name in ("lat", "lon"):
                        reference = source.variables[coordinate_name]
                        candidate = current.variables[coordinate_name]
                        reference_corners = _as_float64(
                            [
                                reference[0, 0],
                                reference[-1, -1],
                            ]
                        )
                        candidate_corners = _as_float64(
                            [
                                candidate[0, 0],
                                candidate[-1, -1],
                            ]
                        )
                        if not np.allclose(
                            candidate_corners,
                            reference_corners,
                            rtol=0.0,
                            atol=1.0e-6,
                        ):
                            raise ValueError(
                                f"{coordinate_name} grid differs in "
                                f"{input_paths[source_index]}"
                            )
            concatenated_time = np.concatenate(time_values)
            canonicalized_time = False
            if interval_start is not None:
                concatenated_time, inserted_boundary = _canonicalize_interval_time(
                    concatenated_time,
                    time_units,
                    time_calendar,
                    interval_seconds,
                    interval_start,
                )
                if inserted_boundary:
                    record_map.insert(0, (-1, -1))
                canonicalized_time = True
            groups, sample_seconds = _time_groups(
                concatenated_time,
                time_units,
                time_calendar,
                interval_seconds,
            )
            sampling_comment = (
                f"Maximum of HICAR-resolved samples spaced {sample_seconds:g} s "
                "apart; this is not a 3-second gust."
            )
            with netCDF4.Dataset(temporary_output, "w", format="NETCDF4") as target:
                target.createDimension("time", len(groups))
                target.createDimension("bounds", 2)
                target.createDimension("height_agl", nh)
                target.createDimension(
                    "wind_distribution_height_agl",
                    DISTRIBUTION_HEIGHTS_M.size,
                )
                target.createDimension(
                    "wind_from_direction_sector",
                    WIND_FROM_DIRECTION_SECTORS_DEG.size,
                )
                target.createDimension(
                    "wind_speed_threshold",
                    WIND_SPEED_THRESHOLDS_M_S.size,
                )
                target.createDimension("lat_y", ny)
                target.createDimension("lon_x", nx)

                time = target.createVariable("time", "f8", ("time",))
                time.units = time_units
                time.calendar = time_calendar
                time.standard_name = "time"
                time.bounds = "time_bounds"
                time[:] = [concatenated_time[end] for _, end in groups]
                bounds = target.createVariable(
                    "time_bounds",
                    "f8",
                    ("time", "bounds"),
                )
                bounds.units = time.units
                bounds.calendar = time.calendar
                bounds[:] = [
                    (concatenated_time[start], concatenated_time[end])
                    for start, end in groups
                ]

                height = target.createVariable("height_agl", "f4", ("height_agl",))
                height.standard_name = "height"
                height.long_name = "height above ground"
                height.units = "m"
                height.positive = "up"
                height.axis = "Z"
                height[:] = EXPECTED_HEIGHTS_M.astype(np.float32)
                height_10m = target.createVariable("height_10m", "f4")
                height_10m.standard_name = "height"
                height_10m.long_name = "height above ground"
                height_10m.units = "m"
                height_10m.positive = "up"
                height_10m.axis = "Z"
                height_10m.assignValue(np.float32(10.0))
                distribution_height = target.createVariable(
                    "wind_distribution_height_agl",
                    "f4",
                    ("wind_distribution_height_agl",),
                )
                distribution_height.standard_name = "height"
                distribution_height.long_name = (
                    "height above ground for wind distribution counts"
                )
                distribution_height.units = "m"
                distribution_height.positive = "up"
                distribution_height[:] = DISTRIBUTION_HEIGHTS_M.astype(np.float32)
                direction_sector = target.createVariable(
                    "wind_from_direction_sector",
                    "f4",
                    ("wind_from_direction_sector",),
                )
                direction_sector.standard_name = "wind_from_direction"
                direction_sector.long_name = (
                    "centre of meteorological wind-from direction sector"
                )
                direction_sector.units = "degree"
                direction_sector.bounds = "wind_from_direction_sector_bounds"
                direction_sector[:] = WIND_FROM_DIRECTION_SECTORS_DEG.astype(
                    np.float32
                )
                direction_bounds = target.createVariable(
                    "wind_from_direction_sector_bounds",
                    "f4",
                    ("wind_from_direction_sector", "bounds"),
                )
                direction_bounds.units = "degree"
                direction_bounds[:] = np.column_stack(
                    (
                        WIND_FROM_DIRECTION_SECTORS_DEG - 15.0,
                        WIND_FROM_DIRECTION_SECTORS_DEG + 15.0,
                    )
                ).astype(np.float32)
                speed_threshold = target.createVariable(
                    "wind_speed_threshold",
                    "f4",
                    ("wind_speed_threshold",),
                )
                speed_threshold.long_name = (
                    "lower bound for wind-speed exceedance count"
                )
                speed_threshold.units = "m s-1"
                speed_threshold[:] = WIND_SPEED_THRESHOLDS_M_S.astype(np.float32)

                for coordinate_name in ("lat", "lon"):
                    source_coordinate = source.variables[coordinate_name]
                    coordinate = target.createVariable(
                        coordinate_name,
                        "f4",
                        ("lat_y", "lon_x"),
                        zlib=True,
                        complevel=1,
                        shuffle=True,
                    )
                    for attribute in source_coordinate.ncattrs():
                        if attribute != "_FillValue":
                            coordinate.setncattr(
                                attribute,
                                source_coordinate.getncattr(attribute),
                            )
                    coordinate[:] = source_coordinate[:]

                fixed_outputs = {
                    name: _create_statistic(
                        target,
                        name,
                        ("time", "height_agl", "lat_y", "lon_x"),
                        metadata,
                        sampling_comment=(
                            sampling_comment if name == "resolved_wind_speed_max" else None
                        ),
                    )
                    for name, metadata in FIXED_HEIGHT_STATISTICS.items()
                }
                surface_outputs = {
                    name: _create_statistic(
                        target,
                        name,
                        ("time", "lat_y", "lon_x"),
                        metadata,
                        sampling_comment=(
                            sampling_comment
                            if name == "resolved_wind_speed_10m_max"
                            else None
                        ),
                    )
                    for name, metadata in TEN_METRE_STATISTICS.items()
                }
                pbl_outputs = (
                    {
                        name: _create_statistic(
                            target,
                            name,
                            ("time", "lat_y", "lon_x"),
                            metadata,
                            coordinates="lat lon",
                        )
                        for name, metadata in PBL_STATISTICS.items()
                    }
                    if has_pbl
                    else {}
                )
                distribution_outputs = {
                    "wind_from_direction_sector_count": target.createVariable(
                        "wind_from_direction_sector_count",
                        "i4",
                        (
                            "time",
                            "wind_distribution_height_agl",
                            "wind_from_direction_sector",
                            "lat_y",
                            "lon_x",
                        ),
                        zlib=True,
                        complevel=1,
                        shuffle=True,
                    ),
                    "wind_speed_threshold_exceedance_count": target.createVariable(
                        "wind_speed_threshold_exceedance_count",
                        "i4",
                        (
                            "time",
                            "wind_distribution_height_agl",
                            "wind_speed_threshold",
                            "lat_y",
                            "lon_x",
                        ),
                        zlib=True,
                        complevel=1,
                        shuffle=True,
                    ),
                    "calm_wind_count": target.createVariable(
                        "calm_wind_count",
                        "i4",
                        (
                            "time",
                            "wind_distribution_height_agl",
                            "lat_y",
                            "lon_x",
                        ),
                        zlib=True,
                        complevel=1,
                        shuffle=True,
                    ),
                }
                distribution_outputs[
                    "wind_from_direction_sector_count"
                ].long_name = "count of non-calm samples by wind-from direction sector"
                distribution_outputs[
                    "wind_from_direction_sector_count"
                ].units = "1"
                distribution_outputs[
                    "wind_from_direction_sector_count"
                ].cell_methods = "time: sum"
                distribution_outputs[
                    "wind_from_direction_sector_count"
                ].coordinates = (
                    "wind_distribution_height_agl wind_from_direction_sector lat lon"
                )
                distribution_outputs[
                    "wind_from_direction_sector_count"
                ].calm_exclusion = (
                    f"wind_speed < {CALM_WIND_SPEED_THRESHOLD_M_S:g} m s-1"
                )
                distribution_outputs[
                    "wind_speed_threshold_exceedance_count"
                ].long_name = "count of samples meeting or exceeding wind-speed threshold"
                distribution_outputs[
                    "wind_speed_threshold_exceedance_count"
                ].units = "1"
                distribution_outputs[
                    "wind_speed_threshold_exceedance_count"
                ].cell_methods = "time: sum"
                distribution_outputs[
                    "wind_speed_threshold_exceedance_count"
                ].coordinates = (
                    "wind_distribution_height_agl wind_speed_threshold lat lon"
                )
                distribution_outputs[
                    "wind_speed_threshold_exceedance_count"
                ].threshold_operator = "greater_than_or_equal_to"
                distribution_outputs[
                    "calm_wind_count"
                ].long_name = "count of calm-wind samples"
                distribution_outputs["calm_wind_count"].units = "1"
                distribution_outputs["calm_wind_count"].cell_methods = "time: sum"
                distribution_outputs["calm_wind_count"].coordinates = (
                    "wind_distribution_height_agl lat lon"
                )
                distribution_outputs["calm_wind_count"].calm_definition = (
                    f"wind_speed < {CALM_WIND_SPEED_THRESHOLD_M_S:g} m s-1"
                )
                fixed_outputs["wind_power_density_mean"].formula = (
                    "0.5 * air_density * wind_speed^3 evaluated before "
                    "temporal averaging"
                )
                fixed_outputs["wind_speed_standard_deviation"].statistical_method = (
                    "population standard deviation"
                )
                count = target.createVariable("sample_count", "i4", ("time",))
                count.long_name = "number of HICAR samples in the interval"
                count.units = "1"
                count[:] = [end - start for start, end in groups]

                for group_index, (start, end) in enumerate(groups):
                    sample_indices = range(start + 1, end + 1)
                    n_samples = end - start
                    for y_start in range(0, ny, y_block_size):
                        y_stop = min(y_start + y_block_size, ny)
                        y_slice = slice(y_start, y_stop)
                        fixed_shape = (nh, y_stop - y_start, nx)
                        surface_shape = (y_stop - y_start, nx)
                        sum_u = np.zeros(fixed_shape, dtype=np.float64)
                        sum_v = np.zeros(fixed_shape, dtype=np.float64)
                        sum_speed = np.zeros(fixed_shape, dtype=np.float64)
                        sum_speed_squared = np.zeros(fixed_shape, dtype=np.float64)
                        sum_rho = np.zeros(fixed_shape, dtype=np.float64)
                        sum_power = np.zeros(fixed_shape, dtype=np.float64)
                        max_speed = np.full(fixed_shape, -np.inf, dtype=np.float64)
                        sum_u10 = np.zeros(surface_shape, dtype=np.float64)
                        sum_v10 = np.zeros(surface_shape, dtype=np.float64)
                        sum_speed10 = np.zeros(surface_shape, dtype=np.float64)
                        max_speed10 = np.full(
                            surface_shape,
                            -np.inf,
                            dtype=np.float64,
                        )
                        pbl_sums = {
                            name: np.zeros(surface_shape, dtype=np.float64)
                            for name in PBL_MEAN_STATISTICS
                        } if has_pbl else {}
                        pbl_maxima = {
                            name: np.full(
                                surface_shape,
                                -np.inf,
                                dtype=np.float64,
                            )
                            for name in PBL_MAX_STATISTICS
                        } if has_pbl else {}
                        direction_counts = np.zeros(
                            (
                                DISTRIBUTION_HEIGHTS_M.size,
                                WIND_FROM_DIRECTION_SECTORS_DEG.size,
                                y_stop - y_start,
                                nx,
                            ),
                            dtype=np.int32,
                        )
                        threshold_counts = np.zeros(
                            (
                                DISTRIBUTION_HEIGHTS_M.size,
                                WIND_SPEED_THRESHOLDS_M_S.size,
                                y_stop - y_start,
                                nx,
                            ),
                            dtype=np.int32,
                        )
                        calm_counts = np.zeros(
                            (
                                DISTRIBUTION_HEIGHTS_M.size,
                                y_stop - y_start,
                                nx,
                            ),
                            dtype=np.int32,
                        )

                        for time_index in sample_indices:
                            source_index, local_time_index = record_map[time_index]
                            current = sources[source_index]
                            u = _as_float64(
                                current.variables["u_agl"][
                                    local_time_index, :, y_slice, :
                                ]
                            )
                            v = _as_float64(
                                current.variables["v_agl"][
                                    local_time_index, :, y_slice, :
                                ]
                            )
                            rho = _as_float64(
                                current.variables["rho_agl"][
                                    local_time_index, :, y_slice, :
                                ]
                            )
                            u10 = _as_float64(
                                current.variables["u10m"][
                                    local_time_index, y_slice, :
                                ]
                            )
                            v10 = _as_float64(
                                current.variables["v10m"][
                                    local_time_index, y_slice, :
                                ]
                            )
                            pbl_values = (
                                {
                                    source_name: _as_float64(
                                        current.variables[source_name][
                                            local_time_index, y_slice, :
                                        ]
                                    )
                                    for source_name in PBL_SOURCE_VARIABLES
                                }
                                if has_pbl
                                else {}
                            )
                            if not all(
                                np.all(np.isfinite(values))
                                for values in (
                                    u,
                                    v,
                                    rho,
                                    u10,
                                    v10,
                                    *pbl_values.values(),
                                )
                            ):
                                raise ValueError(
                                    "non-finite source value at global time index "
                                    f"{time_index}"
                                )
                            if np.any(rho <= 0.0):
                                raise ValueError(
                                    "non-positive air density at global time index "
                                    f"{time_index}"
                                )
                            speed = np.hypot(u, v)
                            speed10 = np.hypot(u10, v10)
                            sum_u += u
                            sum_v += v
                            sum_speed += speed
                            sum_speed_squared += speed * speed
                            sum_rho += rho
                            sum_power += 0.5 * rho * speed**3
                            np.maximum(max_speed, speed, out=max_speed)
                            sum_u10 += u10
                            sum_v10 += v10
                            sum_speed10 += speed10
                            np.maximum(max_speed10, speed10, out=max_speed10)
                            for name, (source_name, _) in (
                                PBL_MEAN_STATISTICS.items() if has_pbl else ()
                            ):
                                pbl_sums[name] += pbl_values[source_name]
                            for name, (source_name, _) in (
                                PBL_MAX_STATISTICS.items() if has_pbl else ()
                            ):
                                np.maximum(
                                    pbl_maxima[name],
                                    pbl_values[source_name],
                                    out=pbl_maxima[name],
                                )
                            distribution_u = np.concatenate((u10[None, ...], u))
                            distribution_v = np.concatenate((v10[None, ...], v))
                            distribution_speed = np.hypot(
                                distribution_u,
                                distribution_v,
                            )
                            calm = (
                                distribution_speed
                                < CALM_WIND_SPEED_THRESHOLD_M_S
                            )
                            calm_counts += calm
                            direction = (
                                270.0
                                - np.degrees(
                                    np.arctan2(distribution_v, distribution_u)
                                )
                            ) % 360.0
                            sector_index = np.floor(
                                (
                                    (
                                        direction
                                        + 0.5
                                        * (
                                            WIND_FROM_DIRECTION_SECTORS_DEG[1]
                                            - WIND_FROM_DIRECTION_SECTORS_DEG[0]
                                        )
                                    )
                                    % 360.0
                                )
                                / (
                                    WIND_FROM_DIRECTION_SECTORS_DEG[1]
                                    - WIND_FROM_DIRECTION_SECTORS_DEG[0]
                                )
                            ).astype(np.int16)
                            for sector in range(
                                WIND_FROM_DIRECTION_SECTORS_DEG.size
                            ):
                                direction_counts[:, sector, :, :] += (
                                    (~calm) & (sector_index == sector)
                                )
                            for threshold_index, threshold in enumerate(
                                WIND_SPEED_THRESHOLDS_M_S
                            ):
                                threshold_counts[:, threshold_index, :, :] += (
                                    distribution_speed >= threshold
                                )

                        mean_speed = sum_speed / n_samples
                        variance_speed = np.maximum(
                            sum_speed_squared / n_samples - mean_speed**2,
                            0.0,
                        )
                        fixed_values = {
                            "eastward_wind_mean": sum_u / n_samples,
                            "northward_wind_mean": sum_v / n_samples,
                            "wind_speed_mean": mean_speed,
                            "wind_speed_standard_deviation": np.sqrt(
                                variance_speed
                            ),
                            "air_density_mean": sum_rho / n_samples,
                            "wind_power_density_mean": sum_power / n_samples,
                            "resolved_wind_speed_max": max_speed,
                        }
                        surface_values = {
                            "eastward_wind_10m_mean": sum_u10 / n_samples,
                            "northward_wind_10m_mean": sum_v10 / n_samples,
                            "wind_speed_10m_mean": sum_speed10 / n_samples,
                            "resolved_wind_speed_10m_max": max_speed10,
                        }
                        for name, values in fixed_values.items():
                            fixed_outputs[name][
                                group_index, :, y_slice, :
                            ] = values.astype(np.float32)
                        for name, values in surface_values.items():
                            surface_outputs[name][
                                group_index, y_slice, :
                            ] = values.astype(np.float32)
                        for name, values in pbl_sums.items():
                            pbl_outputs[name][group_index, y_slice, :] = (
                                values / n_samples
                            ).astype(np.float32)
                        for name, values in pbl_maxima.items():
                            pbl_outputs[name][group_index, y_slice, :] = (
                                values.astype(np.float32)
                            )
                        if np.any(
                            np.sum(direction_counts, axis=1) + calm_counts
                            != n_samples
                        ):
                            raise ValueError(
                                "direction-sector and calm counts do not "
                                "sum to the interval sample count"
                            )
                        distribution_outputs[
                            "wind_from_direction_sector_count"
                        ][group_index, :, :, y_slice, :] = direction_counts
                        distribution_outputs[
                            "wind_speed_threshold_exceedance_count"
                        ][group_index, :, :, y_slice, :] = threshold_counts
                        distribution_outputs["calm_wind_count"][
                            group_index, :, y_slice, :
                        ] = calm_counts

                target.Conventions = "CF-1.10"
                target.title = "HICAR compact wind-climatology interval statistics"
                target.source = ", ".join(str(path) for path in input_paths)
                target.history = (
                    f"Created {datetime.now(timezone.utc).isoformat()} by "
                    "reduce_hicar_wind_climatology.py"
                )
                target.reduction_interval_seconds = interval_seconds
                target.source_sample_interval_seconds = sample_seconds
                target.interval_sample_convention = "start < sample_time <= end"
                target.time_coordinate_canonicalization = (
                    "Input timestamps within 1 second of the explicit chunk "
                    "boundary and regular cadence were snapped to exact bounds."
                    if canonicalized_time
                    else "none"
                )
                target.gust_policy = (
                    "No ICON VMAX is used. Resolved maxima are not 3-second gusts."
                )
                target.surface_pbl_statistics = (
                    "included" if has_pbl else "not present in source"
                )
                target.wind_distribution_statistics = (
                    "30-degree non-calm direction sectors, calm counts, and "
                    "speed-threshold exceedance counts at 10/50/75/100/125/"
                    "150/200 m AGL."
                )
                if static_identity is not None:
                    _set_static_domain_identity(target, static_identity)

        with netCDF4.Dataset(temporary_output) as product:
            for name in (
                *FIXED_HEIGHT_STATISTICS,
                *TEN_METRE_STATISTICS,
                *(PBL_STATISTICS if has_pbl else ()),
                "time_bounds",
                "sample_count",
            ):
                values = _as_float64(product.variables[name][:])
                if not np.all(np.isfinite(values)):
                    raise ValueError(f"published product contains non-finite {name}")
            if product.variables["time_bounds"].shape != (len(groups), 2):
                raise ValueError("published product has invalid time bounds")
            _validate_distribution_counts(
                product,
                y_block_size=y_block_size,
            )
        os.replace(temporary_output, output_path)

        report = {
            "status": "PASS",
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "inputs": [
                {
                    "path": str(path),
                    "size_bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                }
                for path in input_paths
            ],
            "output": str(output_path),
            "output_sha256": _sha256(output_path),
            "interval_seconds": interval_seconds,
            "interval_start": interval_start,
            "sample_interval_seconds": sample_seconds,
            "time_coordinate_canonicalized": canonicalized_time,
            "interval_count": len(groups),
            "samples_per_interval": [end - start for start, end in groups],
            "heights_agl_m": EXPECTED_HEIGHTS_M.tolist(),
            "surface_pbl_statistics": has_pbl,
            "static_domain": static_identity,
            "wind_distribution_statistics": True,
            "wind_distribution_heights_agl_m": (
                DISTRIBUTION_HEIGHTS_M.tolist()
            ),
            "wind_from_direction_sectors_degrees": (
                WIND_FROM_DIRECTION_SECTORS_DEG.tolist()
            ),
            "wind_speed_thresholds_m_s": (
                WIND_SPEED_THRESHOLDS_M_S.tolist()
            ),
            "calm_wind_speed_threshold_m_s": (
                CALM_WIND_SPEED_THRESHOLD_M_S
            ),
            "gust_policy": (
                "No ICON VMAX; resolved sample maximum is not a 3-second gust."
            ),
        }
        _write_json_atomic(report_path, report)
        if publish_ready:
            ready_path.touch()
            report_ready_path.touch()
        return report
    finally:
        if temporary_output.exists():
            temporary_output.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, nargs="+")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--interval-seconds", required=True, type=int)
    parser.add_argument(
        "--static-file",
        type=Path,
        help=(
            "Published HICAR static-domain NetCDF to validate and bind by "
            "SHA-256 as the product's immutable spatial companion."
        ),
    )
    parser.add_argument(
        "--start-time",
        help=(
            "Interval boundary timestamp. It may equal the first record or be "
            "one regular sample before a restart-continuation output."
        ),
    )
    parser.add_argument("--y-block-size", type=int, default=128)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--no-ready", action="store_true")
    args = parser.parse_args()
    try:
        report = reduce_product(
            args.input,
            args.output,
            interval_seconds=args.interval_seconds,
            interval_start=args.start_time,
            static_file=args.static_file,
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
