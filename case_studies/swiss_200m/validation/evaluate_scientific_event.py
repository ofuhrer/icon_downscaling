#!/usr/bin/env python3
"""Evaluate one HICAR scientific event over class-aware Swiss surface masks.

Production water closure uses restart-persistent cumulative precipitation,
runoff, and signed net evaporation.  Consecutive cumulative records define
exact ``(previous_time, time]`` amounts without sampling sub-output-interval
peaks.  A separately selected legacy mode reconstructs old runoff snapshots
and is always labelled approximate and ineligible for promotion.

The surface-energy residual uses the sign conventions documented by HICAR:

    (1-albedo)*rsds + lwtr - rlus - hfss - hfls - hfgs

where radiative inputs and ground heat into the surface are positive down,
and turbulent and longwave outputs are positive up.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Iterable

import netCDF4
import numpy as np


LATENT_HEAT_VAPORIZATION_J_KG = 2.5e6
GRID_SPACING_M = 200.0
BOUNDARY_WIDTH_M = 10_000.0
PRECIP_DECREASE_TOLERANCE_KG_M2 = 1.0e-6
CUMULATIVE_DECREASE_TOLERANCE_KG_M2 = 1.0e-6
SOIL_LAYER_THICKNESS_M = np.asarray([0.1, 0.2, 0.4, 0.8], dtype=np.float64)
SOIL_COLUMN_CROSSCHECK_TOLERANCE_KG_M2 = 1.0e-2

BASE_REQUIRED_VARIABLES = (
    "precipitation",
    "taix",
    "hus2m",
    "u10m",
    "v10m",
    "rsds",
    "lwtr",
    "rlus",
    "hfgs",
    "hfss",
    "hfls",
    "albedo",
    "canopy_water",
    "swet",
    "snow_height",
    "soil_column_total_water",
    "soil_water_content",
    "soil_temperature",
)
LEGACY_WATER_VARIABLES = (
    "runoff_surface",
    "runoff_subsurface",
)
PRODUCTION_WATER_VARIABLES = (
    "runoff_surface_cumulative",
    "runoff_subsurface_cumulative",
    "evaporation_net_cumulative",
    "water_aquifer",
    "storage_gw",
    "wetland_h20_store",
)

AMOUNT_UNITS = {"kg m-2", "kg m^-2", "mm"}
EXPECTED_ACCUMULATION_SEMANTICS = (
    "cumulative since simulation start; no output reset; restart-persistent"
)


@dataclass(frozen=True)
class Record:
    time: datetime
    path: Path
    index: int


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


def _as_2d(dataset: netCDF4.Dataset, name: str) -> np.ndarray:
    values = np.squeeze(np.asarray(dataset.variables[name][:]))
    if values.ndim != 2:
        raise ValueError(f"{name} must reduce to 2-D, got {values.shape}")
    return values


def _records(paths: Iterable[Path]) -> list[Record]:
    records: list[Record] = []
    for path in paths:
        with netCDF4.Dataset(path) as dataset:
            time_variable = dataset.variables["time"]
            decoded = netCDF4.num2date(
                time_variable[:],
                time_variable.units,
                calendar=getattr(time_variable, "calendar", "standard"),
            )
            for index, value in enumerate(decoded):
                records.append(
                    Record(
                        time=datetime(
                            value.year,
                            value.month,
                            value.day,
                            value.hour,
                            value.minute,
                            value.second,
                        ),
                        path=path,
                        index=index,
                    )
                )
    records.sort(key=lambda item: item.time)
    return records


def _read_record(dataset: netCDF4.Dataset, name: str, index: int) -> np.ndarray:
    variable = dataset.variables[name]
    values = np.ma.asarray(variable[index, ...])
    if np.ma.count_masked(values):
        return np.asarray(values.filled(np.nan), dtype=np.float64)
    return np.asarray(values, dtype=np.float64)


def _attribute_text(variable: netCDF4.Variable, name: str) -> str:
    return str(getattr(variable, name, "")).strip()


def _validate_production_metadata(
    dataset: netCDF4.Dataset,
    path: Path,
    failures: list[str],
) -> None:
    for name in ("precipitation", *PRODUCTION_WATER_VARIABLES[:3]):
        variable = dataset.variables[name]
        units = _attribute_text(variable, "units")
        if units not in AMOUNT_UNITS:
            failures.append(
                f"{path} {name} units are {units!r}, expected a water amount"
            )
        semantics = _attribute_text(variable, "accumulation_semantics")
        if semantics != EXPECTED_ACCUMULATION_SEMANTICS:
            failures.append(
                f"{path} {name} lacks the frozen no-reset, restart-persistent "
                "accumulation semantics"
            )
        interval = _attribute_text(variable, "interval_semantics")
        if "(previous_time, time]" not in interval:
            failures.append(
                f"{path} {name} lacks exact consecutive-record interval semantics"
            )
    for name in PRODUCTION_WATER_VARIABLES[3:]:
        units = _attribute_text(dataset.variables[name], "units")
        if units not in AMOUNT_UNITS:
            failures.append(
                f"{path} {name} units are {units!r}, expected a water amount"
            )


def _mean(values: np.ndarray, mask: np.ndarray) -> float:
    if values.shape[-2:] != mask.shape:
        raise ValueError(f"shape {values.shape} does not match mask {mask.shape}")
    selected = values[..., mask]
    return float(np.mean(selected))


def _sample_summary(
    values: np.ndarray, mask: np.ndarray, maximum_samples: int
) -> dict[str, float | int]:
    selected = np.asarray(values[..., mask], dtype=np.float64).reshape(-1)
    if selected.size > maximum_samples:
        stride = int(np.ceil(selected.size / maximum_samples))
        sampled = selected[::stride]
    else:
        stride = 1
        sampled = selected
    quantiles = np.percentile(sampled, [1.0, 5.0, 50.0, 95.0, 99.0])
    return {
        "count": int(selected.size),
        "sample_count": int(sampled.size),
        "sample_stride": stride,
        "minimum": float(np.min(selected)),
        "p01": float(quantiles[0]),
        "p05": float(quantiles[1]),
        "median": float(quantiles[2]),
        "p95": float(quantiles[3]),
        "p99": float(quantiles[4]),
        "maximum": float(np.max(selected)),
        "mean": float(np.mean(selected)),
    }


def _linear_slope_per_day(times: list[datetime], values: list[float]) -> float:
    if len(times) < 2:
        return 0.0
    seconds = np.asarray([(value - times[0]).total_seconds() for value in times])
    centered = seconds - np.mean(seconds)
    denominator = float(np.sum(centered * centered))
    if denominator == 0.0:
        return 0.0
    slope_per_second = float(
        np.sum(centered * (np.asarray(values) - np.mean(values))) / denominator
    )
    return slope_per_second * 86400.0


def _integrate_trapezoid(
    times: list[datetime], values: list[float], scale: float = 1.0
) -> float:
    total = 0.0
    for left_time, right_time, left, right in zip(
        times, times[1:], values, values[1:]
    ):
        total += (
            0.5
            * (left + right)
            * (right_time - left_time).total_seconds()
            * scale
        )
    return float(total)


def _surface_masks(static_file: Path) -> tuple[dict[str, np.ndarray], dict]:
    with netCDF4.Dataset(static_file) as static:
        landmask = _as_2d(static, "landmask") > 0
        landuse = _as_2d(static, "landuse").astype(np.int64)
        terrain = _as_2d(static, "topo")

    active_soil = landmask & (landuse != 16) & (landuse != 24)
    ny, nx = landmask.shape
    y, x = np.indices((ny, nx))
    edge_cells = np.minimum.reduce((y, x, ny - 1 - y, nx - 1 - x))
    boundary = edge_cells * GRID_SPACING_M < BOUNDARY_WIDTH_M

    masks = {
        "active_soil_all": active_soil,
        "active_soil_boundary_10km": active_soil & boundary,
        "active_soil_interior": active_soil & ~boundary,
        "elevation_lt_500m": active_soil & (terrain < 500.0),
        "elevation_500_1000m": active_soil
        & (terrain >= 500.0)
        & (terrain < 1000.0),
        "elevation_1000_1500m": active_soil
        & (terrain >= 1000.0)
        & (terrain < 1500.0),
        "elevation_1500_2000m": active_soil
        & (terrain >= 1500.0)
        & (terrain < 2000.0),
        "elevation_2000_3000m": active_soil
        & (terrain >= 2000.0)
        & (terrain < 3000.0),
        "elevation_ge_3000m": active_soil & (terrain >= 3000.0),
    }
    metadata = {
        "grid_spacing_m": GRID_SPACING_M,
        "boundary_width_m": BOUNDARY_WIDTH_M,
        "raw_land_cells": int(np.count_nonzero(landmask)),
        "usgs_water_16_cells": int(np.count_nonzero(landmask & (landuse == 16))),
        "usgs_snow_ice_24_cells": int(np.count_nonzero(landmask & (landuse == 24))),
        "classes": {
            name: int(np.count_nonzero(mask)) for name, mask in masks.items()
        },
    }
    if not np.any(active_soil):
        raise ValueError("active-soil mask is empty")
    return masks, metadata


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--static-file", type=Path, required=True)
    parser.add_argument("--output-file", type=Path, required=True, nargs="+")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--expected-start")
    parser.add_argument("--expected-end")
    parser.add_argument("--expected-interval-seconds", type=int, default=10800)
    parser.add_argument("--maximum-percentile-samples", type=int, default=250_000)
    parser.add_argument(
        "--legacy-runoff-timestep-seconds",
        type=float,
        help=(
            "Explicitly enable representativeness-limited reconstruction for "
            "preserved outputs whose runoff fields are last-soil-step amounts. "
            "This mode can never satisfy a production promotion gate."
        ),
    )
    args = parser.parse_args()

    failures: list[str] = []
    legacy_mode = args.legacy_runoff_timestep_seconds is not None
    if legacy_mode and args.legacy_runoff_timestep_seconds <= 0:
        failures.append("legacy runoff timestep must be positive")
    required_variables = (
        BASE_REQUIRED_VARIABLES
        + (LEGACY_WATER_VARIABLES if legacy_mode else PRODUCTION_WATER_VARIABLES)
    )
    records = _records(args.output_file)
    times = [record.time for record in records]
    if not records:
        failures.append("no output records")
    if times != sorted(set(times)):
        failures.append("output times are not unique and strictly ordered")
    if args.expected_start and (
        not times or times[0] != datetime.fromisoformat(args.expected_start)
    ):
        failures.append("first output time does not match expected start")
    if args.expected_end and (
        not times or times[-1] != datetime.fromisoformat(args.expected_end)
    ):
        failures.append("last output time does not match expected end")
    if args.expected_interval_seconds <= 0:
        failures.append("expected interval must be positive")
    elif any(
        (right - left).total_seconds() != args.expected_interval_seconds
        for left, right in zip(times, times[1:])
    ):
        failures.append("output time interval is not uniform and expected")

    masks, mask_metadata = _surface_masks(args.static_file)
    series: dict[str, dict[str, list[float]]] = {
        name: {
            "precipitation_cumulative_kg_m2": [],
            "evaporation_net_cumulative_kg_m2": [],
            "runoff_surface_cumulative_kg_m2": [],
            "runoff_subsurface_cumulative_kg_m2": [],
            "evaporation_rate_kg_m2_s": [],
            "runoff_sample_rate_kg_m2_s": [],
            "runoff_surface_sample_rate_kg_m2_s": [],
            "runoff_subsurface_sample_rate_kg_m2_s": [],
            "water_store_kg_m2": [],
            "soil_water_kg_m2": [],
            "soil_water_reported_kg_m2": [],
            "swe_kg_m2": [],
            "canopy_water_kg_m2": [],
            "water_aquifer_kg_m2": [],
            "storage_gw_diagnostic_kg_m2": [],
            "wetland_water_kg_m2": [],
            "surface_energy_residual_w_m2": [],
            "temperature_2m_k": [],
            "wind_speed_10m_m_s": [],
            "snow_height_m": [],
        }
        for name in masks
    }
    first_fields: dict[str, np.ndarray] = {}
    last_fields: dict[str, np.ndarray] = {}
    previous_precipitation: np.ndarray | None = None
    previous_surface_runoff: np.ndarray | None = None
    previous_subsurface_runoff: np.ndarray | None = None
    precipitation_decrease_cells = 0
    surface_runoff_decrease_cells = 0
    subsurface_runoff_decrease_cells = 0
    nonfinite_counts: dict[str, int] = {name: 0 for name in required_variables}
    soil_column_crosscheck: list[dict[str, float | str]] = []
    validated_metadata_files: set[Path] = set()

    current_path: Path | None = None
    dataset: netCDF4.Dataset | None = None
    current_file_valid = False
    try:
        for record_index, record in enumerate(records):
            if record.path != current_path:
                if dataset is not None:
                    dataset.close()
                dataset = netCDF4.Dataset(record.path)
                current_path = record.path
                missing = [
                    name for name in required_variables if name not in dataset.variables
                ]
                if missing:
                    failures.append(f"{record.path} misses {','.join(missing)}")
                    current_file_valid = False
                    continue
                current_file_valid = True
                if not legacy_mode and record.path not in validated_metadata_files:
                    _validate_production_metadata(dataset, record.path, failures)
                    validated_metadata_files.add(record.path)

            if not current_file_valid:
                continue
            assert dataset is not None
            fields = {
                name: _read_record(dataset, name, record.index)
                for name in required_variables
                if name not in ("soil_water_content", "soil_temperature")
            }
            soil_water_content = _read_record(
                dataset, "soil_water_content", record.index
            )
            soil_temperature = _read_record(dataset, "soil_temperature", record.index)
            if soil_water_content.shape[0] != len(SOIL_LAYER_THICKNESS_M):
                failures.append(
                    f"{record.path} soil_water_content has "
                    f"{soil_water_content.shape[0]} layers, expected "
                    f"{len(SOIL_LAYER_THICKNESS_M)}"
                )
                continue
            calculated_soil_column = np.sum(
                soil_water_content
                * SOIL_LAYER_THICKNESS_M[:, np.newaxis, np.newaxis]
                * 1000.0,
                axis=0,
            )
            for name, values in fields.items():
                count = int(np.count_nonzero(~np.isfinite(values[..., masks["active_soil_all"]])))
                nonfinite_counts[name] += count
            nonfinite_counts["soil_water_content"] += int(
                np.count_nonzero(
                    ~np.isfinite(
                        soil_water_content[..., masks["active_soil_all"]]
                    )
                )
            )
            nonfinite_counts["soil_temperature"] += int(
                np.count_nonzero(
                    ~np.isfinite(soil_temperature[..., masks["active_soil_all"]])
                )
            )
            column_difference_2d = (
                fields["soil_column_total_water"] - calculated_soil_column
            )
            soil_column_crosscheck.append(
                {
                    "time": record.time.isoformat(),
                    "classes": {
                        class_name: {
                            "mean_absolute_difference_kg_m2": float(
                                np.mean(np.abs(column_difference_2d[mask]))
                            ),
                            "maximum_absolute_difference_kg_m2": float(
                                np.max(np.abs(column_difference_2d[mask]))
                            ),
                            "cells_above_tolerance": int(
                                np.count_nonzero(
                                    np.abs(column_difference_2d[mask])
                                    > SOIL_COLUMN_CROSSCHECK_TOLERANCE_KG_M2
                                )
                            ),
                        }
                        for class_name, mask in masks.items()
                        if np.any(mask)
                    },
                }
            )

            precipitation = fields["precipitation"]
            if previous_precipitation is not None:
                decreases = (
                    precipitation - previous_precipitation
                    < -PRECIP_DECREASE_TOLERANCE_KG_M2
                ) & masks["active_soil_all"]
                precipitation_decrease_cells += int(np.count_nonzero(decreases))
            previous_precipitation = precipitation.copy()

            if legacy_mode:
                evaporation_rate = (
                    fields["hfls"] / LATENT_HEAT_VAPORIZATION_J_KG
                )
                runoff_sample_rate = (
                    fields["runoff_surface"] + fields["runoff_subsurface"]
                ) / float(args.legacy_runoff_timestep_seconds)
                runoff_surface_sample_rate = (
                    fields["runoff_surface"]
                    / float(args.legacy_runoff_timestep_seconds)
                )
                runoff_subsurface_sample_rate = (
                    fields["runoff_subsurface"]
                    / float(args.legacy_runoff_timestep_seconds)
                )
                evaporation_cumulative = np.full_like(precipitation, np.nan)
                runoff_surface_cumulative = np.full_like(
                    precipitation, np.nan
                )
                runoff_subsurface_cumulative = np.full_like(
                    precipitation, np.nan
                )
                water_aquifer = np.zeros_like(precipitation)
                storage_gw = np.zeros_like(precipitation)
                wetland_water = np.zeros_like(precipitation)
            else:
                evaporation_rate = np.full_like(precipitation, np.nan)
                runoff_sample_rate = np.full_like(precipitation, np.nan)
                runoff_surface_sample_rate = np.full_like(
                    precipitation, np.nan
                )
                runoff_subsurface_sample_rate = np.full_like(
                    precipitation, np.nan
                )
                evaporation_cumulative = fields[
                    "evaporation_net_cumulative"
                ]
                runoff_surface_cumulative = fields[
                    "runoff_surface_cumulative"
                ]
                runoff_subsurface_cumulative = fields[
                    "runoff_subsurface_cumulative"
                ]
                water_aquifer = fields["water_aquifer"]
                storage_gw = fields["storage_gw"]
                wetland_water = fields["wetland_h20_store"]
                if previous_surface_runoff is not None:
                    surface_runoff_decrease_cells += int(
                        np.count_nonzero(
                            (
                                runoff_surface_cumulative
                                - previous_surface_runoff
                                < -CUMULATIVE_DECREASE_TOLERANCE_KG_M2
                            )
                            & masks["active_soil_all"]
                        )
                    )
                    subsurface_runoff_decrease_cells += int(
                        np.count_nonzero(
                            (
                                runoff_subsurface_cumulative
                                - previous_subsurface_runoff
                                < -CUMULATIVE_DECREASE_TOLERANCE_KG_M2
                            )
                            & masks["active_soil_all"]
                        )
                    )
                previous_surface_runoff = runoff_surface_cumulative.copy()
                previous_subsurface_runoff = runoff_subsurface_cumulative.copy()
            water_store = (
                calculated_soil_column
                + fields["swet"]
                + fields["canopy_water"]
                + water_aquifer
                + wetland_water
            )
            energy_residual = (
                (1.0 - fields["albedo"]) * fields["rsds"]
                + fields["lwtr"]
                - fields["rlus"]
                - fields["hfss"]
                - fields["hfls"]
                - fields["hfgs"]
            )
            wind_speed = np.hypot(fields["u10m"], fields["v10m"])

            derived = {
                "precipitation_cumulative_kg_m2": precipitation,
                "evaporation_net_cumulative_kg_m2": evaporation_cumulative,
                "runoff_surface_cumulative_kg_m2": runoff_surface_cumulative,
                "runoff_subsurface_cumulative_kg_m2": (
                    runoff_subsurface_cumulative
                ),
                "evaporation_rate_kg_m2_s": evaporation_rate,
                "runoff_sample_rate_kg_m2_s": runoff_sample_rate,
                "runoff_surface_sample_rate_kg_m2_s": (
                    runoff_surface_sample_rate
                ),
                "runoff_subsurface_sample_rate_kg_m2_s": (
                    runoff_subsurface_sample_rate
                ),
                "water_store_kg_m2": water_store,
                "soil_water_kg_m2": calculated_soil_column,
                "soil_water_reported_kg_m2": fields["soil_column_total_water"],
                "swe_kg_m2": fields["swet"],
                "canopy_water_kg_m2": fields["canopy_water"],
                "water_aquifer_kg_m2": water_aquifer,
                "storage_gw_diagnostic_kg_m2": storage_gw,
                "wetland_water_kg_m2": wetland_water,
                "surface_energy_residual_w_m2": energy_residual,
                "temperature_2m_k": fields["taix"],
                "wind_speed_10m_m_s": wind_speed,
                "snow_height_m": fields["snow_height"],
            }
            for class_name, mask in masks.items():
                if not np.any(mask):
                    for name in derived:
                        series[class_name][name].append(float("nan"))
                    continue
                for name, values in derived.items():
                    series[class_name][name].append(_mean(values, mask))

            if record_index == 0:
                first_fields = {name: values.copy() for name, values in derived.items()}
            if record_index == len(records) - 1:
                last_fields = {name: values.copy() for name, values in derived.items()}
    finally:
        if dataset is not None:
            dataset.close()

    for name, count in nonfinite_counts.items():
        if count:
            failures.append(f"{name} has {count} non-finite active-soil values")
    if precipitation_decrease_cells:
        failures.append(
            "cumulative precipitation decreased beyond tolerance in "
            f"{precipitation_decrease_cells} active-soil record/cells"
        )
    if surface_runoff_decrease_cells:
        failures.append(
            "cumulative surface runoff decreased beyond tolerance in "
            f"{surface_runoff_decrease_cells} active-soil record/cells"
        )
    if subsurface_runoff_decrease_cells:
        failures.append(
            "cumulative subsurface runoff decreased beyond tolerance in "
            f"{subsurface_runoff_decrease_cells} active-soil record/cells"
        )
    soil_crosscheck_gate_class = (
        "active_soil_interior"
        if np.any(masks["active_soil_interior"])
        else "active_soil_all"
    )
    for crosscheck in soil_column_crosscheck[1:]:
        interior_crosscheck = crosscheck["classes"][soil_crosscheck_gate_class]
        if (
            interior_crosscheck["maximum_absolute_difference_kg_m2"]
            > SOIL_COLUMN_CROSSCHECK_TOLERANCE_KG_M2
        ):
            failures.append(
                "reported and layer-derived soil columns differ after the "
                f"initial frame in {soil_crosscheck_gate_class} at "
                f"{crosscheck['time']}: "
                f"{interior_crosscheck['maximum_absolute_difference_kg_m2']} "
                "kg m-2"
            )

    classes: dict[str, dict] = {}
    for class_name, class_series in series.items():
        if (
            not np.any(masks[class_name])
            or not times
            or len(class_series["precipitation_cumulative_kg_m2"]) != len(times)
        ):
            continue
        precipitation_amount = (
            class_series["precipitation_cumulative_kg_m2"][-1]
            - class_series["precipitation_cumulative_kg_m2"][0]
        )
        if legacy_mode:
            evaporation_amount = _integrate_trapezoid(
                times, class_series["evaporation_rate_kg_m2_s"]
            )
            runoff_surface_amount = _integrate_trapezoid(
                times,
                class_series["runoff_surface_sample_rate_kg_m2_s"],
            )
            runoff_subsurface_amount = _integrate_trapezoid(
                times,
                class_series["runoff_subsurface_sample_rate_kg_m2_s"],
            )
            runoff_amount = runoff_surface_amount + runoff_subsurface_amount
        else:
            evaporation_amount = (
                class_series["evaporation_net_cumulative_kg_m2"][-1]
                - class_series["evaporation_net_cumulative_kg_m2"][0]
            )
            runoff_surface_amount = (
                class_series["runoff_surface_cumulative_kg_m2"][-1]
                - class_series["runoff_surface_cumulative_kg_m2"][0]
            )
            runoff_subsurface_amount = (
                class_series["runoff_subsurface_cumulative_kg_m2"][-1]
                - class_series["runoff_subsurface_cumulative_kg_m2"][0]
            )
            runoff_amount = runoff_surface_amount + runoff_subsurface_amount
        storage_change = (
            class_series["water_store_kg_m2"][-1]
            - class_series["water_store_kg_m2"][0]
        )
        water_residual = (
            precipitation_amount
            - evaporation_amount
            - runoff_amount
            - storage_change
        )
        emitted_series = {
            name: values
            for name, values in class_series.items()
            if not all(np.isnan(value) for value in values)
        }
        classes[class_name] = {
            "cell_count": int(np.count_nonzero(masks[class_name])),
            "water_diagnostic_kg_m2": {
                "precipitation": precipitation_amount,
                "evaporation": evaporation_amount,
                "runoff": runoff_amount,
                "runoff_surface": runoff_surface_amount,
                "runoff_subsurface": runoff_subsurface_amount,
                "resolved_storage_change": storage_change,
                "residual": water_residual,
            },
            "surface_energy_diagnostic": {
                "mean_residual_w_m2": float(
                    np.mean(class_series["surface_energy_residual_w_m2"])
                ),
                "mean_absolute_residual_w_m2": float(
                    np.mean(
                        np.abs(class_series["surface_energy_residual_w_m2"])
                    )
                ),
                "integrated_residual_mj_m2": _integrate_trapezoid(
                    times,
                    class_series["surface_energy_residual_w_m2"],
                    scale=1.0e-6,
                ),
            },
            "linear_tendencies_per_day": {
                name: _linear_slope_per_day(times, values)
                for name, values in class_series.items()
                if name
                in {
                    "water_store_kg_m2",
                    "soil_water_kg_m2",
                    "swe_kg_m2",
                    "canopy_water_kg_m2",
                    "temperature_2m_k",
                    "snow_height_m",
                }
            },
            "time_series": {
                "times": [value.isoformat() for value in times],
                **emitted_series,
            },
        }

    endpoint_distributions: dict[str, dict] = {}
    if times and first_fields and last_fields:
        for name in (
            "temperature_2m_k",
            "wind_speed_10m_m_s",
            "snow_height_m",
            "soil_water_kg_m2",
            "swe_kg_m2",
            "surface_energy_residual_w_m2",
        ):
            endpoint_distributions[name] = {
                "start": _sample_summary(
                    first_fields[name],
                    masks["active_soil_all"],
                    args.maximum_percentile_samples,
                ),
                "end": _sample_summary(
                    last_fields[name],
                    masks["active_soil_all"],
                    args.maximum_percentile_samples,
                ),
            }

    water_budget_contract = {
        "mode": (
            "legacy_snapshot_reconstruction"
            if legacy_mode
            else "production_cumulative"
        ),
        "production_eligible": not legacy_mode and not failures,
        "interval": {
            "start": times[0].isoformat() if times else None,
            "end": times[-1].isoformat() if times else None,
            "mathematical_bounds": "(start, end]",
            "derivation": (
                "endpoint difference of restart-persistent cumulative fields"
                if not legacy_mode
                else "trapezoidal integration of three-hourly samples"
            ),
        },
        "precipitation": {
            "field": "precipitation",
            "semantics": (
                EXPECTED_ACCUMULATION_SEMANTICS
                if not legacy_mode
                else "legacy cumulative field checked only for non-decrease"
            ),
        },
        "evaporation": {
            "field": (
                "evaporation_net_cumulative" if not legacy_mode else "hfls"
            ),
            "positive": "upward",
            "signed": True,
        },
        "runoff": {
            "fields": (
                [
                    "runoff_surface_cumulative",
                    "runoff_subsurface_cumulative",
                ]
                if not legacy_mode
                else ["runoff_surface", "runoff_subsurface"]
            ),
            "legacy_soil_timestep_seconds": (
                args.legacy_runoff_timestep_seconds if legacy_mode else None
            ),
        },
        "storage": {
            "summed_for_closure": [
                "layer-derived soil water",
                "swet",
                "canopy_water",
                *(
                    []
                    if legacy_mode
                    else ["water_aquifer", "wetland_h20_store"]
                ),
            ],
            "diagnostic_not_summed": (
                []
                if legacy_mode
                else [
                    {
                        "field": "storage_gw",
                        "reason": (
                            "Noah-MP WaterStorageSoilAqf includes aquifer plus "
                            "saturated soil already represented by the layer "
                            "soil column and water_aquifer."
                        ),
                    }
                ]
            ),
        },
        "restart_continuity": {
            "required_for_promotion": True,
            "observable_check": (
                "cumulative precipitation and runoff never decrease across "
                "the supplied ordered records"
            ),
            "trajectory_evidence_required_separately": True,
        },
        "representativeness_limited": legacy_mode,
    }

    payload = {
        "schema_version": 2,
        "status": "PASS" if not failures else "FAIL",
        "event_name": args.event_name,
        "start": times[0].isoformat() if times else None,
        "end": times[-1].isoformat() if times else None,
        "record_count": len(records),
        "output_interval_seconds": args.expected_interval_seconds,
        "static_file": str(args.static_file.resolve()),
        "static_sha256": _sha256(args.static_file),
        "output_files": [
            {
                "path": str(path.resolve()),
                "size_bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in args.output_file
        ],
        "mask_contract": mask_metadata,
        "nonfinite_counts": nonfinite_counts,
        "precipitation_decrease_cells": precipitation_decrease_cells,
        "surface_runoff_decrease_cells": surface_runoff_decrease_cells,
        "subsurface_runoff_decrease_cells": subsurface_runoff_decrease_cells,
        "soil_column_crosscheck": {
            "calculation": (
                "sum(soil_water_content * [0.1,0.2,0.4,0.8] m * "
                "1000 kg m-3)"
            ),
            "post_initial_tolerance_kg_m2": (
                SOIL_COLUMN_CROSSCHECK_TOLERANCE_KG_M2
            ),
            "gate_class": soil_crosscheck_gate_class,
            "records": soil_column_crosscheck,
        },
        "classes": classes,
        "endpoint_distributions": endpoint_distributions,
        "water_budget_contract": water_budget_contract,
        "surface_energy_contract": {
            "formula": "(1-albedo)*rsds + lwtr - rlus - hfss - hfls - hfgs",
            "sign_convention": (
                "rsds and lwtr are downwelling inputs; rlus, hfss, and hfls "
                "are upward losses; hfgs is the Noah-MP ground heat flux."
            ),
            "source_verification": (
                "HICAR ra_driver passes lwtr as RRTMG GLW and lsm_driver "
                "documents it as downward longwave flux."
            ),
        },
        "limitations": [
            *(
                [
                    "Legacy water closure is approximate and representativeness-limited; it cannot satisfy month or annual promotion.",
                    "Last-soil-step runoff amounts are divided by the verified soil timestep and three-hour samples are trapezoid-integrated; sub-interval variability is unresolved.",
                    "Legacy evaporation is reconstructed from sampled latent heat with a fixed 2.5e6 J kg-1 vaporization heat.",
                    "Legacy water storage omits groundwater and wetland stores.",
                ]
                if legacy_mode
                else [
                    "The active-soil closure excludes lake and atmospheric stores by mask and scope.",
                    "A separate segmented-versus-uninterrupted trajectory comparison must prove restart continuity before promotion.",
                ]
            ),
            "The water budget uses the layer-derived soil column because HICAR's reported column diagnostic is not populated in the initial output frame.",
            "Some pre-metadata-fix HICAR files label lwtr with a net-downward CF standard_name even though source semantics are downwelling; the diagnostic follows the verified source semantics.",
            "A linear tendency over the requested evaluation period characterizes that period and is not by itself evidence of numerical drift.",
            "Equal weighting is appropriate for the approximately equal-area 200 m projected grid.",
        ],
        "failures": failures,
    }
    _write_json_atomic(args.report, payload)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    Path(f"{args.report}.ready").touch()
    print(
        f"PASS: {args.event_name} records={len(records)} "
        f"active_soil={mask_metadata['classes']['active_soil_all']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
