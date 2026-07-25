#!/usr/bin/env python3
"""Merge compact HICAR wind statistics into calendar-month or full-period bins."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Sequence

import netCDF4
import numpy as np

from reduce_hicar_wind_climatology import (
    DISTRIBUTION_HEIGHTS_M,
    DISTRIBUTION_VARIABLES,
    EXPECTED_HEIGHTS_M,
    FIXED_HEIGHT_STATISTICS,
    PBL_MAX_STATISTICS,
    PBL_MEAN_STATISTICS,
    PBL_STATISTICS,
    TEN_METRE_STATISTICS,
    WIND_FROM_DIRECTION_SECTORS_DEG,
    WIND_SPEED_THRESHOLDS_M_S,
    _set_static_domain_identity,
    _static_domain_identity,
    _validate_distribution_counts,
)


FIXED_MEANS = (
    "eastward_wind_mean",
    "northward_wind_mean",
    "wind_speed_mean",
    "air_density_mean",
    "wind_power_density_mean",
)
SURFACE_MEANS = (
    "eastward_wind_10m_mean",
    "northward_wind_10m_mean",
    "wind_speed_10m_mean",
)
FIXED_MAXIMA = ("resolved_wind_speed_max",)
SURFACE_MAXIMA = ("resolved_wind_speed_10m_max",)
SPEED_STD = "wind_speed_standard_deviation"
PBL_MEANS = tuple(PBL_MEAN_STATISTICS)
PBL_MAXIMA = tuple(PBL_MAX_STATISTICS)


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


def _validate_source(
    dataset: netCDF4.Dataset,
) -> tuple[int, int, int, int, bool, bool]:
    for dimension in ("time", "bounds", "height_agl", "lat_y", "lon_x"):
        if dimension not in dataset.dimensions:
            raise ValueError(f"input is missing dimension {dimension}")
    for variable in (
        "time",
        "time_bounds",
        "sample_count",
        "height_agl",
        "lat",
        "lon",
        *FIXED_HEIGHT_STATISTICS,
        *TEN_METRE_STATISTICS,
    ):
        if variable not in dataset.variables:
            raise ValueError(f"input is missing variable {variable}")
    nt = len(dataset.dimensions["time"])
    nh = len(dataset.dimensions["height_agl"])
    ny = len(dataset.dimensions["lat_y"])
    nx = len(dataset.dimensions["lon_x"])
    if nt < 1 or len(dataset.dimensions["bounds"]) != 2:
        raise ValueError("input has an invalid time axis")
    if not np.allclose(
        _as_float64(dataset["height_agl"][:]),
        EXPECTED_HEIGHTS_M,
        rtol=0.0,
        atol=1.0e-6,
    ):
        raise ValueError("input has an unexpected height_agl coordinate")
    if dataset["time_bounds"].shape != (nt, 2):
        raise ValueError("input time_bounds has an invalid shape")
    if dataset["sample_count"].shape != (nt,):
        raise ValueError("input sample_count has an invalid shape")
    for name in FIXED_HEIGHT_STATISTICS:
        if dataset[name].shape != (nt, nh, ny, nx):
            raise ValueError(f"{name} has an invalid shape")
    for name in TEN_METRE_STATISTICS:
        if dataset[name].shape != (nt, ny, nx):
            raise ValueError(f"{name} has an invalid shape")
    pbl_present = [name in dataset.variables for name in PBL_STATISTICS]
    if any(pbl_present) and not all(pbl_present):
        missing = [
            name
            for name, present in zip(PBL_STATISTICS, pbl_present)
            if not present
        ]
        raise ValueError(
            "input has an incomplete compact surface/PBL statistic set; missing "
            + ", ".join(missing)
        )
    has_pbl = all(pbl_present)
    if has_pbl:
        for name in PBL_STATISTICS:
            if dataset[name].dimensions != ("time", "lat_y", "lon_x"):
                raise ValueError(f"{name} has invalid dimensions")
            if dataset[name].shape != (nt, ny, nx):
                raise ValueError(f"{name} has an invalid shape")
    distribution_present = [
        name in dataset.variables
        for name in DISTRIBUTION_VARIABLES
    ]
    if any(distribution_present) and not all(distribution_present):
        missing = [
            name
            for name, present in zip(
                DISTRIBUTION_VARIABLES,
                distribution_present,
            )
            if not present
        ]
        raise ValueError(
            "input has an incomplete wind-distribution statistic set; missing "
            + ", ".join(missing)
        )
    has_distribution = all(distribution_present)
    if has_distribution:
        required_coordinates = (
            "wind_distribution_height_agl",
            "wind_from_direction_sector",
            "wind_from_direction_sector_bounds",
            "wind_speed_threshold",
        )
        for name in required_coordinates:
            if name not in dataset.variables:
                raise ValueError(
                    f"input wind-distribution statistics are missing {name}"
                )
        if not np.allclose(
            _as_float64(dataset["wind_distribution_height_agl"][:]),
            DISTRIBUTION_HEIGHTS_M,
            rtol=0.0,
            atol=1.0e-6,
        ):
            raise ValueError("input has unexpected wind distribution heights")
        if not np.allclose(
            _as_float64(dataset["wind_from_direction_sector"][:]),
            WIND_FROM_DIRECTION_SECTORS_DEG,
            rtol=0.0,
            atol=1.0e-6,
        ):
            raise ValueError("input has unexpected wind direction sectors")
        if not np.allclose(
            _as_float64(dataset["wind_speed_threshold"][:]),
            WIND_SPEED_THRESHOLDS_M_S,
            rtol=0.0,
            atol=1.0e-6,
        ):
            raise ValueError("input has unexpected wind speed thresholds")
        expected_distribution_shapes = {
            "wind_from_direction_sector_count": (
                nt,
                DISTRIBUTION_HEIGHTS_M.size,
                WIND_FROM_DIRECTION_SECTORS_DEG.size,
                ny,
                nx,
            ),
            "wind_speed_threshold_exceedance_count": (
                nt,
                DISTRIBUTION_HEIGHTS_M.size,
                WIND_SPEED_THRESHOLDS_M_S.size,
                ny,
                nx,
            ),
            "calm_wind_count": (
                nt,
                DISTRIBUTION_HEIGHTS_M.size,
                ny,
                nx,
            ),
        }
        for name, expected_shape in expected_distribution_shapes.items():
            if dataset[name].shape != expected_shape:
                raise ValueError(f"{name} has an invalid shape")
    return nt, nh, ny, nx, has_pbl, has_distribution


def _month_key(date: object) -> tuple[int, int]:
    return int(getattr(date, "year")), int(getattr(date, "month"))


def merge_products(
    input_paths: Sequence[Path],
    output_path: Path,
    *,
    group_by: str = "month",
    y_block_size: int = 128,
    report_path: Path | None = None,
    publish_ready: bool = True,
) -> dict[str, object]:
    """Merge interval products using sufficient-statistic identities."""
    sources_paths = tuple(Path(path).resolve() for path in input_paths)
    if not sources_paths:
        raise ValueError("at least one input file is required")
    if group_by not in {"month", "all"}:
        raise ValueError("group_by must be month or all")
    if y_block_size <= 0:
        raise ValueError("y_block_size must be positive")
    for path in sources_paths:
        if not path.is_file() or not Path(f"{path}.ready").is_file():
            raise ValueError(f"input publication is incomplete: {path}")

    output_path = output_path.resolve()
    if output_path in sources_paths:
        raise ValueError("output must differ from every input")
    report_path = (
        report_path.resolve()
        if report_path is not None
        else output_path.with_suffix(output_path.suffix + ".json")
    )
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
                for path in sources_paths
            ]
            reference = sources[0]
            (
                _,
                nh,
                ny,
                nx,
                has_pbl,
                has_distribution,
            ) = _validate_source(reference)
            time_units = reference["time"].units
            time_calendar = getattr(reference["time"], "calendar", "standard")
            sample_interval = float(reference.source_sample_interval_seconds)
            static_identity = _static_domain_identity(reference)

            records: list[tuple[int, int, float, float, int, tuple[int, int] | str]] = []
            previous_end_seconds: float | None = None
            for source_index, source in enumerate(sources):
                (
                    nt,
                    current_nh,
                    current_ny,
                    current_nx,
                    current_has_pbl,
                    current_has_distribution,
                ) = _validate_source(source)
                if (current_nh, current_ny, current_nx) != (nh, ny, nx):
                    raise ValueError(f"input grid differs in {sources_paths[source_index]}")
                if current_has_pbl != has_pbl:
                    raise ValueError(
                        "input files disagree on compact surface/PBL statistics"
                    )
                if current_has_distribution != has_distribution:
                    raise ValueError(
                        "input files disagree on wind-distribution statistics"
                    )
                current_static_identity = _static_domain_identity(source)
                if (current_static_identity is None) != (static_identity is None):
                    raise ValueError(
                        "input files disagree on static-domain companion presence"
                    )
                if static_identity is not None and current_static_identity is not None:
                    for key in (
                        "static_domain_sha256",
                        "static_domain_size_bytes",
                        "static_domain_grid_ny",
                        "static_domain_grid_nx",
                        "static_domain_dx_m",
                        "static_domain_dy_m",
                        "static_domain_projection",
                    ):
                        if current_static_identity[key] != static_identity[key]:
                            raise ValueError(
                                "input files refer to different static-domain "
                                f"companions ({key})"
                            )
                current_time = source["time"]
                if (
                    current_time.units != time_units
                    or getattr(current_time, "calendar", "standard") != time_calendar
                ):
                    raise ValueError("input time metadata differs")
                if not np.isclose(
                    float(source.source_sample_interval_seconds),
                    sample_interval,
                    rtol=0.0,
                    atol=1.0e-6,
                ):
                    raise ValueError("source sample intervals differ")
                coordinate_names = ["height_agl", "lat", "lon"]
                if has_distribution:
                    coordinate_names.extend(
                        (
                            "wind_distribution_height_agl",
                            "wind_from_direction_sector",
                            "wind_from_direction_sector_bounds",
                            "wind_speed_threshold",
                        )
                    )
                for coordinate_name in coordinate_names:
                    candidate = _as_float64(source[coordinate_name][:])
                    expected = _as_float64(reference[coordinate_name][:])
                    if not np.allclose(candidate, expected, rtol=0.0, atol=1.0e-6):
                        raise ValueError(f"{coordinate_name} coordinates differ")

                bounds = _as_float64(source["time_bounds"][:])
                counts = np.asarray(source["sample_count"][:], dtype=np.int64)
                if np.any(counts <= 0):
                    raise ValueError("sample_count must be positive")
                bounds_dates = netCDF4.num2date(
                    bounds,
                    time_units,
                    calendar=time_calendar,
                )
                bounds_seconds = _as_float64(
                    netCDF4.date2num(
                        bounds_dates,
                        "seconds since 1970-01-01 00:00:00",
                        calendar=time_calendar,
                    )
                )
                for time_index in range(nt):
                    start, end = bounds[time_index]
                    start_seconds, end_seconds = bounds_seconds[time_index]
                    if end_seconds <= start_seconds:
                        raise ValueError("input contains non-positive time bounds")
                    if (
                        previous_end_seconds is not None
                        and not np.isclose(
                            start_seconds,
                            previous_end_seconds,
                            rtol=0.0,
                            atol=1.0e-3,
                        )
                    ):
                        raise ValueError("input intervals are not contiguous and ordered")
                    previous_end_seconds = float(end_seconds)
                    key: tuple[int, int] | str
                    if group_by == "month":
                        key = _month_key(bounds_dates[time_index, 0])
                        end_key = _month_key(bounds_dates[time_index, 1])
                        end_date = bounds_dates[time_index, 1]
                        if end_key != key and not (
                            int(getattr(end_date, "day")) == 1
                            and int(getattr(end_date, "hour")) == 0
                            and int(getattr(end_date, "minute")) == 0
                            and int(getattr(end_date, "second")) == 0
                        ):
                            raise ValueError("an input interval crosses a calendar month")
                    else:
                        key = "all"
                    records.append(
                        (
                            source_index,
                            time_index,
                            float(start),
                            float(end),
                            int(counts[time_index]),
                            key,
                        )
                    )

            groups: list[list[tuple[int, int, float, float, int, object]]] = []
            for record in records:
                if not groups or record[-1] != groups[-1][0][-1]:
                    groups.append([record])
                else:
                    groups[-1].append(record)

            with netCDF4.Dataset(temporary_output, "w", format="NETCDF4") as target:
                target.createDimension("time", len(groups))
                target.createDimension("bounds", 2)
                target.createDimension("height_agl", nh)
                if has_distribution:
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
                _copy_attributes(reference["time"], time)
                time.bounds = "time_bounds"
                bounds_out = target.createVariable(
                    "time_bounds",
                    "f8",
                    ("time", "bounds"),
                )
                _copy_attributes(reference["time_bounds"], bounds_out)
                time[:] = [group[-1][3] for group in groups]
                bounds_out[:] = [
                    (group[0][2], group[-1][3])
                    for group in groups
                ]

                for coordinate_name, dimensions in (
                    ("height_agl", ("height_agl",)),
                    ("height_10m", ()),
                    ("lat", ("lat_y", "lon_x")),
                    ("lon", ("lat_y", "lon_x")),
                ):
                    source_variable = reference[coordinate_name]
                    variable = target.createVariable(
                        coordinate_name,
                        source_variable.dtype,
                        dimensions,
                        zlib=bool(dimensions),
                        complevel=1,
                        shuffle=bool(dimensions),
                    )
                    _copy_attributes(source_variable, variable)
                    variable[...] = source_variable[...]
                if has_distribution:
                    for coordinate_name, dimensions in (
                        (
                            "wind_distribution_height_agl",
                            ("wind_distribution_height_agl",),
                        ),
                        (
                            "wind_from_direction_sector",
                            ("wind_from_direction_sector",),
                        ),
                        (
                            "wind_from_direction_sector_bounds",
                            ("wind_from_direction_sector", "bounds"),
                        ),
                        (
                            "wind_speed_threshold",
                            ("wind_speed_threshold",),
                        ),
                    ):
                        source_variable = reference[coordinate_name]
                        variable = target.createVariable(
                            coordinate_name,
                            source_variable.dtype,
                            dimensions,
                        )
                        _copy_attributes(source_variable, variable)
                        variable[...] = source_variable[...]

                outputs: dict[str, netCDF4.Variable] = {}
                for name in (
                    *FIXED_HEIGHT_STATISTICS,
                    *TEN_METRE_STATISTICS,
                    *(PBL_STATISTICS if has_pbl else ()),
                    *(DISTRIBUTION_VARIABLES if has_distribution else ()),
                ):
                    source_variable = reference[name]
                    variable = target.createVariable(
                        name,
                        (
                            source_variable.dtype
                            if name in DISTRIBUTION_VARIABLES
                            else "f4"
                        ),
                        source_variable.dimensions,
                        zlib=True,
                        complevel=1,
                        shuffle=True,
                        **(
                            {}
                            if name in DISTRIBUTION_VARIABLES
                            else {"fill_value": np.float32(9.96921e36)}
                        ),
                    )
                    _copy_attributes(source_variable, variable)
                    outputs[name] = variable
                sample_count = target.createVariable("sample_count", "i8", ("time",))
                _copy_attributes(reference["sample_count"], sample_count)
                interval_count = target.createVariable(
                    "contributing_interval_count",
                    "i4",
                    ("time",),
                )
                interval_count.long_name = "number of compact intervals merged"
                interval_count.units = "1"

                for group_index, group in enumerate(groups):
                    total_count = sum(record[4] for record in group)
                    sample_count[group_index] = total_count
                    interval_count[group_index] = len(group)
                    for y_start in range(0, ny, y_block_size):
                        y_stop = min(y_start + y_block_size, ny)
                        y_slice = slice(y_start, y_stop)
                        fixed_shape = (nh, y_stop - y_start, nx)
                        surface_shape = (y_stop - y_start, nx)
                        fixed_sums = {
                            name: np.zeros(fixed_shape, dtype=np.float64)
                            for name in FIXED_MEANS
                        }
                        surface_sums = {
                            name: np.zeros(surface_shape, dtype=np.float64)
                            for name in SURFACE_MEANS
                        }
                        speed_second_moment = np.zeros(
                            fixed_shape,
                            dtype=np.float64,
                        )
                        fixed_maxima = {
                            name: np.full(fixed_shape, -np.inf, dtype=np.float64)
                            for name in FIXED_MAXIMA
                        }
                        surface_maxima = {
                            name: np.full(surface_shape, -np.inf, dtype=np.float64)
                            for name in SURFACE_MAXIMA
                        }
                        pbl_sums = {
                            name: np.zeros(surface_shape, dtype=np.float64)
                            for name in PBL_MEANS
                        } if has_pbl else {}
                        pbl_maxima = {
                            name: np.full(
                                surface_shape,
                                -np.inf,
                                dtype=np.float64,
                            )
                            for name in PBL_MAXIMA
                        } if has_pbl else {}
                        distribution_sums = (
                            {
                                "wind_from_direction_sector_count": np.zeros(
                                    (
                                        DISTRIBUTION_HEIGHTS_M.size,
                                        WIND_FROM_DIRECTION_SECTORS_DEG.size,
                                        y_stop - y_start,
                                        nx,
                                    ),
                                    dtype=np.int64,
                                ),
                                "wind_speed_threshold_exceedance_count": np.zeros(
                                    (
                                        DISTRIBUTION_HEIGHTS_M.size,
                                        WIND_SPEED_THRESHOLDS_M_S.size,
                                        y_stop - y_start,
                                        nx,
                                    ),
                                    dtype=np.int64,
                                ),
                                "calm_wind_count": np.zeros(
                                    (
                                        DISTRIBUTION_HEIGHTS_M.size,
                                        y_stop - y_start,
                                        nx,
                                    ),
                                    dtype=np.int64,
                                ),
                            }
                            if has_distribution
                            else {}
                        )

                        for source_index, time_index, _, _, count, _ in group:
                            source = sources[source_index]
                            for name in FIXED_MEANS:
                                values = _as_float64(
                                    source[name][time_index, :, y_slice, :]
                                )
                                fixed_sums[name] += count * values
                            for name in SURFACE_MEANS:
                                values = _as_float64(
                                    source[name][time_index, y_slice, :]
                                )
                                surface_sums[name] += count * values
                            speed_mean = _as_float64(
                                source["wind_speed_mean"][
                                    time_index, :, y_slice, :
                                ]
                            )
                            speed_std = _as_float64(
                                source[SPEED_STD][time_index, :, y_slice, :]
                            )
                            speed_second_moment += count * (
                                speed_std**2 + speed_mean**2
                            )
                            for name in FIXED_MAXIMA:
                                np.maximum(
                                    fixed_maxima[name],
                                    _as_float64(
                                        source[name][
                                            time_index, :, y_slice, :
                                        ]
                                    ),
                                    out=fixed_maxima[name],
                                )
                            for name in SURFACE_MAXIMA:
                                np.maximum(
                                    surface_maxima[name],
                                    _as_float64(
                                        source[name][
                                            time_index, y_slice, :
                                        ]
                                    ),
                                    out=surface_maxima[name],
                                )
                            for name in PBL_MEANS if has_pbl else ():
                                pbl_sums[name] += count * _as_float64(
                                    source[name][time_index, y_slice, :]
                                )
                            for name in PBL_MAXIMA if has_pbl else ():
                                np.maximum(
                                    pbl_maxima[name],
                                    _as_float64(
                                        source[name][time_index, y_slice, :]
                                    ),
                                    out=pbl_maxima[name],
                                )
                            for name in (
                                DISTRIBUTION_VARIABLES
                                if has_distribution
                                else ()
                            ):
                                distribution_sums[name] += np.asarray(
                                    source[name][time_index, ..., y_slice, :],
                                    dtype=np.int64,
                                )

                        for name, values in fixed_sums.items():
                            outputs[name][group_index, :, y_slice, :] = (
                                values / total_count
                            ).astype(np.float32)
                        for name, values in surface_sums.items():
                            outputs[name][group_index, y_slice, :] = (
                                values / total_count
                            ).astype(np.float32)
                        merged_speed_mean = fixed_sums["wind_speed_mean"] / total_count
                        merged_variance = np.maximum(
                            speed_second_moment / total_count - merged_speed_mean**2,
                            0.0,
                        )
                        outputs[SPEED_STD][group_index, :, y_slice, :] = np.sqrt(
                            merged_variance
                        ).astype(np.float32)
                        for name, values in fixed_maxima.items():
                            outputs[name][group_index, :, y_slice, :] = values.astype(
                                np.float32
                            )
                        for name, values in surface_maxima.items():
                            outputs[name][group_index, y_slice, :] = values.astype(
                                np.float32
                            )
                        for name, values in pbl_sums.items():
                            outputs[name][group_index, y_slice, :] = (
                                values / total_count
                            ).astype(np.float32)
                        for name, values in pbl_maxima.items():
                            outputs[name][group_index, y_slice, :] = values.astype(
                                np.float32
                            )
                        for name, values in distribution_sums.items():
                            if np.any(values > np.iinfo(np.int32).max):
                                raise ValueError(
                                    f"merged {name} exceeds signed 32-bit count range"
                                )
                            outputs[name][
                                group_index, ..., y_slice, :
                            ] = values.astype(np.int32)

                target.Conventions = "CF-1.10"
                target.title = "Merged HICAR compact wind-climatology statistics"
                target.source = ", ".join(str(path) for path in sources_paths)
                target.history = (
                    f"Created {datetime.now(timezone.utc).isoformat()} by "
                    "merge_hicar_wind_statistics.py"
                )
                target.aggregation = (
                    "Exact weighted merge of counts, means, population "
                    "variance, and block maxima."
                )
                target.calendar_grouping = group_by
                target.source_sample_interval_seconds = sample_interval
                target.interval_sample_convention = "start < sample_time <= end"
                target.gust_policy = (
                    "No ICON VMAX is used. Resolved maxima are not 3-second gusts."
                )
                target.surface_pbl_statistics = (
                    "included" if has_pbl else "not present in source"
                )
                target.wind_distribution_statistics = (
                    "included"
                    if has_distribution
                    else "not present in source"
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
                if not np.all(np.isfinite(_as_float64(product[name][:]))):
                    raise ValueError(f"merged product contains non-finite {name}")
            if has_distribution:
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
                for path in sources_paths
            ],
            "output": str(output_path),
            "output_sha256": _sha256(output_path),
            "group_by": group_by,
            "group_count": len(groups),
            "input_interval_count": len(records),
            "samples_per_group": [
                sum(record[4] for record in group)
                for group in groups
            ],
            "heights_agl_m": EXPECTED_HEIGHTS_M.tolist(),
            "surface_pbl_statistics": has_pbl,
            "wind_distribution_statistics": has_distribution,
            "static_domain": static_identity,
            "gust_policy": (
                "No ICON VMAX; merged resolved maxima are not 3-second gusts."
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
    parser.add_argument("--group-by", choices=("month", "all"), default="month")
    parser.add_argument("--y-block-size", type=int, default=128)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--no-ready", action="store_true")
    args = parser.parse_args()
    try:
        report = merge_products(
            args.input,
            args.output,
            group_by=args.group_by,
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
