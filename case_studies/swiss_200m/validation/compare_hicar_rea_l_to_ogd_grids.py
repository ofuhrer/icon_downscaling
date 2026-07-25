#!/usr/bin/env python3
"""Compare event precipitation and radiation with public MeteoSwiss grids."""

from __future__ import annotations

import argparse
import json
import math
import os
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

import netCDF4
import numpy as np


@dataclass
class Statistics:
    count: int = 0
    sum_error: float = 0.0
    sum_abs_error: float = 0.0
    sum_squared_error: float = 0.0
    sum_model: float = 0.0
    sum_reference: float = 0.0
    sum_model_squared: float = 0.0
    sum_reference_squared: float = 0.0
    sum_product: float = 0.0

    def add(self, model: np.ndarray, reference: np.ndarray, mask: np.ndarray) -> None:
        valid = mask & np.isfinite(model) & np.isfinite(reference)
        if not np.any(valid):
            return
        model_values = np.asarray(model[valid], dtype=np.float64)
        reference_values = np.asarray(reference[valid], dtype=np.float64)
        error = model_values - reference_values
        self.count += len(error)
        self.sum_error += float(np.sum(error))
        self.sum_abs_error += float(np.sum(np.abs(error)))
        self.sum_squared_error += float(np.sum(error * error))
        self.sum_model += float(np.sum(model_values))
        self.sum_reference += float(np.sum(reference_values))
        self.sum_model_squared += float(np.sum(model_values * model_values))
        self.sum_reference_squared += float(np.sum(reference_values * reference_values))
        self.sum_product += float(np.sum(model_values * reference_values))

    def result(self) -> dict:
        if not self.count:
            return {"count": 0}
        count = float(self.count)
        covariance = self.sum_product - self.sum_model * self.sum_reference / count
        model_variance = (
            self.sum_model_squared - self.sum_model * self.sum_model / count
        )
        reference_variance = (
            self.sum_reference_squared - self.sum_reference * self.sum_reference / count
        )
        denominator = math.sqrt(max(model_variance * reference_variance, 0.0))
        return {
            "count": self.count,
            "model_mean": self.sum_model / count,
            "reference_mean": self.sum_reference / count,
            "bias": self.sum_error / count,
            "mean_absolute_error": self.sum_abs_error / count,
            "root_mean_squared_error": math.sqrt(self.sum_squared_error / count),
            "correlation": covariance / denominator if denominator > 0.0 else None,
        }


def canonical_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    rounded = value.replace(microsecond=0)
    return rounded if abs((value - rounded).total_seconds()) <= 1.0 else value


def climatological_season(value: datetime) -> str:
    if value.month in (12, 1, 2):
        return "DJF"
    if value.month in (3, 4, 5):
        return "MAM"
    if value.month in (6, 7, 8):
        return "JJA"
    return "SON"


def decoded_times(dataset: netCDF4.Dataset) -> list[datetime]:
    variable = dataset.variables["time"]
    values = netCDF4.num2date(
        variable[:],
        units=variable.units,
        calendar=getattr(variable, "calendar", "standard"),
        only_use_cftime_datetimes=False,
        only_use_python_datetimes=True,
    )
    return [canonical_time(value) for value in values]


def read_array(
    dataset: netCDF4.Dataset, name: str, index: int | None = None
) -> np.ndarray:
    variable = dataset.variables[name]
    values = variable[:] if index is None else variable[index]
    values = np.squeeze(np.ma.asarray(values))
    if np.ma.count_masked(values):
        values = values.filled(np.nan)
    return np.asarray(values, dtype=np.float64)


def polynomial_features(latitude_offset: np.ndarray, longitude_offset: np.ndarray):
    a = np.asarray(latitude_offset).reshape(-1)
    b = np.asarray(longitude_offset).reshape(-1)
    return np.column_stack(
        (
            np.ones(a.size),
            a,
            b,
            a * a,
            a * b,
            b * b,
            a**3,
            a * a * b,
            a * b * b,
            b**3,
        )
    )


