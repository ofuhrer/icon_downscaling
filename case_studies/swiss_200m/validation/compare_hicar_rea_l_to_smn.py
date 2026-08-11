#!/usr/bin/env python3
"""Compare HICAR and REA-L at quality-controlled SwissMetNet sites."""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile

import netCDF4
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from preprocessing.hicarprep import (  # noqa: E402
    grid_to_earth_wind,
    hicar_grid_rotation,
)


EPSILON = 0.622
GRAVITY = 9.80665
DRY_AIR_GAS_CONSTANT = 287.05
WIND_DIRECTION_OBSERVATION_THRESHOLD_M_S = 2.5
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
PAIR_METRICS = (
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
        bias = self.sum_error / count
        rmse = math.sqrt(self.sum_squared_error / count)
        return {
            "count": self.count,
            "model_mean": self.sum_model / count,
            "observation_mean": self.sum_reference / count,
            "bias": bias,
            "mean_absolute_error": self.sum_abs_error / count,
            "root_mean_squared_error": rmse,
            "centered_root_mean_squared_error": math.sqrt(
                max(rmse * rmse - bias * bias, 0.0)
            ),
            "model_standard_deviation": math.sqrt(max(model_variance / count, 0.0)),
            "observation_standard_deviation": math.sqrt(
                max(reference_variance / count, 0.0)
            ),
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


@dataclass(frozen=True)
class HourlyHicarRecord:
    """Six ten-minute records representing the hour ending at ``valid``."""

    samples: tuple[tuple, ...]


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
    if abs((value - rounded).total_seconds()) <= 0.5:
        return rounded
    return value


def exact_integral_lead_hour(valid: datetime, start: datetime) -> int:
    """Return a nonnegative whole-hour lead, rejecting ambiguous timestamps."""
    delta = valid - start
    if (
        delta < timedelta(0)
        or delta.microseconds != 0
        or delta.seconds % 3600 != 0
    ):
        raise ValueError(
            f"model output time {valid.isoformat()} is not an exact nonnegative "
            f"integral-hour lead from {start.isoformat()}"
        )
    return delta.days * 24 + delta.seconds // 3600


def select_hourly_evaluation_records(
    records: dict[datetime, tuple],
    simulation_start: datetime,
    evaluation_start: datetime,
    evaluation_end: datetime,
) -> dict[datetime, HourlyHicarRecord]:
    """Build ending-hour aggregates in an inclusive evaluation window.

    SwissMetNet ``h0`` means are aggregates over the preceding civil hour.
    HICAR writes every ten minutes, so each selected hour consists of the six
    ending-interval samples at minutes 10, 20, ..., 60.  Missing samples are a
    hard error instead of silently changing an hour's weighting.  The lead
    remains elapsed time from the original simulation start.
    """
    if not simulation_start <= evaluation_start < evaluation_end:
        raise ValueError("invalid simulation/evaluation time ordering")
    result: dict[datetime, HourlyHicarRecord] = {}
    valid = evaluation_start
    while valid <= evaluation_end:
        exact_integral_lead_hour(valid, simulation_start)
        sample_times = tuple(
            valid - timedelta(minutes=offset) for offset in (50, 40, 30, 20, 10, 0)
        )
        missing = [time for time in sample_times if time not in records]
        if missing:
            rendered = ", ".join(time.isoformat() for time in missing)
            raise ValueError(
                f"HICAR ending-hour aggregate at {valid.isoformat()} lacks "
                f"ten-minute samples: {rendered}"
            )
        result[valid] = HourlyHicarRecord(
            samples=tuple(records[time] for time in sample_times)
        )
        valid += timedelta(hours=1)
    return result


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


def sample_hicar_hourly(
    record: HourlyHicarRecord,
    name: str,
    y_indices: np.ndarray,
    x_indices: np.ndarray,
) -> np.ndarray:
    """Return the arithmetic mean of six ending-ten-minute samples."""
    values = [
        sample_hicar(dataset, name, index, y_indices, x_indices)
        for dataset, index in record.samples
    ]
    return np.mean(np.stack(values), axis=0)


def sample_hicar_series(
    record: HourlyHicarRecord,
    name: str,
    y_indices: np.ndarray,
    x_indices: np.ndarray,
) -> np.ndarray:
    """Return six ending-ten-minute samples with time as the first axis."""
    return np.stack(
        [
            sample_hicar(dataset, name, index, y_indices, x_indices)
            for dataset, index in record.samples
        ]
    )


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


def read_native_reference(
    path: Path,
) -> dict[datetime, dict[str, dict[str, float]]]:
    records: dict[datetime, dict[str, dict[str, float]]] = {}
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {
            "valid_time", "station_key", "source_terrain_m", "ta2m_ref",
            "psfc_ref", "hus2m_ref", "u10m_ref", "v10m_ref",
            "snow_height_ref", "precipitation_interval_ref",
        }
        missing = sorted(required - set(reader.fieldnames or ()))
        if missing:
            raise ValueError(f"native REA-L reference CSV is missing columns: {missing}")
        for row in reader:
            valid = canonical_time(
                datetime.fromisoformat(row["valid_time"].replace("Z", "+00:00"))
            )
            site_key = row["station_key"]
            if site_key in records.setdefault(valid, {}):
                raise ValueError(f"duplicate native REA-L row for {valid}/{site_key}")
            records[valid][site_key] = {
                name: finite_float(row[name]) for name in required - {"valid_time", "station_key"}
            }
    return records


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


def select_sites_by_distance(
    sites: list[Site],
    y_indices: np.ndarray,
    x_indices: np.ndarray,
    distances_km: np.ndarray,
    maximum_distance_km: float,
) -> tuple[list[Site], np.ndarray, np.ndarray, np.ndarray, list[dict]]:
    inside_domain = distances_km <= maximum_distance_km
    excluded_sites = [
        {
            "key": site.key,
            "nearest_cell_distance_km": float(distance),
        }
        for site, distance, inside in zip(sites, distances_km, inside_domain)
        if not inside
    ]
    selected_sites = [site for site, inside in zip(sites, inside_domain) if inside]
    if not selected_sites:
        raise ValueError("no observation sites fall within the HICAR domain")
    return (
        selected_sites,
        y_indices[inside_domain],
        x_indices[inside_domain],
        distances_km[inside_domain],
        excluded_sites,
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
    return {
        source: {
            class_name: {
                "pairs": {metric: PairStatistics() for metric in PAIR_METRICS},
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


def finite_values(
    values: dict[str, float], names: tuple[str, ...]
) -> tuple[float, ...] | None:
    try:
        result = tuple(float(values[name]) for name in names)
    except (KeyError, TypeError, ValueError):
        return None
    return result if all(math.isfinite(value) for value in result) else None


def select_common_site_values(
    hicar: dict[str, float],
    rea_l: dict[str, float],
    observation: dict[str, float],
    accounting: dict[str, Counter],
) -> dict:
    """Select exact finite observation/HICAR/REA-L metric triplets once."""

    def select(
        metric: str,
        names: tuple[str, ...],
        require_noncalm: bool = False,
    ) -> tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...]] | None:
        if not any(name in hicar or name in rea_l for name in names):
            return None
        counts = accounting.setdefault(metric, Counter())
        counts["candidate_station_time_count"] += 1
        observed = finite_values(observation, names)
        hicar_values = finite_values(hicar, names)
        rea_l_values = finite_values(rea_l, names)
        if observed is None:
            reason = "observation_missing_or_nonfinite"
        elif hicar_values is None and rea_l_values is None:
            reason = "both_models_missing_or_nonfinite"
        elif hicar_values is None:
            reason = "hicar_missing_or_nonfinite"
        elif rea_l_values is None:
            reason = "rea_l_missing_or_nonfinite"
        elif (
            require_noncalm
            and observed[1] < WIND_DIRECTION_OBSERVATION_THRESHOLD_M_S
        ):
            reason = "observation_calm_wind_direction_mask"
        else:
            counts["accepted_common_triplet_count"] += 1
            return hicar_values, rea_l_values, observed
        counts[reason] += 1
        return None

    pairs = {
        metric: selected
        for metric in PAIR_METRICS
        if (selected := select(metric, (metric,))) is not None
    }
    return {
        "pairs": pairs,
        "wind_vector": select(
            "wind_vector", ("u_wind_10m_m_s", "v_wind_10m_m_s")
        ),
        "wind_direction": select(
            "wind_direction",
            ("wind_direction_degrees", "wind_speed_10m_m_s"),
            require_noncalm=True,
        ),
    }


def add_common_site_values(
    accumulators: dict,
    classes: dict[str, np.ndarray],
    site_index: int,
    common: dict,
) -> None:
    for class_name, membership in classes.items():
        if not membership[site_index]:
            continue
        hicar_target = accumulators["hicar"][class_name]
        rea_l_target = accumulators["rea_l"][class_name]
        for metric, (hicar, rea_l, observed) in common["pairs"].items():
            hicar_target["pairs"][metric].add(hicar[0], observed[0])
            rea_l_target["pairs"][metric].add(rea_l[0], observed[0])
        vector = common["wind_vector"]
        if vector is not None:
            hicar, rea_l, observed = vector
            for target, model in (
                (hicar_target, hicar),
                (rea_l_target, rea_l),
            ):
                du = model[0] - observed[0]
                dv = model[1] - observed[1]
                target["wind_vector_squared_error_sum"] += du * du + dv * dv
                target["wind_vector_pair_count"] += 1
        direction = common["wind_direction"]
        if direction is not None:
            hicar, rea_l, observed = direction
            hicar_target["wind_direction"].add(hicar[0], observed[0])
            rea_l_target["wind_direction"].add(rea_l[0], observed[0])


def common_triplet_accounting_results(accounting: dict[str, Counter]) -> dict:
    return {
        metric: {
            "candidate_station_time_count": counts[
                "candidate_station_time_count"
            ],
            "accepted_common_triplet_count": counts[
                "accepted_common_triplet_count"
            ],
            "excluded_station_time_count": (
                counts["candidate_station_time_count"]
                - counts["accepted_common_triplet_count"]
            ),
            "exclusions": {
                name: count
                for name, count in sorted(counts.items())
                if name
                not in {
                    "candidate_station_time_count",
                    "accepted_common_triplet_count",
                }
                and count
            },
        }
        for metric, counts in sorted(accounting.items())
    }


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
    reference = parser.add_mutually_exclusive_group(required=True)
    reference.add_argument("--reference-list", type=Path)
    reference.add_argument("--native-reference-csv", type=Path)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--simulation-start",
        help="original integration start used to label elapsed lead hours",
    )
    parser.add_argument("--evaluation-start", help="inclusive scoring-window start")
    parser.add_argument("--evaluation-end", help="inclusive scoring-window end")
    parser.add_argument("--boundary-width-m", type=float, default=10_000.0)
    parser.add_argument("--temperature-lapse-rate-k-m", type=float, default=-0.0065)
    parser.add_argument("--minimum-core-pairs", type=int, default=100)
    parser.add_argument(
        "--overlap-policy",
        choices=("error", "last"),
        default="error",
        help="use 'last' for restart campaigns with one-hour overlap output",
    )
    args = parser.parse_args()

    failures: list[str] = []
    sites_by_key, observations, observation_inventory = read_observations(
        args.observations
    )
    sites = sorted(sites_by_key.values(), key=lambda site: site.key)

    with netCDF4.Dataset(args.static_file) as static:
        latitude = read_2d(static, "lat")
        longitude = read_2d(static, "lon")
        terrain = read_2d(static, "topo")
        dx_m = float(getattr(static, "hicar_dx_m", 200.0))
    grid_sine, grid_cosine = hicar_grid_rotation(
        latitude, longitude, dx_m=dx_m
    )
    y_indices, x_indices, distances_km = nearest_hicar_cells(
        latitude, longitude, sites
    )
    maximum_distance_km = max(1.0, 3.0 * dx_m / 1000.0)
    sites, y_indices, x_indices, distances_km, excluded_sites = (
        select_sites_by_distance(
            sites,
            y_indices,
            x_indices,
            distances_km,
            maximum_distance_km,
        )
    )
    site_position = {site.key: index for index, site in enumerate(sites)}
    site_elevation = np.asarray([site.elevation_m for site in sites])
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
    lead_time_accumulators: dict[int, dict] = {}
    single_site_classes = {"selected_site": np.ones(1, dtype=bool)}
    site_accumulators = {
        site.key: create_accumulators(single_site_classes) for site in sites
    }
    common_triplet_accounting: dict[str, Counter] = {}
    daylight_shortwave = PairStatistics()

    model_records: dict[datetime, tuple[netCDF4.Dataset, int]] = {}
    overlap_times: list[datetime] = []
    output_datasets = [netCDF4.Dataset(path) for path in args.output_file]
    reference_datasets: list[netCDF4.Dataset] = []
    try:
        for dataset in output_datasets:
            for index, valid in enumerate(decoded_times(dataset)):
                if valid in model_records:
                    overlap_times.append(valid)
                    if args.overlap_policy == "error":
                        failures.append(f"duplicate HICAR time {valid.isoformat()}")
                model_records[valid] = (dataset, index)
        if not model_records:
            failures.append("no HICAR output records")
            raise RuntimeError("no HICAR output records")

        explicit_window = args.evaluation_start is not None or args.evaluation_end is not None
        if explicit_window:
            if args.evaluation_start is None or args.evaluation_end is None:
                raise ValueError(
                    "--evaluation-start and --evaluation-end must be supplied together"
                )
            evaluation_start = canonical_time(parse_time(args.evaluation_start))
            evaluation_end = canonical_time(parse_time(args.evaluation_end))
            simulation_start = canonical_time(
                parse_time(args.simulation_start)
                if args.simulation_start is not None
                else evaluation_start
            )
            model_records = select_hourly_evaluation_records(
                model_records,
                simulation_start,
                evaluation_start,
                evaluation_end,
            )
            expected_evaluation_times = [
                evaluation_start + timedelta(hours=hour)
                for hour in range(
                    int((evaluation_end - evaluation_start).total_seconds() // 3600) + 1
                )
            ]
            if sorted(model_records) != expected_evaluation_times:
                failures.append(
                    "HICAR hourly evaluation records do not exactly cover the "
                    "inclusive evaluation window"
                )
        else:
            evaluation_start = min(model_records)
            evaluation_end = max(model_records)
            simulation_start = (
                canonical_time(parse_time(args.simulation_start))
                if args.simulation_start is not None
                else evaluation_start
            )
            model_records = select_hourly_evaluation_records(
                model_records,
                simulation_start,
                evaluation_start,
                evaluation_end,
            )
        if not model_records:
            failures.append("no hourly HICAR records in evaluation window")
            raise RuntimeError("no hourly HICAR records in evaluation window")

        native_reference = (
            read_native_reference(args.native_reference_csv)
            if args.native_reference_csv is not None else None
        )
        reference_paths = [] if args.reference_list is None else [
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
        if native_reference is not None:
            reference_records = {valid: None for valid in native_reference}  # type: ignore[assignment]

        ordered_times = sorted(model_records)
        previous_model_precipitation = None
        previous_model_time = None
        previous_reference_state = None
        matched_times = []
        for valid in ordered_times:
            lead_hour = exact_integral_lead_hour(valid, simulation_start)
            if valid not in reference_records:
                failures.append(f"missing REA-L reference at {valid.isoformat()}")
                continue
            if valid not in observations:
                failures.append(f"missing station observations at {valid.isoformat()}")
                continue
            model_record = model_records[valid]
            reference_dataset = reference_records[valid]
            lead_accumulator = lead_time_accumulators.setdefault(
                lead_hour, create_accumulators(classes)
            )

            hicar_temperature_series = sample_hicar_series(
                model_record, "taix", y_indices, x_indices
            )
            hicar_pressure_series = sample_hicar_series(
                model_record, "psfc", y_indices, x_indices
            )
            hicar_humidity_series = sample_hicar_series(
                model_record, "hus2m", y_indices, x_indices
            )
            hicar_grid_u_series = sample_hicar_series(
                model_record, "u10m", y_indices, x_indices
            )
            hicar_grid_v_series = sample_hicar_series(
                model_record, "v10m", y_indices, x_indices
            )
            sampled_cosine = grid_cosine[y_indices, x_indices]
            sampled_sine = grid_sine[y_indices, x_indices]
            earth_wind_samples = [
                grid_to_earth_wind(
                    grid_u, grid_v, sampled_sine, sampled_cosine
                )
                for grid_u, grid_v in zip(
                    hicar_grid_u_series, hicar_grid_v_series
                )
            ]
            hicar_earth_u_series = np.stack(
                [values[0] for values in earth_wind_samples]
            )
            hicar_earth_v_series = np.stack(
                [values[1] for values in earth_wind_samples]
            )
            mean_earth_u = np.mean(hicar_earth_u_series, axis=0)
            mean_earth_v = np.mean(hicar_earth_v_series, axis=0)
            hicar_speed = np.mean(
                np.hypot(hicar_earth_u_series, hicar_earth_v_series), axis=0
            )
            mean_vector_speed = np.hypot(mean_earth_u, mean_earth_v)
            hicar_u = np.divide(
                mean_earth_u * hicar_speed,
                mean_vector_speed,
                out=np.zeros_like(mean_earth_u),
                where=mean_vector_speed > 0.0,
            )
            hicar_v = np.divide(
                mean_earth_v * hicar_speed,
                mean_vector_speed,
                out=np.zeros_like(mean_earth_v),
                where=mean_vector_speed > 0.0,
            )
            hicar_snow = sample_hicar(
                model_record.samples[-1][0],
                "snow_height",
                model_record.samples[-1][1],
                y_indices,
                x_indices,
            )
            hicar_shortwave = sample_hicar_hourly(
                model_record, "rsds", y_indices, x_indices
            )
            hicar_precipitation = sample_hicar(
                model_record.samples[-1][0],
                "precipitation",
                model_record.samples[-1][1],
                y_indices,
                x_indices,
            )
            hicar_terrain = terrain[y_indices, x_indices]
            hicar_fields = {
                "temperature_2m_raw_k": np.mean(hicar_temperature_series, axis=0),
                "temperature_2m_height_adjusted_k": height_adjust_temperature(
                    np.mean(hicar_temperature_series, axis=0),
                    hicar_terrain,
                    site_elevation,
                    args.temperature_lapse_rate_k_m,
                ),
                "relative_humidity_2m_percent": np.mean(
                    relative_humidity_percent(
                        hicar_temperature_series,
                        hicar_humidity_series,
                        hicar_pressure_series,
                    ),
                    axis=0,
                ),
                "surface_pressure_raw_pa": np.mean(hicar_pressure_series, axis=0),
                "surface_pressure_height_adjusted_pa": np.mean(
                    height_adjust_pressure(
                        hicar_pressure_series,
                        hicar_temperature_series,
                        hicar_terrain,
                        site_elevation,
                    ),
                    axis=0,
                ),
                "u_wind_10m_m_s": hicar_u,
                "v_wind_10m_m_s": hicar_v,
                "wind_speed_10m_m_s": hicar_speed,
                "wind_direction_degrees": wind_direction_from(hicar_u, hicar_v),
                "global_shortwave_radiation_w_m2": hicar_shortwave,
                "snow_height_m": hicar_snow,
            }

            if native_reference is None:
                assert reference_setup is not None
                assert reference_terrain is not None
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
                reference_snow = regular_bilinear(
                    read_2d(reference_dataset, "snow_height_ref", 0), reference_setup
                )
                reference_precipitation = regular_bilinear(
                    read_2d(reference_dataset, "precipitation_interval_ref", 0),
                    reference_setup,
                )
            else:
                rows = native_reference[valid]
                absent_sites = [site.key for site in sites if site.key not in rows]
                if absent_sites:
                    failures.append(
                        f"native REA-L reference at {valid.isoformat()} lacks "
                        f"{len(absent_sites)} sites"
                    )
                def native_values(name: str) -> np.ndarray:
                    return np.asarray(
                        [rows.get(site.key, {}).get(name, math.nan) for site in sites],
                        dtype=np.float64,
                    )
                reference_temperature = native_values("ta2m_ref")
                reference_pressure = native_values("psfc_ref")
                reference_humidity = native_values("hus2m_ref")
                reference_u = native_values("u10m_ref")
                reference_v = native_values("v10m_ref")
                reference_snow = native_values("snow_height_ref")
                reference_precipitation = native_values("precipitation_interval_ref")
                reference_terrain = native_values("source_terrain_m")
            current_reference_state = {
                "temperature": reference_temperature,
                "pressure": reference_pressure,
                "humidity": reference_humidity,
                "u": reference_u,
                "v": reference_v,
                "terrain": reference_terrain,
            }
            reference_fields = {"snow_height_m": reference_snow}
            if previous_reference_state is None:
                hicar_fields = {"snow_height_m": hicar_snow}
            else:
                reference_temperature_mean = 0.5 * (
                    previous_reference_state["temperature"] + reference_temperature
                )
                reference_pressure_mean = 0.5 * (
                    previous_reference_state["pressure"] + reference_pressure
                )
                reference_mean_u = 0.5 * (
                    previous_reference_state["u"] + reference_u
                )
                reference_mean_v = 0.5 * (
                    previous_reference_state["v"] + reference_v
                )
                reference_speed = 0.5 * (
                    np.hypot(
                        previous_reference_state["u"],
                        previous_reference_state["v"],
                    )
                    + np.hypot(reference_u, reference_v)
                )
                reference_vector_speed = np.hypot(
                    reference_mean_u, reference_mean_v
                )
                reference_hourly_u = np.divide(
                    reference_mean_u * reference_speed,
                    reference_vector_speed,
                    out=np.zeros_like(reference_mean_u),
                    where=reference_vector_speed > 0.0,
                )
                reference_hourly_v = np.divide(
                    reference_mean_v * reference_speed,
                    reference_vector_speed,
                    out=np.zeros_like(reference_mean_v),
                    where=reference_vector_speed > 0.0,
                )
                reference_fields.update({
                    "temperature_2m_raw_k": reference_temperature_mean,
                    "temperature_2m_height_adjusted_k": 0.5 * (
                        height_adjust_temperature(
                            previous_reference_state["temperature"],
                            previous_reference_state["terrain"],
                            site_elevation,
                            args.temperature_lapse_rate_k_m,
                        )
                        + height_adjust_temperature(
                            reference_temperature,
                            reference_terrain,
                            site_elevation,
                            args.temperature_lapse_rate_k_m,
                        )
                    ),
                    "relative_humidity_2m_percent": 0.5 * (
                        relative_humidity_percent(
                            previous_reference_state["temperature"],
                            previous_reference_state["humidity"],
                            previous_reference_state["pressure"],
                        )
                        + relative_humidity_percent(
                            reference_temperature,
                            reference_humidity,
                            reference_pressure,
                        )
                    ),
                    "surface_pressure_raw_pa": reference_pressure_mean,
                    "surface_pressure_height_adjusted_pa": 0.5 * (
                        height_adjust_pressure(
                            previous_reference_state["pressure"],
                            previous_reference_state["temperature"],
                            previous_reference_state["terrain"],
                            site_elevation,
                        )
                        + height_adjust_pressure(
                            reference_pressure,
                            reference_temperature,
                            reference_terrain,
                            site_elevation,
                        )
                    ),
                    "u_wind_10m_m_s": reference_hourly_u,
                    "v_wind_10m_m_s": reference_hourly_v,
                    "wind_speed_10m_m_s": reference_speed,
                    "wind_direction_degrees": wind_direction_from(
                        reference_mean_u, reference_mean_v
                    ),
                })

            if previous_model_precipitation is not None:
                hicar_fields["precipitation_interval_kg_m2"] = (
                    hicar_precipitation - previous_model_precipitation
                )
                reference_fields["precipitation_interval_kg_m2"] = (
                    reference_precipitation
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
                hicar_site_fields = {
                    name: float(values[site_index])
                    for name, values in hicar_fields.items()
                }
                reference_site_fields = {
                    name: float(values[site_index])
                    for name, values in reference_fields.items()
                }
                common = select_common_site_values(
                    hicar_site_fields,
                    reference_site_fields,
                    observed,
                    common_triplet_accounting,
                )
                add_common_site_values(
                    accumulators,
                    classes,
                    site_index,
                    common,
                )
                add_common_site_values(
                    seasonal_accumulators[climatological_season(valid)],
                    classes,
                    site_index,
                    common,
                )
                add_common_site_values(
                    lead_accumulator,
                    classes,
                    site_index,
                    common,
                )
                add_common_site_values(
                    site_accumulators[site.key],
                    single_site_classes,
                    0,
                    common,
                )
                if previous_reference_state is not None:
                    observed_shortwave = observed.get(
                        "global_shortwave_radiation_w_m2", math.nan
                    )
                    hicar_shortwave_value = float(hicar_shortwave[site_index])
                    if observed_shortwave > 0.0:
                        daylight_shortwave.add(
                            hicar_shortwave_value, observed_shortwave
                        )
            previous_model_precipitation = hicar_precipitation
            previous_model_time = valid
            previous_reference_state = current_reference_state
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
        "schema_version": 2,
        "event_name": args.event_name,
        "interpretation": (
            "Independent station comparison on ending-hour intervals. HICAR "
            "means use six ten-minute samples; REA-L means use the trapezoidal "
            "average of consecutive hourly endpoints; SwissMetNet h0 values "
            "are civil-hour aggregates. Snow height remains endpoint-to-endpoint."
        ),
        "sampling": {
            "simulation_start": simulation_start.isoformat(),
            "evaluation_start_inclusive": evaluation_start.isoformat(),
            "evaluation_end_inclusive": evaluation_end.isoformat(),
            "temporal_selection": (
                "For each scored ending hour after the baseline endpoint: HICAR "
                "arithmetic mean of samples at minutes 10..60; REA-L linear "
                "interval estimate 0.5*(previous endpoint+current endpoint); "
                "SwissMetNet civil-hour h0 aggregate. Precipitation is an endpoint "
                "difference/sum and snow height is the ending-time state."
            ),
            "wind_rotation": (
                "HICAR grid-relative ten-metre u/v are inverse-rotated using "
                "mass-grid coefficients derived from static lat/lon with HICAR's "
                "+/-2-cell x derivative and smoothing convention. Scalar wind "
                "speed is averaged before combining with the mean-vector direction, "
                "matching post-2014 DWH fkl010h0 scalar-hourly semantics."
            ),
            "horizontal": (
                "nearest HICAR cell; nearest native REA-L cell"
                if args.native_reference_csv is not None
                else "nearest HICAR cell; bilinear REA-L regular grid"
            ),
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
            "pairing": (
                "Each metric uses exact common finite observation/HICAR/REA-L "
                "station-time triplets; neither model receives an unpaired sample."
            ),
            "terrain_exposure": (
                "HICAR-cell elevation minus median terrain in a 5 km "
                "half-width square; valley/ridge thresholds are -/+150 m"
            ),
            "minimum_core_pairs": args.minimum_core_pairs,
            "wind_direction_observation_threshold_m_s": (
                WIND_DIRECTION_OBSERVATION_THRESHOLD_M_S
            ),
        },
        "observation_inventory": observation_inventory,
        "common_triplet_accounting": {
            "interpretation": (
                "Counts are evaluated once per metric and station-time before "
                "aggregation into overlapping spatial, seasonal, lead-time, and "
                "single-site classes. Exclusion reasons are mutually exclusive."
            ),
            "metrics": common_triplet_accounting_results(
                common_triplet_accounting
            ),
        },
        "hicar_observation_shortwave_daylight_only": {
            "interpretation": (
                "HICAR six-sample ending-hour mean versus SwissMetNet gre000h0, "
                "restricted to finite pairs with observed radiation > 0 W m-2. "
                "This diagnostic is excluded from HICAR-versus-REA-L added-value "
                "ranking because the staged native REA-L reference has no "
                "shortwave-radiation field."
            ),
            "statistics": daylight_shortwave.result(),
        },
        "observation_file": str(args.observations.resolve()),
        "rea_l_reference": str(
            (args.native_reference_csv or args.reference_list).resolve()
        ),
        "matched_model_times": [value.isoformat() for value in matched_times],
        "overlap_times_replaced_by_later_file": [
            value.isoformat() for value in overlap_times
        ],
        "station_mapping": {
            "site_count": len(sites),
            "maximum_accepted_distance_km": maximum_distance_km,
            "excluded_outside_domain_site_count": len(excluded_sites),
            "excluded_outside_domain_sites": excluded_sites,
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
        "lead_time_definition": (
            "Whole elapsed hours since the original HICAR simulation start; "
            "the post-spin-up evaluation window does not reset lead time."
        ),
        "lead_time_metrics": {
            str(lead_hour): accumulator_results(values)
            for lead_hour, values in sorted(lead_time_accumulators.items())
        },
        "site_metrics": {
            site_key: {
                source: source_values["selected_site"]
                for source, source_values in accumulator_results(values).items()
            }
            for site_key, values in site_accumulators.items()
        },
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
