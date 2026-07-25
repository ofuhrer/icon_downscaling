#!/usr/bin/env python3
"""Compare three-hourly HICAR event output with its REA-L surface driver."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
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

    def add(self, model: np.ndarray, reference: np.ndarray, mask: np.ndarray) -> int:
        valid = mask & np.isfinite(model) & np.isfinite(reference)
        if not np.any(valid):
            return int(np.count_nonzero(mask))
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
        self.sum_reference_squared += float(
            np.sum(reference_values * reference_values)
        )
        self.sum_product += float(np.sum(model_values * reference_values))
        return int(np.count_nonzero(mask) - len(error))

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
        denominator = np.sqrt(max(model_variance * reference_variance, 0.0))
        correlation = covariance / denominator if denominator > 0.0 else None
        return {
            "count": self.count,
            "model_mean": self.sum_model / count,
            "reference_mean": self.sum_reference / count,
            "bias": self.sum_error / count,
            "mean_absolute_error": self.sum_abs_error / count,
            "root_mean_squared_error": np.sqrt(self.sum_squared_error / count),
            "correlation": correlation,
        }


def read_array(dataset: netCDF4.Dataset, name: str, index=None) -> np.ndarray:
    variable = dataset.variables[name]
    values = variable[:] if index is None else variable[index]
    values = np.squeeze(np.ma.asarray(values))
    if np.ma.count_masked(values):
        values = values.filled(np.nan)
    return np.asarray(values, dtype=np.float64)


def decoded_times(dataset: netCDF4.Dataset) -> list[datetime]:
    variable = dataset.variables["time"]
    values = netCDF4.num2date(
        variable[:],
        units=variable.units,
        calendar=getattr(variable, "calendar", "standard"),
        only_use_cftime_datetimes=False,
        only_use_python_datetimes=True,
    )
    decoded = []
    for value in values:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        else:
            value = value.astimezone(timezone.utc)
        # HICAR's encoded day coordinate carries a known ~0.432 s
        # representation offset. Output cadence is integral-second, so
        # canonicalize only sub-second offsets before exact record matching.
        without_microseconds = value.replace(microsecond=0)
        if abs((value - without_microseconds).total_seconds()) <= 1.0:
            value = without_microseconds
        decoded.append(value)
    return decoded


def bilinear_setup(
    latitude: np.ndarray,
    longitude: np.ndarray,
    target_latitude: np.ndarray,
    target_longitude: np.ndarray,
) -> tuple[np.ndarray, ...]:
    if latitude[0] > latitude[-1]:
        latitude = latitude[::-1]
        latitude_reversed = True
    else:
        latitude_reversed = False
    if longitude[0] > longitude[-1]:
        longitude = longitude[::-1]
        longitude_reversed = True
    else:
        longitude_reversed = False
    outside = (
        (target_latitude < latitude[0])
        | (target_latitude > latitude[-1])
        | (target_longitude < longitude[0])
        | (target_longitude > longitude[-1])
    )
    if np.any(outside):
        raise ValueError(
            f"{int(np.count_nonzero(outside))} HICAR cells are outside "
            "the REA-L reference grid"
        )
    iy1 = np.searchsorted(latitude, target_latitude, side="right")
    ix1 = np.searchsorted(longitude, target_longitude, side="right")
    iy1 = np.clip(iy1, 1, len(latitude) - 1).astype(np.int32)
    ix1 = np.clip(ix1, 1, len(longitude) - 1).astype(np.int32)
    iy0 = iy1 - 1
    ix0 = ix1 - 1
    wy = (
        (target_latitude - latitude[iy0]) / (latitude[iy1] - latitude[iy0])
    ).astype(np.float32)
    wx = (
        (target_longitude - longitude[ix0]) / (longitude[ix1] - longitude[ix0])
    ).astype(np.float32)
    return iy0, iy1, ix0, ix1, wy, wx, latitude_reversed, longitude_reversed


def bilinear(values: np.ndarray, setup: tuple[np.ndarray, ...]) -> np.ndarray:
    iy0, iy1, ix0, ix1, wy, wx, reverse_y, reverse_x = setup
    if reverse_y:
        values = values[::-1, :]
    if reverse_x:
        values = values[:, ::-1]
    return (
        values[iy0, ix0] * (1.0 - wy) * (1.0 - wx)
        + values[iy1, ix0] * wy * (1.0 - wx)
        + values[iy0, ix1] * (1.0 - wy) * wx
        + values[iy1, ix1] * wy * wx
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--static-file", type=Path, required=True)
    parser.add_argument("--output-file", type=Path, action="append", required=True)
    parser.add_argument("--reference-list", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--boundary-width-m", type=float, default=10_000.0)
    parser.add_argument("--temperature-lapse-rate-k-m", type=float, default=-0.0065)
    args = parser.parse_args()

    failures: list[str] = []
    with netCDF4.Dataset(args.static_file) as static:
        target_latitude = read_array(static, "lat")
        target_longitude = read_array(static, "lon")
        target_terrain = read_array(static, "topo")
        landmask = read_array(static, "landmask") == 1
        landuse = read_array(static, "landuse")
        dx = float(getattr(static, "hicar_dx_m", 200.0))
    active = landmask & (landuse != 24)
    boundary_cells = int(round(args.boundary_width_m / dx))
    interior = np.zeros_like(active)
    if 2 * boundary_cells < min(active.shape):
        interior[
            boundary_cells:-boundary_cells, boundary_cells:-boundary_cells
        ] = True
        interior &= active
    else:
        interior = active.copy()
    masks = {
        "active_soil_all": active,
        "active_soil_interior": interior,
        "elevation_lt_500m": active & (target_terrain < 500.0),
        "elevation_500_1000m": active
        & (target_terrain >= 500.0)
        & (target_terrain < 1000.0),
        "elevation_1000_1500m": active
        & (target_terrain >= 1000.0)
        & (target_terrain < 1500.0),
        "elevation_1500_2000m": active
        & (target_terrain >= 1500.0)
        & (target_terrain < 2000.0),
        "elevation_2000_3000m": active
        & (target_terrain >= 2000.0)
        & (target_terrain < 3000.0),
        "elevation_ge_3000m": active & (target_terrain >= 3000.0),
    }

    reference_paths = [
        Path(line.strip().strip('"'))
        for line in args.reference_list.read_text().splitlines()
        if line.strip()
    ]
    if not reference_paths:
        raise SystemExit("reference list is empty")

    metric_names = (
        "temperature_2m_raw_k",
        "temperature_2m_height_adjusted_k",
        "specific_humidity_2m",
        "surface_pressure_pa",
        "u_wind_10m_m_s",
        "v_wind_10m_m_s",
        "wind_speed_10m_m_s",
        "snow_height_m",
        "swe_kg_m2",
        "precipitation_interval_kg_m2",
    )
    statistics = {
        class_name: {name: Statistics() for name in metric_names}
        for class_name in masks
    }
    nonfinite = {
        class_name: {name: 0 for name in metric_names} for class_name in masks
    }
    references = []

    with ExitStack() as stack:
        outputs = [
            stack.enter_context(netCDF4.Dataset(path)) for path in args.output_file
        ]
        model_records: dict[datetime, tuple[netCDF4.Dataset, int]] = {}
        for dataset in outputs:
            for index, valid in enumerate(decoded_times(dataset)):
                if valid in model_records:
                    failures.append(f"duplicate HICAR output time {valid.isoformat()}")
                model_records[valid] = (dataset, index)

        previous_precipitation = None
        interpolation_setup = None
        interpolated_source_terrain = None
        for reference_index, path in enumerate(reference_paths):
            reference = stack.enter_context(netCDF4.Dataset(path))
            valid = decoded_times(reference)[0]
            if valid not in model_records:
                failures.append(f"missing HICAR record for {valid.isoformat()}")
                continue
            model, model_index = model_records[valid]
            if interpolation_setup is None:
                source_latitude = read_array(reference, "latitude")
                source_longitude = read_array(reference, "longitude")
                interpolation_setup = bilinear_setup(
                    source_latitude,
                    source_longitude,
                    target_latitude,
                    target_longitude,
                )
                interpolated_source_terrain = bilinear(
                    read_array(reference, "source_terrain", 0),
                    interpolation_setup,
                )

            source = {
                name: bilinear(read_array(reference, variable, 0), interpolation_setup)
                for name, variable in (
                    ("temperature_2m_raw_k", "ta2m_ref"),
                    ("specific_humidity_2m", "hus2m_ref"),
                    ("surface_pressure_pa", "psfc_ref"),
                    ("u_wind_10m_m_s", "u10m_ref"),
                    ("v_wind_10m_m_s", "v10m_ref"),
                    ("snow_height_m", "snow_height_ref"),
                    ("swe_kg_m2", "swe_ref"),
                    (
                        "precipitation_interval_kg_m2",
                        "precipitation_interval_ref",
                    ),
                )
            }
            assert interpolated_source_terrain is not None
            source["temperature_2m_height_adjusted_k"] = (
                source["temperature_2m_raw_k"]
                + args.temperature_lapse_rate_k_m
                * (target_terrain - interpolated_source_terrain)
            )
            source["wind_speed_10m_m_s"] = np.hypot(
                source["u_wind_10m_m_s"], source["v_wind_10m_m_s"]
            )
            model_values = {
                "temperature_2m_raw_k": read_array(model, "taix", model_index),
                "temperature_2m_height_adjusted_k": read_array(
                    model, "taix", model_index
                ),
                "specific_humidity_2m": read_array(model, "hus2m", model_index),
                "surface_pressure_pa": read_array(model, "psfc", model_index),
                "u_wind_10m_m_s": read_array(model, "u10m", model_index),
                "v_wind_10m_m_s": read_array(model, "v10m", model_index),
                "snow_height_m": read_array(model, "snow_height", model_index),
                "swe_kg_m2": read_array(model, "swet", model_index),
            }
            model_values["wind_speed_10m_m_s"] = np.hypot(
                model_values["u_wind_10m_m_s"],
                model_values["v_wind_10m_m_s"],
            )
            accumulated_precipitation = read_array(
                model, "precipitation", model_index
            )
            if previous_precipitation is not None:
                model_values["precipitation_interval_kg_m2"] = (
                    accumulated_precipitation - previous_precipitation
                )
            previous_precipitation = accumulated_precipitation

            record_metrics = [
                name
                for name in metric_names
                if name != "precipitation_interval_kg_m2" or reference_index > 0
            ]
            for class_name, mask in masks.items():
                for name in record_metrics:
                    nonfinite[class_name][name] += statistics[class_name][name].add(
                        model_values[name], source[name], mask
                    )
            references.append(
                {
                    "valid_time": valid.isoformat(),
                    "path": str(path.resolve()),
                    "sha256": sha256(path),
                }
            )

    for class_name, counts in nonfinite.items():
        for name, count in counts.items():
            if count:
                failures.append(
                    f"{class_name}/{name} has {count} non-finite comparison pairs"
                )
    report = {
        "schema_version": 1,
        "status": "FAIL" if failures else "PASS",
        "event_name": args.event_name,
        "interpretation": (
            "Source-consistency comparison against the 1 km REA-L driver; "
            "it is not independent validation and is not a downscaling skill score."
        ),
        "temperature_height_adjustment": {
            "formula": "T_ref_adjusted=T_ref+lapse_rate*(H_HICAR-H_REA-L)",
            "lapse_rate_k_m": args.temperature_lapse_rate_k_m,
        },
        "mask_contract": {
            "boundary_width_m": args.boundary_width_m,
            "grid_spacing_m": dx,
            "cells": {
                name: int(np.count_nonzero(mask)) for name, mask in masks.items()
            },
        },
        "source_plan": str(args.reference_list.resolve()),
        "reference_records": references,
        "hicar_outputs": [
            {
                "path": str(path.resolve()),
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for path in args.output_file
        ],
        "metrics": {
            class_name: {
                name: statistic.result()
                for name, statistic in class_statistics.items()
            }
            for class_name, class_statistics in statistics.items()
        },
        "nonfinite_pairs": nonfinite,
        "failures": failures,
    }
    write_json_atomic(args.report, report)
    if failures:
        return 1
    Path(f"{args.report}.ready").touch()
    print(f"PASS: compared {len(references)} REA-L reference records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