def fit_local_coordinates(
    latitude: np.ndarray,
    longitude: np.ndarray,
    x: np.ndarray,
    y: np.ndarray,
) -> dict:
    latitude_origin = float(np.mean(latitude))
    longitude_origin = float(np.mean(longitude))
    sample_y = np.unique(
        np.linspace(
            0,
            latitude.shape[0] - 1,
            min(15, latitude.shape[0]),
            dtype=np.int32,
        )
    )
    sample_x = np.unique(
        np.linspace(
            0,
            latitude.shape[1] - 1,
            min(15, latitude.shape[1]),
            dtype=np.int32,
        )
    )
    y_grid, x_grid = np.meshgrid(sample_y, sample_x, indexing="ij")
    matrix = polynomial_features(
        latitude[y_grid, x_grid] - latitude_origin,
        longitude[y_grid, x_grid] - longitude_origin,
    )
    coefficient_x = np.linalg.lstsq(matrix, x[x_grid.reshape(-1)], rcond=None)[0]
    coefficient_y = np.linalg.lstsq(matrix, y[y_grid.reshape(-1)], rcond=None)[0]

    verify_y = np.arange(17, latitude.shape[0], 37)
    verify_x = np.arange(19, latitude.shape[1], 37)
    verify_y_grid, verify_x_grid = np.meshgrid(verify_y, verify_x, indexing="ij")
    verify = polynomial_features(
        latitude[verify_y_grid, verify_x_grid] - latitude_origin,
        longitude[verify_y_grid, verify_x_grid] - longitude_origin,
    )
    error = np.hypot(
        verify @ coefficient_x - x[verify_x_grid.reshape(-1)],
        verify @ coefficient_y - y[verify_y_grid.reshape(-1)],
    )
    maximum_error = float(np.max(error))
    if maximum_error > 5.0:
        raise ValueError(
            f"lat/lon-to-local coordinate fit error is {maximum_error:.3f} m"
        )
    return {
        "latitude_origin": latitude_origin,
        "longitude_origin": longitude_origin,
        "coefficient_x": coefficient_x,
        "coefficient_y": coefficient_y,
        "verification_count": len(error),
        "maximum_verification_error_m": maximum_error,
        "p99_verification_error_m": float(np.percentile(error, 99.0)),
    }


