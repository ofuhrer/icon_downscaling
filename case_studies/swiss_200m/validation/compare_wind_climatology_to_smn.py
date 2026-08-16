#!/usr/bin/env python3
"""Verify hourly HICAR wind-climatology output against SwissMetNet h0 wind."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime
import json
import math
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile

import netCDF4
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from case_studies.swiss_200m.validation.compare_hicar_rea_l_to_smn import (  # noqa: E402
    CircularStatistics,
    PairStatistics,
    Site,
    WIND_DIRECTION_OBSERVATION_THRESHOLD_M_S,
    canonical_time,
    climatological_season,
    decoded_times,
    finite_float,
    hicar_grid_rotation,
    grid_to_earth_wind,
    nearest_hicar_cells,
    nearest_hicar_land_cells,
    observation_values,
    parse_time,
    read_2d,
    select_sites_by_distance,
)


HOURLY_VARIABLES = (
    "u_agl_mean_1h",
    "v_agl_mean_1h",
    "wind_speed_agl_mean_1h",
    "wind_speed_agl_10min_max_1h",
    "u10m_mean_1h",
    "v10m_mean_1h",
    "wind_speed_10m_mean_1h",
    "wind_speed_10m_10min_max_1h",
)
EXPECTED_HEIGHTS_AGL_M = np.asarray((50, 75, 100, 125, 150, 200, 250), dtype=float)


def read_wind_observations(path: Path) -> tuple[dict[str, Site], dict, dict]:
    required = (
        "meas_site",
        "termin",
        "latitude",
        "longitude",
        "elev",
        "nat_abbr",
        "fkl010h0",
        "dkl010h0",
    )
    sites: dict[str, Site] = {}
    observations: dict = {}
    row_count = 0
    rejected_quality_values = 0
    with path.open(encoding="utf-8", errors="strict", newline="") as stream:
        reader = csv.reader(stream, delimiter=";")
        header = next(reader)
        lower = [name.strip().lower() for name in header]
        missing = sorted(set(required) - set(lower))
        if missing:
            raise ValueError(f"observation CSV is missing columns: {missing}")
        position = {name: lower.index(name) for name in required}
        for parameter in ("fkl010h0", "dkl010h0"):
            quality_index = position[parameter] + 3
            if quality_index >= len(lower) or lower[quality_index] != "dq":
                raise ValueError(
                    f"observation column {parameter!r} is not followed by the expected dq field"
                )
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
            if not site.meas_site or not site.abbreviation or not all(
                math.isfinite(value)
                for value in (site.latitude, site.longitude, site.elevation_m)
            ):
                continue
            previous = sites.get(site.key)
            if previous is not None and previous != site:
                raise ValueError(f"station metadata changes within file: {site.key}")
            sites[site.key] = site
            values = observations.setdefault(parse_time(row[position["termin"]]), {}).setdefault(
                site.key, {}
            )
            for parameter in ("fkl010h0", "dkl010h0"):
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


def atomic_json_dump(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    temporary.replace(path)


def sample_time_y_x(
    dataset: netCDF4.Dataset,
    name: str,
    time_index: int,
    y_indices: np.ndarray,
    x_indices: np.ndarray,
) -> np.ndarray:
    variable = dataset.variables[name]
    if "time" not in variable.dimensions:
        raise ValueError(f"{name!r} has no time dimension")
    selection: list[object] = [slice(None)] * variable.ndim
    selection[variable.dimensions.index("time")] = time_index
    values = np.ma.asarray(variable[tuple(selection)])
    if np.ma.count_masked(values):
        values = values.filled(np.nan)
    values = np.squeeze(np.asarray(values, dtype=np.float64))
    if values.ndim != 2:
        raise ValueError(f"{name!r} is not a two-dimensional surface field")
    return values[y_indices, x_indices]


def validate_output_contract(dataset: netCDF4.Dataset, path: Path) -> None:
    missing = sorted(set(HOURLY_VARIABLES) - set(dataset.variables))
    if missing:
        raise ValueError(f"HICAR wind-climatology output {path} lacks {missing}")
    if "height_agl" not in dataset.variables:
        raise ValueError(f"HICAR wind-climatology output {path} lacks height_agl")
    heights = np.asarray(dataset.variables["height_agl"][:], dtype=float)
    if heights.shape != EXPECTED_HEIGHTS_AGL_M.shape or not np.allclose(
        heights, EXPECTED_HEIGHTS_AGL_M, rtol=0.0, atol=1.0e-6
    ):
        raise ValueError(
            f"HICAR wind-climatology output {path} has unexpected AGL heights {heights}"
        )
    for name in HOURLY_VARIABLES:
        cell_methods = getattr(dataset.variables[name], "cell_methods", "")
        if "time:" not in cell_methods:
            raise ValueError(f"{path} variable {name!r} lacks temporal cell_methods")


def new_statistics() -> dict:
    return {
        "scalar_speed": PairStatistics(),
        "u_component": PairStatistics(),
        "v_component": PairStatistics(),
        "direction": CircularStatistics(),
        "vector_squared_error_sum": 0.0,
        "vector_count": 0,
        "ten_minute_max_count": 0,
        "ten_minute_max_sum": 0.0,
        "ten_minute_max_min": math.inf,
        "ten_minute_max_max": -math.inf,
        "ten_minute_max_ge_hourly_count": 0,
    }


def add_pair(
    statistics: dict,
    model_speed: float,
    model_u: float,
    model_v: float,
    model_direction: float,
    ten_minute_max: float,
    observation: dict[str, float],
) -> None:
    observed_speed = observation["wind_speed_10m_m_s"]
    observed_u = observation["u_wind_10m_m_s"]
    observed_v = observation["v_wind_10m_m_s"]
    statistics["scalar_speed"].add(model_speed, observed_speed)
    statistics["u_component"].add(model_u, observed_u)
    statistics["v_component"].add(model_v, observed_v)
    statistics["vector_squared_error_sum"] += (
        (model_u - observed_u) ** 2 + (model_v - observed_v) ** 2
    )
    statistics["vector_count"] += 1
    if observed_speed >= WIND_DIRECTION_OBSERVATION_THRESHOLD_M_S:
        statistics["direction"].add(
            model_direction, observation["wind_direction_degrees"]
        )
    if math.isfinite(ten_minute_max):
        statistics["ten_minute_max_count"] += 1
        statistics["ten_minute_max_sum"] += ten_minute_max
        statistics["ten_minute_max_min"] = min(
            statistics["ten_minute_max_min"], ten_minute_max
        )
        statistics["ten_minute_max_max"] = max(
            statistics["ten_minute_max_max"], ten_minute_max
        )
        statistics["ten_minute_max_ge_hourly_count"] += int(
            ten_minute_max + 1.0e-6 >= model_speed
        )


def statistics_result(statistics: dict) -> dict:
    vector_count = statistics["vector_count"]
    maximum_count = statistics["ten_minute_max_count"]
    return {
        "scalar_mean_speed_m_s": statistics["scalar_speed"].result(),
        "direction_degrees": statistics["direction"].result(),
        "u_component_m_s": statistics["u_component"].result(),
        "v_component_m_s": statistics["v_component"].result(),
        "wind_vector": {
            "count": vector_count,
            "vector_root_mean_squared_error_m_s": math.sqrt(
                statistics["vector_squared_error_sum"] / vector_count
            )
            if vector_count
            else None,
        },
        "maximum_of_ten_minute_means_m_s": {
            "count": maximum_count,
            "mean": statistics["ten_minute_max_sum"] / maximum_count
            if maximum_count
            else None,
            "minimum": statistics["ten_minute_max_min"] if maximum_count else None,
            "maximum": statistics["ten_minute_max_max"] if maximum_count else None,
            "ge_hourly_scalar_mean_count": statistics[
                "ten_minute_max_ge_hourly_count"
            ],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--static-file", type=Path, required=True)
    parser.add_argument("--output-file", type=Path, action="append", required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--minimum-pairs", type=int, default=1)
    parser.add_argument("--maximum-land-cell-displacement-km", type=float, default=1.0)
    parser.add_argument(
        "--overlap-policy", choices=("error", "last"), default="error"
    )
    args = parser.parse_args()

    sites_by_key, observations, observation_inventory = read_wind_observations(
        args.observations
    )
    sites = sorted(sites_by_key.values(), key=lambda site: site.key)
    with netCDF4.Dataset(args.static_file) as static:
        latitude = read_2d(static, "lat")
        longitude = read_2d(static, "lon")
        terrain = read_2d(static, "topo")
        landmask = read_2d(static, "landmask")
        dx_m = float(getattr(static, "hicar_dx_m", 200.0))

    grid_sine, grid_cosine = hicar_grid_rotation(latitude, longitude, dx_m=dx_m)
    unconstrained_y, unconstrained_x, unconstrained_distance = nearest_hicar_cells(
        latitude, longitude, sites
    )
    maximum_distance_km = max(1.0, 3.0 * dx_m / 1000.0)
    sites, unconstrained_y, unconstrained_x, unconstrained_distance, excluded_sites = (
        select_sites_by_distance(
            sites,
            unconstrained_y,
            unconstrained_x,
            unconstrained_distance,
            maximum_distance_km,
        )
    )
    mapping = nearest_hicar_land_cells(
        latitude,
        longitude,
        landmask,
        sites,
        unconstrained_y,
        unconstrained_x,
        unconstrained_distance,
        dx_m,
        args.maximum_land_cell_displacement_km,
    )
    sampled_sine = grid_sine[mapping.y_indices, mapping.x_indices]
    sampled_cosine = grid_cosine[mapping.y_indices, mapping.x_indices]

    datasets: list[netCDF4.Dataset] = []
    records: dict[datetime, tuple[netCDF4.Dataset, int, Path]] = {}
    overlap_times: list[str] = []
    try:
        for path in args.output_file:
            dataset = netCDF4.Dataset(path)
            datasets.append(dataset)
            validate_output_contract(dataset, path)
            times = decoded_times(dataset)
            if any(time.minute or time.second or time.microsecond for time in times):
                raise ValueError(f"HICAR wind-climatology output {path} is not hourly")
            for index, valid in enumerate(times):
                if valid in records:
                    if args.overlap_policy == "error":
                        raise ValueError(f"duplicate HICAR time {valid.isoformat()}")
                    overlap_times.append(valid.isoformat())
                records[valid] = (dataset, index, path)

        all_statistics = new_statistics()
        seasonal = {name: new_statistics() for name in ("DJF", "MAM", "JJA", "SON")}
        by_site = {site.key: new_statistics() for site in sites}
        accounting = Counter()
        matched_times: set[datetime] = set()
        for valid in sorted(records):
            dataset, index, _ = records[valid]
            grid_u = sample_time_y_x(
                dataset, "u10m_mean_1h", index, mapping.y_indices, mapping.x_indices
            )
            grid_v = sample_time_y_x(
                dataset, "v10m_mean_1h", index, mapping.y_indices, mapping.x_indices
            )
            scalar_speed = sample_time_y_x(
                dataset,
                "wind_speed_10m_mean_1h",
                index,
                mapping.y_indices,
                mapping.x_indices,
            )
            maximum_speed = sample_time_y_x(
                dataset,
                "wind_speed_10m_10min_max_1h",
                index,
                mapping.y_indices,
                mapping.x_indices,
            )
            if not np.any(
                np.isfinite(grid_u)
                & np.isfinite(grid_v)
                & np.isfinite(scalar_speed)
                & np.isfinite(maximum_speed)
            ):
                accounting["cold_start_or_partial_hour_record_count"] += 1
                continue
            earth_u, earth_v = grid_to_earth_wind(
                grid_u, grid_v, sampled_sine, sampled_cosine
            )
            vector_speed = np.hypot(earth_u, earth_v)
            comparison_u = np.divide(
                earth_u * scalar_speed,
                vector_speed,
                out=np.zeros_like(earth_u),
                where=vector_speed > 0.0,
            )
            comparison_v = np.divide(
                earth_v * scalar_speed,
                vector_speed,
                out=np.zeros_like(earth_v),
                where=vector_speed > 0.0,
            )
            direction = (270.0 - np.degrees(np.arctan2(comparison_v, comparison_u))) % 360.0

            for site_index, site in enumerate(sites):
                accounting["candidate_station_time_count"] += 1
                observation = observation_values(
                    observations.get(valid, {}), site.key
                )
                model_values = (
                    scalar_speed[site_index],
                    comparison_u[site_index],
                    comparison_v[site_index],
                    direction[site_index],
                )
                if "wind_speed_10m_m_s" not in observation:
                    accounting["observation_missing_or_rejected"] += 1
                    continue
                if not all(math.isfinite(float(value)) for value in model_values):
                    accounting["model_missing_or_nonfinite"] += 1
                    continue
                accounting["accepted_pair_count"] += 1
                matched_times.add(valid)
                for target in (
                    all_statistics,
                    seasonal[climatological_season(valid)],
                    by_site[site.key],
                ):
                    add_pair(
                        target,
                        float(scalar_speed[site_index]),
                        float(comparison_u[site_index]),
                        float(comparison_v[site_index]),
                        float(direction[site_index]),
                        float(maximum_speed[site_index]),
                        observation,
                    )
    finally:
        for dataset in datasets:
            dataset.close()

    failures = []
    if all_statistics["scalar_speed"].count < args.minimum_pairs:
        failures.append(
            f"only {all_statistics['scalar_speed'].count} wind pairs; "
            f"minimum is {args.minimum_pairs}"
        )
    report = {
        "schema_version": 1,
        "status": "pass" if not failures else "fail",
        "failures": failures,
        "interpretation": (
            "HICAR timestep-weighted ending-hour scalar wind speed and vector-mean "
            "direction at 10 m versus quality-controlled SwissMetNet fkl010h0 and "
            "dkl010h0. The maximum field is the maximum of six ten-minute scalar "
            "means; it is a diagnostic, not an instantaneous gust."
        ),
        "input_contract": {
            "required_hourly_variables": list(HOURLY_VARIABLES),
            "height_agl_m": EXPECTED_HEIGHTS_AGL_M.tolist(),
            "cold_start_policy": (
                "the initial record is expected to be missing because no preceding "
                "hour exists; missing records are excluded explicitly"
            ),
            "restart_overlap_policy": args.overlap_policy,
        },
        "observation_inventory": observation_inventory,
        "pair_accounting": dict(accounting),
        "matched_model_times": [value.isoformat() for value in sorted(matched_times)],
        "overlap_times_replaced_by_later_file": overlap_times,
        "statistics": statistics_result(all_statistics),
        "seasonal_statistics": {
            name: statistics_result(values) for name, values in seasonal.items()
        },
        "site_statistics": {
            name: statistics_result(values) for name, values in by_site.items()
        },
        "station_mapping": {
            "site_count": len(sites),
            "excluded_outside_domain_sites": excluded_sites,
            "maximum_domain_distance_km": maximum_distance_km,
            "maximum_land_cell_displacement_km": args.maximum_land_cell_displacement_km,
            "sites": [
                {
                    "key": site.key,
                    "hicar_y_index": int(mapping.y_indices[index]),
                    "hicar_x_index": int(mapping.x_indices[index]),
                    "hicar_elevation_m": float(
                        terrain[mapping.y_indices[index], mapping.x_indices[index]]
                    ),
                    "selected_cell_distance_km": float(mapping.distances_km[index]),
                    "surface_mapping_displacement_km": float(
                        mapping.displacement_km[index]
                    ),
                }
                for index, site in enumerate(sites)
            ],
        },
    }
    atomic_json_dump(args.report, report)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
