#!/usr/bin/env python3
"""Compare HICAR and REA-L at quality-controlled SwissMetNet sites."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import netCDF4
import numpy as np


EPSILON = 0.622
GRAVITY = 9.80665
DRY_AIR_GAS_CONSTANT = 287.05
CALM_WIND_THRESHOLD_M_S = 0.5
OBSERVATION_PARAMETERS = (
    "tre200h0",
    "ure200h0",
    "prestah0",
    "rre150h0",
    "fkl010h0",
    "dkl010h0",
    "gre000h0",
    "htoauths",
)


def climatological_season(value: datetime) -> str:
    if value.month in (12, 1, 2):
        return "DJF"
    if value.month in (3, 4, 5):
        return "MAM"
    if value.month in (6, 7, 8):
        return "JJA"
    return "SON"


@dataclass
class PairStatistics:
    count: int = 0
    sum_error: float = 0.0
    sum_abs_error: float = 0.0
    sum_squared_error: float = 0.0
    sum_model: float = 0.0
    sum_reference: float = 0.0
    sum_model_squared: float = 0.0
    sum_reference_squared: float = 0.0
    sum_product: float = 0.0

    def add(self, model: float, reference: float) -> None:
        if not (math.isfinite(model) and math.isfinite(reference)):
            return
        error = model - reference
        self.count += 1
        self.sum_error += error
        self.sum_abs_error += abs(error)
        self.sum_squared_error += error * error
        self.sum_model += model
        self.sum_reference += reference
        self.sum_model_squared += model * model
        self.sum_reference_squared += reference * reference
        self.sum_product += model * reference

    def result(self) -> dict:
        if not self.count:
            return {"count": 0}
        count = float(self.count)
        covariance = self.sum_product - self.sum_model * self.sum_reference / count
        model_variance = (
            self.sum_model_squared - self.sum_model * self.sum_model / count
        )
        reference_variance = (
            self.sum_reference_squared
            - self.sum_reference * self.sum_reference / count
        )
        denominator = math.sqrt(max(model_variance * reference_variance, 0.0))
        return {
            "count": self.count,
            "model_mean": self.sum_model / count,
            "observation_mean": self.sum_reference / count,
            "bias": self.sum_error / count,
            "mean_absolute_error": self.sum_abs_error / count,
            "root_mean_squared_error": math.sqrt(self.sum_squared_error / count),
            "correlation": covariance / denominator if denominator > 0.0 else None,
        }


@dataclass
class CircularStatistics:
    count: int = 0
    sum_sine_error: float = 0.0
    sum_cosine_error: float = 0.0
    sum_abs_error: float = 0.0
    sum_squared_error: float = 0.0

    def add(self, model_degrees: float, observation_degrees: float) -> None:
        if not (math.isfinite(model_degrees) and math.isfinite(observation_degrees)):
            return
        error = (model_degrees - observation_degrees + 180.0) % 360.0 - 180.0
        radians = math.radians(error)
        self.count += 1
        self.sum_sine_error += math.sin(radians)
        self.sum_cosine_error += math.cos(radians)
        self.sum_abs_error += abs(error)
        self.sum_squared_error += error * error

    def result(self) -> dict:
        if not self.count:
            return {"count": 0}
        count = float(self.count)
        return {
            "count": self.count,
            "circular_bias_degrees": math.degrees(
                math.atan2(self.sum_sine_error, self.sum_cosine_error)
            ),
            "mean_absolute_circular_error_degrees": self.sum_abs_error / count,
            "root_mean_squared_circular_error_degrees": math.sqrt(
                self.sum_squared_error / count
            ),
        }


@dataclass(frozen=True)
class Site:
    meas_site: str
    abbreviation: str
    latitude: float
    longitude: float
    elevation_m: float

    @property
    def key(self) -> str:
        return f"{self.abbreviation}:{self.meas_site}"


def finite_float(value: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return math.nan
    return result if math.isfinite(result) else math.nan


def parse_time(value: str) -> datetime:
    for form in ("%Y%m%d%H%M%S", "%Y%m%d%H%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value.strip(), form).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    raise ValueError(f"unrecognized observation time {value!r}")


def canonical_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    rounded = value.replace(microsecond=0)
    if abs((value - rounded).total_seconds()) <= 1.0:
        return rounded
    return value


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


def read_2d(dataset: netCDF4.Dataset, name: str, index: int | None = None) -> np.ndarray:
    variable = dataset.variables[name]
    values = variable[:] if index is None else variable[index]
    values = np.squeeze(np.ma.asarray(values))
    if np.ma.count_masked(values):
        values = values.filled(np.nan)
    return np.asarray(values, dtype=np.float64)


def sample_hicar(
    dataset: netCDF4.Dataset,
    name: str,
    index: int,
    y_indices: np.ndarray,
    x_indices: np.ndarray,
) -> np.ndarray:
    return read_2d(dataset, name, index)[y_indices, x_indices]


def read_observations(
    path: Path,
) -> tuple[
    dict[str, Site],
    dict[datetime, dict[str, dict[str, float]]],
    dict,
]:
    with path.open(encoding="utf-8", errors="strict", newline="") as stream:
        reader = csv.reader(stream, delimiter=";")
        header = next(reader)
        lower = [name.strip().lower() for name in header]
        required = {
            "meas_site",
            "termin",
            "latitude",
            "longitude",
            "elev",
            "nat_abbr",
        } | set(OBSERVATION_PARAMETERS)
        missing = sorted(required - set(lower))
        if missing:
            raise ValueError(f"observation CSV is missing columns: {missing}")
        position = {name: lower.index(name) for name in required}
        sites: dict[str, Site] = {}
        observations: dict[datetime, dict[str, dict[str, float]]] = {}
        row_count = 0
        rejected_quality_values = 0
        for row in reader:
            if not row or len(row) < len(header):
                continue
            row_count += 1
            site = Site(
                meas_site=row[position["meas_site"]].strip(),
                abbreviation=row[position["nat_abbr"]].strip(),
                latitude=finite_float(row[position["latitude"]]),
                longitude=finite_float(row[position["longitude"]]),
                elevation_m=finite_float(row[position["elev"]]),
            )
            if not site.meas_site or not site.abbreviation:
                continue
            if not all(
                math.isfinite(value)
                for value in (site.latitude, site.longitude, site.elevation_m)
            ):
                continue
            previous = sites.get(site.key)
            if previous is not None and previous != site:
                raise ValueError(f"station metadata changes within event: {site.key}")
            sites[site.key] = site
            valid = parse_time(row[position["termin"]])
            values = observations.setdefault(valid, {}).setdefault(site.key, {})
            for parameter in OBSERVATION_PARAMETERS:
                value_index = position[parameter]
                value = finite_float(row[value_index])
                quality = finite_float(row[value_index + 3])
                if math.isfinite(value) and quality >= 4.0:
                    values[parameter] = value
                elif math.isfinite(value):
                    rejected_quality_values += 1
    return sites, observations, {
        "row_count": row_count,
        "site_count": len(sites),
        "station_abbreviation_count": len(
            {site.abbreviation for site in sites.values()}
        ),
        "rejected_quality_values": rejected_quality_values,
    }


def nearest_hicar_cells(
    latitude: np.ndarray,
    longitude: np.ndarray,
    sites: list[Site],
    stride: int = 20,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    latitude0 = float(np.nanmean(latitude))
    longitude_scale = 111.32 * math.cos(math.radians(latitude0))
    latitude_scale = 110.57
    coarse_y = np.arange(0, latitude.shape[0], stride, dtype=np.int32)
    coarse_x = np.arange(0, latitude.shape[1], stride, dtype=np.int32)
    if coarse_y[-1] != latitude.shape[0] - 1:
        coarse_y = np.append(coarse_y, latitude.shape[0] - 1)
    if coarse_x[-1] != latitude.shape[1] - 1:
        coarse_x = np.append(coarse_x, latitude.shape[1] - 1)
    coarse_latitude = latitude[np.ix_(coarse_y, coarse_x)]
    coarse_longitude = longitude[np.ix_(coarse_y, coarse_x)]
    y_indices = []
    x_indices = []
    distances_km = []
    for site in sites:
        coarse_distance_squared = (
            (coarse_latitude - site.latitude) * latitude_scale
        ) ** 2 + (
            (coarse_longitude - site.longitude) * longitude_scale
        ) ** 2
        coarse_flat = int(np.nanargmin(coarse_distance_squared))
        coarse_j, coarse_i = np.unravel_index(
            coarse_flat, coarse_distance_squared.shape
        )
        center_y = int(coarse_y[coarse_j])
        center_x = int(coarse_x[coarse_i])
        y0 = max(0, center_y - stride)
        y1 = min(latitude.shape[0], center_y + stride + 1)
        x0 = max(0, center_x - stride)
        x1 = min(latitude.shape[1], center_x + stride + 1)
        local_distance_squared = (
            (latitude[y0:y1, x0:x1] - site.latitude) * latitude_scale
        ) ** 2 + (
            (longitude[y0:y1, x0:x1] - site.longitude) * longitude_scale
        ) ** 2
        local_flat = int(np.nanargmin(local_distance_squared))
        local_y, local_x = np.unravel_index(local_flat, local_distance_squared.shape)
        y_indices.append(y0 + local_y)
        x_indices.append(x0 + local_x)
        distances_km.append(math.sqrt(float(local_distance_squared[local_y, local_x])))
    return (
        np.asarray(y_indices, dtype=np.int32),
        np.asarray(x_indices, dtype=np.int32),
        np.asarray(distances_km, dtype=np.float64),
    )


def regular_bilinear_setup(
    latitude: np.ndarray,
    longitude: np.ndarray,
    sites: list[Site],
) -> tuple[np.ndarray, ...]:
    target_latitude = np.asarray([site.latitude for site in sites])
    target_longitude = np.asarray([site.longitude for site in sites])
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
    if np.any(outside):
        names = [sites[index].key for index in np.flatnonzero(outside)]
        raise ValueError(f"sites outside REA-L grid: {names}")
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
    return y0, y1, x0, x1, wy, wx, reverse_y, reverse_x


def regular_bilinear(values: np.ndarray, setup: tuple[np.ndarray, ...]) -> np.ndarray:
    y0, y1, x0, x1, wy, wx, reverse_y, reverse_x = setup
    if reverse_y:
        values = values[::-1, :]
    if reverse_x:
        values = values[:, ::-1]
    return (
        values[y0, x0] * (1.0 - wy) * (1.0 - wx)
        + values[y1, x0] * wy * (1.0 - wx)
        + values[y0, x1] * (1.0 - wy) * wx
        + values[y1, x1] * wy * wx
    )


def relative_humidity_percent(
    temperature_k: np.ndarray, specific_humidity: np.ndarray, pressure_pa: np.ndarray
) -> np.ndarray:
    vapor_pressure = specific_humidity * pressure_pa / (
        EPSILON + (1.0 - EPSILON) * specific_humidity
    )
    temperature_c = temperature_k - 273.15
    saturation_pressure = 611.2 * np.exp(
        17.67 * temperature_c / (temperature_c + 243.5)
    )
    return 100.0 * vapor_pressure / saturation_pressure


def wind_direction_from(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    return (270.0 - np.degrees(np.arctan2(v, u))) % 360.0


def height_adjust_temperature(
    temperature_k: np.ndarray,
    source_elevation_m: np.ndarray,
    target_elevation_m: np.ndarray,
    lapse_rate_k_m: float,
) -> np.ndarray:
    return temperature_k + lapse_rate_k_m * (
        target_elevation_m - source_elevation_m
    )


def height_adjust_pressure(
    pressure_pa: np.ndarray,
    temperature_k: np.ndarray,
    source_elevation_m: np.ndarray,
    target_elevation_m: np.ndarray,
) -> np.ndarray:
    return pressure_pa * np.exp(
        -GRAVITY
        * (target_elevation_m - source_elevation_m)
        / (DRY_AIR_GAS_CONSTANT * temperature_k)
    )


def class_memberships(
    sites: list[Site],
    y_indices: np.ndarray,
    x_indices: np.ndarray,
    terrain: np.ndarray,
    dx_m: float,
    boundary_width_m: float,
) -> tuple[dict[str, np.ndarray], np.ndarray]:
    site_elevation = np.asarray([site.elevation_m for site in sites])
    boundary_cells = int(round(boundary_width_m / dx_m))
    distance_cells = np.minimum.reduce(
        (
            y_indices,
            x_indices,
            terrain.shape[0] - 1 - y_indices,
            terrain.shape[1] - 1 - x_indices,
        )
    )
    relative_terrain = np.empty(len(sites), dtype=np.float64)
    relief_cells = max(1, int(round(5_000.0 / dx_m)))
    for index, (y_index, x_index) in enumerate(zip(y_indices, x_indices)):
        y0 = max(0, int(y_index) - relief_cells)
        y1 = min(terrain.shape[0], int(y_index) + relief_cells + 1)
        x0 = max(0, int(x_index) - relief_cells)
        x1 = min(terrain.shape[1], int(x_index) + relief_cells + 1)
        relative_terrain[index] = terrain[y_index, x_index] - np.nanmedian(
            terrain[y0:y1, x0:x1]
        )
    return {
        "all_sites": np.ones(len(sites), dtype=bool),
        "interior_ge_10km": distance_cells >= boundary_cells,
        "boundary_lt_10km": distance_cells < boundary_cells,
        "station_elevation_lt_500m": site_elevation < 500.0,
        "station_elevation_500_1000m": (site_elevation >= 500.0)
        & (site_elevation < 1000.0),
        "station_elevation_1000_1500m": (site_elevation >= 1000.0)
        & (site_elevation < 1500.0),
        "station_elevation_1500_2000m": (site_elevation >= 1500.0)
        & (site_elevation < 2000.0),
        "station_elevation_ge_2000m": site_elevation >= 2000.0,
        "station_elevation_2000_3000m": (site_elevation >= 2000.0)
        & (site_elevation < 3000.0),
        "station_elevation_ge_3000m": site_elevation >= 3000.0,
        "terrain_valley_relative_lt_minus_150m": relative_terrain < -150.0,
        "terrain_neutral_relative_pm_150m": np.abs(relative_terrain) <= 150.0,
        "terrain_ridge_relative_gt_150m": relative_terrain > 150.0,
    }, relative_terrain


def create_accumulators(classes: dict[str, np.ndarray]) -> dict:
    metrics = (
        "temperature_2m_raw_k",
        "temperature_2m_height_adjusted_k",
        "relative_humidity_2m_percent",
        "surface_pressure_raw_pa",
        "surface_pressure_height_adjusted_pa",
        "u_wind_10m_m_s",
        "v_wind_10m_m_s",
        "wind_speed_10m_m_s",
        "global_shortwave_radiation_w_m2",
        "snow_height_m",
        "precipitation_interval_kg_m2",
    )
    return {
        source: {
            class_name: {
                "pairs": {metric: PairStatistics() for metric in metrics},
                "wind_direction": CircularStatistics(),
                "wind_vector_squared_error_sum": 0.0,
                "wind_vector_pair_count": 0,
            }
            for class_name in classes
        }
        for source in ("hicar", "rea_l")
    }


def accumulator_results(accumulators: dict) -> dict:
    metric_results = {}
    for source, source_classes in accumulators.items():
        metric_results[source] = {}
        for class_name, values in source_classes.items():
            pairs = {
                metric: statistic.result()
                for metric, statistic in values["pairs"].items()
            }
            count = values["wind_vector_pair_count"]
            pairs["wind_vector"] = {
                "count": count,
                "vector_root_mean_squared_error_m_s": math.sqrt(
                    values["wind_vector_squared_error_sum"] / count
                )
                if count
                else None,
            }
            pairs["wind_direction"] = values["wind_direction"].result()
            metric_results[source][class_name] = pairs
    return metric_results


def add_site_values(
    accumulators: dict,
    classes: dict[str, np.ndarray],
    source: str,
    site_index: int,
    model: dict[str, float],
    observation: dict[str, float],
) -> None:
    for class_name, membership in classes.items():
        if not membership[site_index]:
            continue
        target = accumulators[source][class_name]
        for metric, observed in observation.items():
            if metric in target["pairs"] and metric in model:
                target["pairs"][metric].add(model[metric], observed)
        if all(
            key in model and key in observation
            for key in ("u_wind_10m_m_s", "v_wind_10m_m_s")
        ):
            du = model["u_wind_10m_m_s"] - observation["u_wind_10m_m_s"]
            dv = model["v_wind_10m_m_s"] - observation["v_wind_10m_m_s"]
            if math.isfinite(du) and math.isfinite(dv):
                target["wind_vector_squared_error_sum"] += du * du + dv * dv
                target["wind_vector_pair_count"] += 1
        if (
            "wind_direction_degrees" in model
            and "wind_direction_degrees" in observation
            and model.get("wind_speed_10m_m_s", 0.0)
            >= CALM_WIND_THRESHOLD_M_S
            and observation.get("wind_speed_10m_m_s", 0.0)
            >= CALM_WIND_THRESHOLD_M_S
        ):
            target["wind_direction"].add(
                model["wind_direction_degrees"],
                observation["wind_direction_degrees"],
            )


def observation_values(
    records: dict[str, dict[str, float]],
    site_key: str,
) -> dict[str, float]:
    values = records.get(site_key, {})
    result: dict[str, float] = {}
    if "tre200h0" in values:
        result["temperature_2m_raw_k"] = values["tre200h0"] + 273.15
        result["temperature_2m_height_adjusted_k"] = values["tre200h0"] + 273.15
    if "ure200h0" in values:
        result["relative_humidity_2m_percent"] = values["ure200h0"]
    if "prestah0" in values:
        result["surface_pressure_raw_pa"] = values["prestah0"] * 100.0
        result["surface_pressure_height_adjusted_pa"] = values["prestah0"] * 100.0
    if "fkl010h0" in values and "dkl010h0" in values:
        speed = values["fkl010h0"]
        direction = values["dkl010h0"]
        angle = math.radians(direction)
        result["wind_speed_10m_m_s"] = speed
        result["wind_direction_degrees"] = direction
        result["u_wind_10m_m_s"] = -speed * math.sin(angle)
        result["v_wind_10m_m_s"] = -speed * math.cos(angle)
    if "gre000h0" in values:
        result["global_shortwave_radiation_w_m2"] = values["gre000h0"]
    if "htoauths" in values:
        result["snow_height_m"] = values["htoauths"] / 100.0
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--static-file", type=Path, required=True)
    parser.add_argument("--output-file", type=Path, action="append", required=True)
    parser.add_argument("--reference-list", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--boundary-width-m", type=float, default=10_000.0)
    parser.add_argument("--temperature-lapse-rate-k-m", type=float, default=-0.0065)
    parser.add_argument("--minimum-core-pairs", type=int, default=100)
    args = parser.parse_args()

    failures: list[str] = []
    sites_by_key, observations, observation_inventory = read_observations(
        args.observations
    )
    sites = sorted(sites_by_key.values(), key=lambda site: site.key)
    site_position = {site.key: index for index, site in enumerate(sites)}
    site_elevation = np.asarray([site.elevation_m for site in sites])

    with netCDF4.Dataset(args.static_file) as static:
        latitude = read_2d(static, "lat")
        longitude = read_2d(static, "lon")
        terrain = read_2d(static, "topo")
        dx_m = float(getattr(static, "hicar_dx_m", 200.0))
    y_indices, x_indices, distances_km = nearest_hicar_cells(
        latitude, longitude, sites
    )
    if float(np.max(distances_km)) > max(1.0, 3.0 * dx_m / 1000.0):
        failures.append(
            f"maximum station-to-grid distance is {np.max(distances_km):.3f} km"
        )
    classes, relative_terrain = class_memberships(
        sites,
        y_indices,
        x_indices,
        terrain,
        dx_m,
        args.boundary_width_m,
    )
    accumulators = create_accumulators(classes)
    seasonal_accumulators = {
        season: create_accumulators(classes)
        for season in ("DJF", "MAM", "JJA", "SON")
    }

    model_records: dict[datetime, tuple[netCDF4.Dataset, int]] = {}
    output_datasets = [netCDF4.Dataset(path) for path in args.output_file]
    reference_datasets: list[netCDF4.Dataset] = []
    try:
        for dataset in output_datasets:
            for index, valid in enumerate(decoded_times(dataset)):
                if valid in model_records:
                    failures.append(f"duplicate HICAR time {valid.isoformat()}")
                model_records[valid] = (dataset, index)
        if not model_records:
            failures.append("no HICAR output records")
            raise RuntimeError("no HICAR output records")

        reference_paths = [
            Path(line.strip().strip('"'))
            for line in args.reference_list.read_text().splitlines()
            if line.strip()
        ]
        reference_records: dict[datetime, netCDF4.Dataset] = {}
        reference_setup = None
        reference_terrain = None
        for path in reference_paths:
            dataset = netCDF4.Dataset(path)
            reference_datasets.append(dataset)
            valid = decoded_times(dataset)[0]
            reference_records[valid] = dataset
            if reference_setup is None:
                reference_setup = regular_bilinear_setup(
                    read_2d(dataset, "latitude"),
                    read_2d(dataset, "longitude"),
                    sites,
                )
                reference_terrain = regular_bilinear(
                    read_2d(dataset, "source_terrain", 0),
                    reference_setup,
                )

        ordered_times = sorted(model_records)
        previous_model_precipitation = None
        previous_model_time = None
        matched_times = []
        for valid in ordered_times:
            if valid not in reference_records:
                failures.append(f"missing REA-L reference at {valid.isoformat()}")
                continue
            if valid not in observations:
                failures.append(f"missing station observations at {valid.isoformat()}")
                continue
            model_dataset, model_index = model_records[valid]
            reference_dataset = reference_records[valid]
            assert reference_setup is not None
            assert reference_terrain is not None

            hicar_temperature = sample_hicar(
                model_dataset, "taix", model_index, y_indices, x_indices
            )
            hicar_pressure = sample_hicar(
                model_dataset, "psfc", model_index, y_indices, x_indices
            )
            hicar_humidity = sample_hicar(
                model_dataset, "hus2m", model_index, y_indices, x_indices
            )
            hicar_u = sample_hicar(
                model_dataset, "u10m", model_index, y_indices, x_indices
            )
            hicar_v = sample_hicar(
                model_dataset, "v10m", model_index, y_indices, x_indices
            )
            hicar_snow = sample_hicar(
                model_dataset, "snow_height", model_index, y_indices, x_indices
            )
            hicar_shortwave = sample_hicar(
                model_dataset, "rsds", model_index, y_indices, x_indices
            )
            hicar_precipitation = sample_hicar(
                model_dataset, "precipitation", model_index, y_indices, x_indices
            )
            hicar_terrain = terrain[y_indices, x_indices]
            hicar_fields = {
                "temperature_2m_raw_k": hicar_temperature,
                "temperature_2m_height_adjusted_k": height_adjust_temperature(
                    hicar_temperature,
                    hicar_terrain,
                    site_elevation,
                    args.temperature_lapse_rate_k_m,
                ),
                "relative_humidity_2m_percent": relative_humidity_percent(
                    hicar_temperature, hicar_humidity, hicar_pressure
                ),
                "surface_pressure_raw_pa": hicar_pressure,
                "surface_pressure_height_adjusted_pa": height_adjust_pressure(
                    hicar_pressure,
                    hicar_temperature,
                    hicar_terrain,
                    site_elevation,
                ),
                "u_wind_10m_m_s": hicar_u,
                "v_wind_10m_m_s": hicar_v,
                "wind_speed_10m_m_s": np.hypot(hicar_u, hicar_v),
                "wind_direction_degrees": wind_direction_from(hicar_u, hicar_v),
                "global_shortwave_radiation_w_m2": hicar_shortwave,
                "snow_height_m": hicar_snow,
            }

            reference_temperature = regular_bilinear(
                read_2d(reference_dataset, "ta2m_ref", 0), reference_setup
            )
            reference_pressure = regular_bilinear(
                read_2d(reference_dataset, "psfc_ref", 0), reference_setup
            )
            reference_humidity = regular_bilinear(
                read_2d(reference_dataset, "hus2m_ref", 0), reference_setup
            )
            reference_u = regular_bilinear(
                read_2d(reference_dataset, "u10m_ref", 0), reference_setup
            )
            reference_v = regular_bilinear(
                read_2d(reference_dataset, "v10m_ref", 0), reference_setup
            )
            reference_fields = {
                "temperature_2m_raw_k": reference_temperature,
                "temperature_2m_height_adjusted_k": height_adjust_temperature(
                    reference_temperature,
                    reference_terrain,
                    site_elevation,
                    args.temperature_lapse_rate_k_m,
                ),
                "relative_humidity_2m_percent": relative_humidity_percent(
                    reference_temperature, reference_humidity, reference_pressure
                ),
                "surface_pressure_raw_pa": reference_pressure,
                "surface_pressure_height_adjusted_pa": height_adjust_pressure(
                    reference_pressure,
                    reference_temperature,
                    reference_terrain,
                    site_elevation,
                ),
                "u_wind_10m_m_s": reference_u,
                "v_wind_10m_m_s": reference_v,
                "wind_speed_10m_m_s": np.hypot(reference_u, reference_v),
                "wind_direction_degrees": wind_direction_from(
                    reference_u, reference_v
                ),
                "snow_height_m": regular_bilinear(
                    read_2d(reference_dataset, "snow_height_ref", 0),
                    reference_setup,
                ),
            }

            if previous_model_precipitation is not None:
                hicar_fields["precipitation_interval_kg_m2"] = (
                    hicar_precipitation - previous_model_precipitation
                )
                reference_fields["precipitation_interval_kg_m2"] = regular_bilinear(
                    read_2d(
                        reference_dataset, "precipitation_interval_ref", 0
                    ),
                    reference_setup,
                )
            for site in sites:
                site_index = site_position[site.key]
                observed = observation_values(observations[valid], site.key)
                if previous_model_time is not None:
                    precipitation_values = []
                    hour = previous_model_time + timedelta(hours=1)
                    while hour <= valid:
                        value = observations.get(hour, {}).get(site.key, {}).get(
                            "rre150h0"
                        )
                        if value is None:
                            precipitation_values = []
                            break
                        precipitation_values.append(value)
                        hour += timedelta(hours=1)
                    if precipitation_values:
                        observed["precipitation_interval_kg_m2"] = float(
                            sum(precipitation_values)
                        )
                add_site_values(
                    accumulators,
                    classes,
                    "hicar",
                    site_index,
                    {name: float(values[site_index]) for name, values in hicar_fields.items()},
                    observed,
                )
                add_site_values(
                    seasonal_accumulators[climatological_season(valid)],
                    classes,
                    "hicar",
                    site_index,
                    {
                        name: float(values[site_index])
                        for name, values in hicar_fields.items()
                    },
                    observed,
                )
                add_site_values(
                    accumulators,
                    classes,
                    "rea_l",
                    site_index,
                    {
                        name: float(values[site_index])
                        for name, values in reference_fields.items()
                    },
                    observed,
                )
                add_site_values(
                    seasonal_accumulators[climatological_season(valid)],
                    classes,
                    "rea_l",
                    site_index,
                    {
                        name: float(values[site_index])
                        for name, values in reference_fields.items()
                    },
                    observed,
                )
            previous_model_precipitation = hicar_precipitation
            previous_model_time = valid
            matched_times.append(valid)
    finally:
        for dataset in output_datasets + reference_datasets:
            dataset.close()

    core_metrics = (
        "temperature_2m_height_adjusted_k",
        "relative_humidity_2m_percent",
        "surface_pressure_height_adjusted_pa",
        "wind_speed_10m_m_s",
        "precipitation_interval_kg_m2",
    )
    for source in ("hicar", "rea_l"):
        for metric in core_metrics:
            count = accumulators[source]["all_sites"]["pairs"][metric].count
            if count < args.minimum_core_pairs:
                failures.append(
                    f"{source}/{metric} has {count} pairs; "
                    f"minimum is {args.minimum_core_pairs}"
                )

    metric_results = accumulator_results(accumulators)

    report = {
        "schema_version": 1,
        "event_name": args.event_name,
        "interpretation": (
            "Independent station comparison. HICAR values are instantaneous at "
            "three-hour output times, while station temperature, humidity, "
            "wind, and radiation are hourly aggregates. Precipitation is "
            "compared over aligned ending-hour intervals."
        ),
        "sampling": {
            "horizontal": "nearest HICAR cell; bilinear REA-L regular grid",
            "temperature_height_adjustment": (
                "T_at_station=T_model+lapse_rate*(H_station-H_model)"
            ),
            "temperature_lapse_rate_k_m": args.temperature_lapse_rate_k_m,
            "pressure_height_adjustment": (
                "p_at_station=p_model*exp(-g*(H_station-H_model)/(Rd*T_model))"
            ),
            "quality_filter": (
                "DWH query and reader require data-quality category >=4"
            ),
            "terrain_exposure": (
                "HICAR-cell elevation minus median terrain in a 5 km "
                "half-width square; valley/ridge thresholds are -/+150 m"
            ),
            "minimum_core_pairs": args.minimum_core_pairs,
            "calm_direction_mask_threshold_m_s": CALM_WIND_THRESHOLD_M_S,
        },
        "observation_inventory": observation_inventory,
        "observation_file": str(args.observations.resolve()),
        "matched_model_times": [value.isoformat() for value in matched_times],
        "station_mapping": {
            "site_count": len(sites),
            "maximum_nearest_cell_distance_km": float(np.max(distances_km)),
            "mean_nearest_cell_distance_km": float(np.mean(distances_km)),
            "class_site_counts": {
                name: int(np.count_nonzero(values))
                for name, values in classes.items()
            },
            "sites": [
                {
                    "key": site.key,
                    "abbreviation": site.abbreviation,
                    "meas_site": site.meas_site,
                    "latitude": site.latitude,
                    "longitude": site.longitude,
                    "station_elevation_m": site.elevation_m,
                    "hicar_y_index": int(y_indices[index]),
                    "hicar_x_index": int(x_indices[index]),
                    "hicar_elevation_m": float(
                        terrain[y_indices[index], x_indices[index]]
                    ),
                    "nearest_cell_distance_km": float(distances_km[index]),
                    "terrain_relative_elevation_m": float(relative_terrain[index]),
                }
                for index, site in enumerate(sites)
            ],
        },
        "metrics": metric_results,
        "seasonal_metrics": {
            season: accumulator_results(values)
            for season, values in seasonal_accumulators.items()
        },
        "issues": failures,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=args.report.parent, delete=False
    ) as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, args.report)
    if failures:
        return 1
    print(
        f"Compared HICAR and REA-L with {len(sites)} SwissMetNet sites "
        f"at {len(matched_times)} model times"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