def local_coordinates(
    transform: dict, latitude: np.ndarray, longitude: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    shape = np.asarray(latitude).shape
    matrix = polynomial_features(
        np.asarray(latitude) - transform["latitude_origin"],
        np.asarray(longitude) - transform["longitude_origin"],
    )
    return (
        (matrix @ transform["coefficient_x"]).reshape(shape),
        (matrix @ transform["coefficient_y"]).reshape(shape),
    )


def grid_setup(
    transform: dict,
    target_latitude: np.ndarray,
    target_longitude: np.ndarray,
    x: np.ndarray,
    y: np.ndarray,
    boundary_width_m: float,
) -> dict:
    target_x, target_y = local_coordinates(transform, target_latitude, target_longitude)
    dx = float(np.median(np.diff(x)))
    dy = float(np.median(np.diff(y)))
    x_index = np.rint((target_x.reshape(-1) - x[0]) / dx).astype(np.int32)
    y_index = np.rint((target_y.reshape(-1) - y[0]) / dy).astype(np.int32)
    boundary_x = int(math.ceil(boundary_width_m / abs(dx)))
    boundary_y = int(math.ceil(boundary_width_m / abs(dy)))
    inside = (x_index >= 0) & (x_index < len(x)) & (y_index >= 0) & (y_index < len(y))
    if not np.any(inside):
        raise ValueError("reference grid does not overlap the HICAR domain")
    interior = (
        (x_index >= boundary_x)
        & (x_index < len(x) - boundary_x)
        & (y_index >= boundary_y)
        & (y_index < len(y) - boundary_y)
    )
    return {
        "target_shape": np.asarray(target_latitude).shape,
        "x_index": x_index,
        "y_index": y_index,
        "inside": inside,
        "interior": interior,
        "dx_m": dx,
        "dy_m": dy,
        "maximum_center_offset_m": float(
            np.max(
                np.hypot(
                    target_x.reshape(-1)[inside] - x[x_index[inside]],
                    target_y.reshape(-1)[inside] - y[y_index[inside]],
                )
            )
        ),
    }


def box_mean(values: np.ndarray, setup: dict, half_width_cells: int) -> np.ndarray:
    y_index = setup["y_index"]
    x_index = setup["x_index"]
    total = np.zeros(len(y_index), dtype=np.float64)
    count = np.zeros(len(y_index), dtype=np.int16)
    for y_offset in range(-half_width_cells, half_width_cells + 1):
        sample_y = y_index + y_offset
        for x_offset in range(-half_width_cells, half_width_cells + 1):
            sample_x = x_index + x_offset
            valid = (
                setup["inside"]
                & (sample_y >= 0)
                & (sample_y < values.shape[0])
                & (sample_x >= 0)
                & (sample_x < values.shape[1])
            )
            sampled = np.full(len(y_index), np.nan)
            sampled[valid] = values[sample_y[valid], sample_x[valid]]
            finite = np.isfinite(sampled)
            total[finite] += sampled[finite]
            count[finite] += 1
    result = np.full(len(y_index), np.nan)
    result[count > 0] = total[count > 0] / count[count > 0]
    return result


def regular_bilinear_setup(
    latitude: np.ndarray,
    longitude: np.ndarray,
    target_latitude: np.ndarray,
    target_longitude: np.ndarray,
) -> tuple[np.ndarray, ...]:
    target_latitude = np.asarray(target_latitude).reshape(-1)
    target_longitude = np.asarray(target_longitude).reshape(-1)
    reverse_y = latitude[0] > latitude[-1]
    reverse_x = longitude[0] > longitude[-1]
    ordered_latitude = latitude[::-1] if reverse_y else latitude
    ordered_longitude = longitude[::-1] if reverse_x else longitude
    outside = (
        (target_latitude < ordered_latitude[0])
        | (target_latitude > ordered_latitude[-1])
        | (target_longitude < ordered_longitude[0])
        | (target_longitude > ordered_longitude[-1])
    )
    y1 = np.clip(
        np.searchsorted(ordered_latitude, target_latitude, side="right"),
        1,
        len(ordered_latitude) - 1,
    )
    x1 = np.clip(
        np.searchsorted(ordered_longitude, target_longitude, side="right"),
        1,
        len(ordered_longitude) - 1,
    )
    y0 = y1 - 1
    x0 = x1 - 1
    wy = (target_latitude - ordered_latitude[y0]) / (
        ordered_latitude[y1] - ordered_latitude[y0]
    )
    wx = (target_longitude - ordered_longitude[x0]) / (
        ordered_longitude[x1] - ordered_longitude[x0]
    )
    return y0, y1, x0, x1, wy, wx, reverse_y, reverse_x, outside


def regular_bilinear(values: np.ndarray, setup: tuple[np.ndarray, ...]):
    y0, y1, x0, x1, wy, wx, reverse_y, reverse_x, outside = setup
    if reverse_y:
        values = values[::-1, :]
    if reverse_x:
        values = values[:, ::-1]
    result = (
        values[y0, x0] * (1.0 - wy) * (1.0 - wx)
        + values[y1, x0] * wy * (1.0 - wx)
        + values[y0, x1] * (1.0 - wy) * wx
        + values[y1, x1] * wy * wx
    )
    result[outside] = np.nan
    return result


def elevation_classes(terrain: np.ndarray, setup: dict) -> dict[str, np.ndarray]:
    classes = {
        "all": setup["inside"],
        "interior_ge_10km": setup["interior"],
        "elevation_lt_500m": terrain < 500.0,
        "elevation_500_1000m": (terrain >= 500.0) & (terrain < 1000.0),
        "elevation_1000_1500m": (terrain >= 1000.0) & (terrain < 1500.0),
        "elevation_1500_2000m": (terrain >= 1500.0) & (terrain < 2000.0),
        "elevation_ge_2000m": terrain >= 2000.0,
        "elevation_2000_3000m": (terrain >= 2000.0) & (terrain < 3000.0),
        "elevation_ge_3000m": terrain >= 3000.0,
    }
    return {name: values & setup["inside"] for name, values in classes.items()}


def find_assets(
    manifest: dict,
    product: str,
    month: int | None = None,
) -> list[Path]:
    matches = [
        asset
        for asset in manifest["assets"]
        if asset["product"] == product
        and (month is None or asset.get("month") == month)
    ]
    matches.sort(
        key=lambda asset: (
            int(asset.get("year", manifest.get("year") or 0)),
            int(asset.get("month", 0)),
            str(asset["path"]),
        )
    )
    paths = [Path(asset["path"]) for asset in matches]
    if not paths:
        raise ValueError(
            f"expected at least one {product} asset for month={month}"
        )
    return paths


def find_asset(manifest: dict, product: str, month: int | None = None) -> Path:
    matches = find_assets(manifest, product, month)
    if len(matches) != 1:
        raise ValueError(
            f"expected one {product} asset for month={month}, found {matches}"
        )
    return matches[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--static-file", type=Path, required=True)
    parser.add_argument("--output-file", type=Path, action="append", required=True)
    parser.add_argument("--reference-list", type=Path, required=True)
    parser.add_argument("--ogd-manifest", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--boundary-width-m", type=float, default=10_000.0)
    parser.add_argument("--minimum-pairs", type=int, default=1000)
    args = parser.parse_args()

    failures: list[str] = []
    manifest = json.loads(args.ogd_manifest.read_text())
    if manifest.get("status") != "PASS":
        failures.append("OGD reference manifest is not PASS")

    with netCDF4.Dataset(args.static_file) as static:
        latitude = read_array(static, "lat")
        longitude = read_array(static, "lon")
        x = read_array(static, "x")
        y = read_array(static, "y")
        terrain = read_array(static, "topo")
    transform = fit_local_coordinates(latitude, longitude, x, y)

    with ExitStack() as stack:
        outputs = [
            stack.enter_context(netCDF4.Dataset(path)) for path in args.output_file
        ]
        model_records = {}
        for dataset in outputs:
            for index, valid in enumerate(decoded_times(dataset)):
                if valid in model_records:
                    failures.append(f"duplicate HICAR time {valid.isoformat()}")
                model_records[valid] = (dataset, index)
        ordered_times = sorted(model_records)
        if not ordered_times:
            raise SystemExit("no HICAR output records")
        rhires_datasets = [
            stack.enter_context(netCDF4.Dataset(path))
            for path in find_assets(manifest, "rhiresd")
        ]
        tabsd_datasets = [
            stack.enter_context(netCDF4.Dataset(path))
            for path in find_assets(manifest, "tabsd")
        ]
        sis_datasets = [
            stack.enter_context(netCDF4.Dataset(path))
            for path in find_assets(manifest, "sis")
        ]
        sis_no_horizon_datasets = [
            stack.enter_context(netCDF4.Dataset(path))
            for path in find_assets(manifest, "sis-no-horizon")
        ]
        rhires = rhires_datasets[0]
        tabsd = tabsd_datasets[0]
        sis = sis_datasets[0]
        sis_no_horizon = sis_no_horizon_datasets[0]

        rhires_latitude = read_array(rhires, "lat")
        rhires_longitude = read_array(rhires, "lon")
        for dataset in rhires_datasets[1:]:
            if not (
                np.array_equal(read_array(dataset, "lat"), rhires_latitude)
                and np.array_equal(read_array(dataset, "lon"), rhires_longitude)
            ):
                raise ValueError("RhiresD grids differ between annual assets")
        rhires_setup = grid_setup(
            transform,
            rhires_latitude,
            rhires_longitude,
            x,
            y,
            args.boundary_width_m,
        )
        rhires_terrain = box_mean(terrain, rhires_setup, 2)
        rhires_classes = elevation_classes(rhires_terrain, rhires_setup)
        precipitation_statistics = {
            source: {name: Statistics() for name in rhires_classes}
            for source in ("hicar", "rea_l")
        }
        precipitation_seasonal_statistics = {
            season: {
                source: {name: Statistics() for name in rhires_classes}
                for source in ("hicar", "rea_l")
            }
            for season in ("DJF", "MAM", "JJA", "SON")
        }

        rhires_times = {}
        for dataset in rhires_datasets:
            for index, valid in enumerate(decoded_times(dataset)):
                if valid in rhires_times:
                    failures.append(f"duplicate RhiresD time {valid.isoformat()}")
                rhires_times[valid] = (dataset, index)
        reference_paths = [
            Path(line.strip().strip('"'))
            for line in args.reference_list.read_text().splitlines()
            if line.strip()
        ]
        reference_records = {}
        reference_setup = None
        for path in reference_paths:
            dataset = stack.enter_context(netCDF4.Dataset(path))
            valid = decoded_times(dataset)[0]
            reference_records[valid] = dataset
            if reference_setup is None:
                reference_setup = regular_bilinear_setup(
                    read_array(dataset, "latitude"),
                    read_array(dataset, "longitude"),
                    rhires_latitude,
                    rhires_longitude,
                )
        assert reference_setup is not None

        matched_daily_windows = []
        start_day = ordered_times[0].replace(hour=0, minute=0, second=0)
        end_time = ordered_times[-1]
        day = start_day
        while day <= end_time:
            window_start = day.replace(hour=6)
            window_end = window_start + timedelta(days=1)
            if (
                window_start in model_records
                and window_end in model_records
                and day in rhires_times
            ):
                start_dataset, start_index = model_records[window_start]
                end_dataset, end_index = model_records[window_end]
                hicar_amount = box_mean(
                    read_array(end_dataset, "precipitation", end_index)
                    - read_array(start_dataset, "precipitation", start_index),
                    rhires_setup,
                    2,
                )
                rea_l_amount = np.zeros(hicar_amount.shape, dtype=np.float64)
                reference_complete = True
                valid = window_start + timedelta(hours=3)
                while valid <= window_end:
                    dataset = reference_records.get(valid)
                    if dataset is None:
                        reference_complete = False
                        break
                    rea_l_amount += regular_bilinear(
                        read_array(dataset, "precipitation_interval_ref", 0),
                        reference_setup,
                    )
                    valid += timedelta(hours=3)
                if not reference_complete:
                    failures.append(
                        f"REA-L intervals do not cover {window_start}..{window_end}"
                    )
                    day += timedelta(days=1)
                    continue
                rhires_dataset, rhires_index = rhires_times[day]
                observed = read_array(
                    rhires_dataset,
                    "RhiresD",
                    rhires_index,
                ).reshape(-1)
                season = climatological_season(day)
                for class_name, mask in rhires_classes.items():
                    precipitation_statistics["hicar"][class_name].add(
                        hicar_amount, observed, mask
                    )
                    precipitation_statistics["rea_l"][class_name].add(
                        rea_l_amount, observed, mask
                    )
                    precipitation_seasonal_statistics[season]["hicar"][
                        class_name
                    ].add(hicar_amount, observed, mask)
                    precipitation_seasonal_statistics[season]["rea_l"][
                        class_name
                    ].add(rea_l_amount, observed, mask)
                matched_daily_windows.append(
                    {
                        "rhires_day": day.date().isoformat(),
                        "window_start": window_start.isoformat(),
                        "window_end": window_end.isoformat(),
                    }
                )
            day += timedelta(days=1)

        tabsd_latitude = read_array(tabsd, "lat")
        tabsd_longitude = read_array(tabsd, "lon")
        for dataset in tabsd_datasets[1:]:
            if not (
                np.array_equal(read_array(dataset, "lat"), tabsd_latitude)
                and np.array_equal(read_array(dataset, "lon"), tabsd_longitude)
            ):
                raise ValueError("TabsD grids differ between annual assets")
        tabsd_setup = grid_setup(
            transform,
            tabsd_latitude,
            tabsd_longitude,
            x,
            y,
            args.boundary_width_m,
        )
        tabsd_terrain = box_mean(terrain, tabsd_setup, 2)
        tabsd_classes = elevation_classes(tabsd_terrain, tabsd_setup)
        temperature_statistics = {
            source: {name: Statistics() for name in tabsd_classes}
            for source in ("hicar", "rea_l")
        }
        temperature_seasonal_statistics = {
            season: {
                source: {name: Statistics() for name in tabsd_classes}
                for source in ("hicar", "rea_l")
            }
            for season in ("DJF", "MAM", "JJA", "SON")
        }
        tabsd_times = {}
        for dataset in tabsd_datasets:
            for index, valid in enumerate(decoded_times(dataset)):
                if valid in tabsd_times:
                    failures.append(f"duplicate TabsD time {valid.isoformat()}")
                tabsd_times[valid] = (dataset, index)
        first_reference = next(iter(reference_records.values()))
        tabsd_reference_setup = regular_bilinear_setup(
            read_array(first_reference, "latitude"),
            read_array(first_reference, "longitude"),
            tabsd_latitude,
            tabsd_longitude,
        )
        matched_temperature_days = []
        day = start_day
        while day + timedelta(days=1) <= end_time:
            sample_times = [day + timedelta(hours=3 * index) for index in range(9)]
            if day not in tabsd_times:
                failures.append(f"TabsD reference lacks {day.date().isoformat()}")
            elif not all(valid in model_records for valid in sample_times):
                failures.append(
                    f"HICAR records do not cover TabsD day {day.date().isoformat()}"
                )
            elif not all(valid in reference_records for valid in sample_times):
                failures.append(
                    f"REA-L records do not cover TabsD day {day.date().isoformat()}"
                )
            else:
                hicar_daily_grid = np.zeros(terrain.shape, dtype=np.float64)
                reference_shape = read_array(
                    reference_records[sample_times[0]], "ta2m_ref", 0
                ).shape
                rea_l_daily_grid = np.zeros(reference_shape, dtype=np.float64)
                for index, valid in enumerate(sample_times):
                    weight = 0.5 if index in (0, 8) else 1.0
                    model, model_index = model_records[valid]
                    hicar_daily_grid += weight * read_array(model, "taix", model_index)
                    rea_l_daily_grid += weight * read_array(
                        reference_records[valid], "ta2m_ref", 0
                    )
                hicar_daily = box_mean(
                    hicar_daily_grid / 8.0,
                    tabsd_setup,
                    2,
                )
                rea_l_daily = regular_bilinear(
                    rea_l_daily_grid / 8.0,
                    tabsd_reference_setup,
                )
                tabsd_dataset, tabsd_index = tabsd_times[day]
                observed = (
                    read_array(tabsd_dataset, "TabsD", tabsd_index).reshape(-1)
                    + 273.15
                )
                season = climatological_season(day)
                for class_name, mask in tabsd_classes.items():
                    temperature_statistics["hicar"][class_name].add(
                        hicar_daily, observed, mask
                    )
                    temperature_statistics["rea_l"][class_name].add(
                        rea_l_daily, observed, mask
                    )
                    temperature_seasonal_statistics[season]["hicar"][
                        class_name
                    ].add(hicar_daily, observed, mask)
                    temperature_seasonal_statistics[season]["rea_l"][
                        class_name
                    ].add(rea_l_daily, observed, mask)
                matched_temperature_days.append(day.date().isoformat())
            day += timedelta(days=1)

        sis_latitude_1d = read_array(sis, "lat")
        sis_longitude_1d = read_array(sis, "lon")
        for dataset in sis_datasets[1:] + sis_no_horizon_datasets:
            if not (
                np.array_equal(read_array(dataset, "lat"), sis_latitude_1d)
                and np.array_equal(read_array(dataset, "lon"), sis_longitude_1d)
            ):
                raise ValueError("SIS grids differ between annual assets")
        sis_longitude, sis_latitude = np.meshgrid(sis_longitude_1d, sis_latitude_1d)
        sis_setup = grid_setup(
            transform,
            sis_latitude,
            sis_longitude,
            x,
            y,
            args.boundary_width_m,
        )
        sis_terrain = box_mean(terrain, sis_setup, 5)
        sis_classes = elevation_classes(sis_terrain, sis_setup)
        radiation_statistics = {
            product: {name: Statistics() for name in sis_classes}
            for product in ("sis", "sis_no_horizon")
        }
        radiation_seasonal_statistics = {
            season: {
                product: {name: Statistics() for name in sis_classes}
                for product in ("sis", "sis_no_horizon")
            }
            for season in ("DJF", "MAM", "JJA", "SON")
        }
        sis_times = {}
        for dataset in sis_datasets:
            for index, valid in enumerate(decoded_times(dataset)):
                if valid in sis_times:
                    failures.append(f"duplicate SIS time {valid.isoformat()}")
                sis_times[valid] = (dataset, index)
        sis_no_horizon_times = {}
        for dataset in sis_no_horizon_datasets:
            for index, valid in enumerate(decoded_times(dataset)):
                if valid in sis_no_horizon_times:
                    failures.append(
                        f"duplicate SIS-No-Horizon time {valid.isoformat()}"
                    )
                sis_no_horizon_times[valid] = (dataset, index)
        matched_radiation_times = []
        skipped_radiation_times_outside_reference = []
        sis_start = min(sis_times)
        sis_end = max(sis_times)
        for valid in ordered_times[1:]:
            if valid not in sis_times or valid not in sis_no_horizon_times:
                if valid < sis_start or valid > sis_end:
                    skipped_radiation_times_outside_reference.append(valid.isoformat())
                    continue
                failures.append(f"SIS references lack {valid.isoformat()}")
                continue
            model, model_index = model_records[valid]
            hicar_radiation = box_mean(
                read_array(model, "rsds", model_index), sis_setup, 5
            )
            sis_dataset, sis_index = sis_times[valid]
            no_horizon_dataset, no_horizon_index = sis_no_horizon_times[valid]
            references = {
                "sis": read_array(
                    sis_dataset,
                    "SIS",
                    sis_index,
                ).reshape(-1),
                "sis_no_horizon": read_array(
                    no_horizon_dataset,
                    "SIS-No-Horizon",
                    no_horizon_index,
                ).reshape(-1),
            }
            season = climatological_season(valid)
            for product, observed in references.items():
                for class_name, mask in sis_classes.items():
                    radiation_statistics[product][class_name].add(
                        hicar_radiation, observed, mask
                    )
                    radiation_seasonal_statistics[season][product][
                        class_name
                    ].add(hicar_radiation, observed, mask)
            matched_radiation_times.append(valid.isoformat())

    if len(matched_daily_windows) < 1:
        failures.append("no complete 06-to-06 UTC RhiresD window")
    if len(matched_temperature_days) < 1:
        failures.append("no complete 00-to-24 UTC TabsD day")
    if len(matched_radiation_times) < 1:
        failures.append("no matched SIS radiation times")
    for source, values in precipitation_statistics.items():
        count = values["interior_ge_10km"].count
        if count < args.minimum_pairs:
            failures.append(
                f"RhiresD {source} interior has {count} pairs; "
                f"minimum is {args.minimum_pairs}"
            )
    for source, values in temperature_statistics.items():
        count = values["interior_ge_10km"].count
        if count < args.minimum_pairs:
            failures.append(
                f"TabsD {source} interior has {count} pairs; "
                f"minimum is {args.minimum_pairs}"
            )
    for product, values in radiation_statistics.items():
        count = values["interior_ge_10km"].count
        if count < args.minimum_pairs:
            failures.append(
                f"{product} interior has {count} pairs; minimum is {args.minimum_pairs}"
            )

    payload = {
        "schema_version": 1,
        "status": "FAIL" if failures else "PASS",
        "event_name": args.event_name,
        "interpretation": (
            "Independent gridded-reference pipeline. HICAR precipitation is "
            "box-averaged to the 1 km RhiresD grid and compared over its "
            "06-to-06 UTC accumulation window; REA-L uses the same target "
            "grid and interval. HICAR and REA-L temperature use trapezoidal "
            "three-hour sampling over each 00-to-24 UTC TabsD day. HICAR "
            "instantaneous three-hourly shortwave "
            "is box-averaged near the ~2 km SIS grid and compared with the "
            "matching hourly satellite mean, so radiation scores include a "
            "documented temporal representativeness mismatch."
        ),
        "coordinate_transform": {
            "method": (
                "third-order polynomial inverse fitted from HICAR's own "
                "lat/lon and local x/y coordinates"
            ),
            "verification_count": transform["verification_count"],
            "maximum_verification_error_m": transform["maximum_verification_error_m"],
            "p99_verification_error_m": transform["p99_verification_error_m"],
        },
        "aggregation": {
            "rhiresd_hicar_box": "5 x 5 HICAR cells (~1 km square)",
            "tabsd_hicar_box": "5 x 5 HICAR cells (~1 km square)",
            "sis_hicar_box": "11 x 11 HICAR cells (~2.2 km square)",
            "rhiresd_maximum_center_offset_m": rhires_setup["maximum_center_offset_m"],
            "tabsd_maximum_center_offset_m": tabsd_setup["maximum_center_offset_m"],
            "sis_maximum_center_offset_m": sis_setup["maximum_center_offset_m"],
        },
        "matched_daily_windows": matched_daily_windows,
        "matched_temperature_days": matched_temperature_days,
        "matched_radiation_times": matched_radiation_times,
        "skipped_radiation_times_outside_reference": (
            skipped_radiation_times_outside_reference
        ),
        "metrics": {
            "rhiresd": {
                source: {
                    name: statistic.result() for name, statistic in classes.items()
                }
                for source, classes in precipitation_statistics.items()
            },
            "tabsd": {
                source: {
                    name: statistic.result() for name, statistic in classes.items()
                }
                for source, classes in temperature_statistics.items()
            },
            "sis": {
                product: {
                    name: statistic.result() for name, statistic in classes.items()
                }
                for product, classes in radiation_statistics.items()
            },
        },
        "seasonal_metrics": {
            season: {
                "rhiresd": {
                    source: {
                        name: statistic.result()
                        for name, statistic in classes.items()
                    }
                    for source, classes in precipitation_seasonal_statistics[
                        season
                    ].items()
                },
                "tabsd": {
                    source: {
                        name: statistic.result()
                        for name, statistic in classes.items()
                    }
                    for source, classes in temperature_seasonal_statistics[
                        season
                    ].items()
                },
                "sis": {
                    product: {
                        name: statistic.result()
                        for name, statistic in classes.items()
                    }
                    for product, classes in radiation_seasonal_statistics[
                        season
                    ].items()
                },
            }
            for season in ("DJF", "MAM", "JJA", "SON")
        },
        "ogd_manifest": str(args.ogd_manifest.resolve()),
        "failures": failures,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=args.report.parent, delete=False
    ) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, args.report)
    if failures:
        return 1
    Path(f"{args.report}.ready").touch()
    print(
        f"PASS: {len(matched_daily_windows)} RhiresD windows and "
        f"{len(matched_radiation_times)} SIS times"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
