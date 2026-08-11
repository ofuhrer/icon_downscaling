#!/usr/bin/env python3
"""Summarize where hicarprep used global same-surface SST fallback support."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any

import netCDF4
import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree


EARTH_MEAN_RADIUS_KM = 6_371.0088
LARGEST_COMPONENT_COUNT = 20
MATERIAL_WATER_AREA_KM2 = 1.0
MATERIAL_FALLBACK_AREA_KM2 = 0.2


def read_array(variable: netCDF4.Variable, *, fill: float = np.nan) -> np.ndarray:
    values = np.ma.asarray(variable[:])
    if np.ma.count_masked(values):
        values = values.filled(fill)
    return np.asarray(values)


def require_finite_2d(name: str, values: np.ndarray) -> np.ndarray:
    result = np.asarray(values, dtype=np.float64)
    if result.ndim != 2 or not np.isfinite(result).all():
        raise ValueError(f"{name} must be a finite two-dimensional field")
    return result


def regular_spacing_m(dataset: netCDF4.Dataset) -> tuple[float, float, str]:
    if "x" in dataset.variables and "y" in dataset.variables:
        x = np.asarray(read_array(dataset["x"]), dtype=np.float64)
        y = np.asarray(read_array(dataset["y"]), dtype=np.float64)
        if x.ndim != 1 or y.ndim != 1 or x.size < 2 or y.size < 2:
            raise ValueError("static x/y coordinates must be one-dimensional with two or more cells")
        if not np.isfinite(x).all() or not np.isfinite(y).all():
            raise ValueError("static x/y coordinates are not finite")
        dx_values = np.abs(np.diff(x))
        dy_values = np.abs(np.diff(y))
        dx_m = float(np.median(dx_values))
        dy_m = float(np.median(dy_values))
        if dx_m <= 0.0 or dy_m <= 0.0:
            raise ValueError("static x/y coordinates are degenerate")
        if not np.allclose(dx_values, dx_m, rtol=1.0e-7, atol=1.0e-6) or not np.allclose(
            dy_values, dy_m, rtol=1.0e-7, atol=1.0e-6
        ):
            raise ValueError("static x/y coordinates are not regularly spaced")
        return dx_m, dy_m, "static projected x/y coordinate spacing"

    dx_m = float(getattr(dataset, "hicar_dx_m", np.nan))
    dy_m = float(getattr(dataset, "hicar_dy_m", dx_m))
    if not (math.isfinite(dx_m) and math.isfinite(dy_m) and dx_m > 0.0 and dy_m > 0.0):
        raise ValueError("static lacks usable projected grid spacing")
    return dx_m, dy_m, "static hicar_dx_m/hicar_dy_m attributes"


def load_inputs(
    forcing_path: Path,
    static_path: Path,
) -> tuple[
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    float,
    float,
    str,
]:
    with netCDF4.Dataset(static_path) as static:
        missing = sorted({"lat", "lon", "landmask"} - set(static.variables))
        if missing:
            raise ValueError(f"static runtime domain lacks variables: {missing}")
        latitude = require_finite_2d("static lat", read_array(static["lat"]))
        longitude = require_finite_2d("static lon", read_array(static["lon"]))
        landmask = require_finite_2d("static landmask", read_array(static["landmask"]))
        if longitude.shape != latitude.shape or landmask.shape != latitude.shape:
            raise ValueError("static lat/lon/landmask shapes differ")
        if np.any((landmask < 0.0) | (landmask > 1.0)):
            raise ValueError("static landmask lies outside [0, 1]")
        dx_m, dy_m, spacing_source = regular_spacing_m(static)

    with netCDF4.Dataset(forcing_path) as forcing:
        required = {
            "lat_1",
            "lon_1",
            "SST_global_fallback_mask",
            "SST_global_fallback_distance_km",
        }
        missing = sorted(required - set(forcing.variables))
        if missing:
            raise ValueError(f"regular forcing lacks variables: {missing}")
        mask_variable = forcing["SST_global_fallback_mask"]
        distance_variable = forcing["SST_global_fallback_distance_km"]
        if mask_variable.dimensions != ("y_1", "x_1") or distance_variable.dimensions != (
            "y_1",
            "x_1",
        ):
            raise ValueError("SST fallback provenance is not on the forcing target grid")
        forcing_latitude = require_finite_2d("forcing lat_1", read_array(forcing["lat_1"]))
        forcing_longitude = require_finite_2d("forcing lon_1", read_array(forcing["lon_1"]))
        fallback_values = np.asarray(read_array(mask_variable, fill=np.nan), dtype=np.float64)
        if not np.isfinite(fallback_values).all() or np.any(
            (fallback_values != 0.0) & (fallback_values != 1.0)
        ):
            raise ValueError("SST global fallback mask is not binary")
        fallback_mask = fallback_values.astype(bool)
        fallback_distance_km = np.asarray(
            read_array(distance_variable, fill=np.nan), dtype=np.float64
        )
        reported_water_count = int(getattr(forcing, "sst_water_cell_count", -1))
        reported_fallback_count = int(
            getattr(forcing, "sst_water_global_fallback_count", -1)
        )
        reported_fallback_maximum = float(
            getattr(forcing, "sst_maximum_global_fallback_distance_km", np.nan)
        )

    if not (
        forcing_latitude.shape
        == forcing_longitude.shape
        == fallback_mask.shape
        == fallback_distance_km.shape
        == latitude.shape
    ):
        raise ValueError("forcing fallback provenance and static target-grid shapes differ")
    if not np.array_equal(forcing_latitude, latitude) or not np.array_equal(
        forcing_longitude, longitude
    ):
        raise ValueError("forcing and static target latitude/longitude differ")

    water = landmask < 0.5
    water_count = int(np.count_nonzero(water))
    fallback_count = int(np.count_nonzero(fallback_mask))
    if reported_water_count != water_count:
        raise ValueError("forcing SST water-cell count disagrees with the static domain")
    if reported_fallback_count != fallback_count:
        raise ValueError("forcing SST global-fallback count disagrees with its mask")
    if np.any(fallback_mask & ~water):
        raise ValueError("SST global fallback mask includes target land cells")
    if np.any(~np.isfinite(fallback_distance_km[fallback_mask])) or np.any(
        fallback_distance_km[fallback_mask] < 0.0
    ):
        raise ValueError("SST global fallback distances are invalid on fallback cells")
    if np.any(np.isfinite(fallback_distance_km[~fallback_mask])):
        raise ValueError("SST global fallback distances are defined outside the fallback mask")
    expected_maximum = (
        float(np.max(fallback_distance_km[fallback_mask])) if fallback_count else 0.0
    )
    if not math.isclose(reported_fallback_maximum, expected_maximum, rel_tol=0.0, abs_tol=1.0e-9):
        raise ValueError("forcing SST global-fallback maximum disagrees with its distance field")

    return (
        latitude,
        longitude,
        water,
        fallback_mask,
        fallback_distance_km,
        dx_m,
        dy_m,
        spacing_source,
    )


def distribution(values: np.ndarray) -> dict[str, float | int | None]:
    finite = np.asarray(values, dtype=np.float64)
    if finite.size == 0:
        return {"count": 0, "maximum": None, "p50": None, "p90": None, "p99": None}
    quantiles = np.quantile(finite, [0.5, 0.9, 0.99], method="linear")
    return {
        "count": int(finite.size),
        "maximum": float(np.max(finite)),
        "p50": float(quantiles[0]),
        "p90": float(quantiles[1]),
        "p99": float(quantiles[2]),
    }


def select_component_ids(
    water_counts: np.ndarray,
    fallback_counts: np.ndarray,
    cell_area_km2: float,
) -> tuple[set[int], dict[int, list[str]]]:
    component_ids = np.arange(1, water_counts.size, dtype=np.int32)
    reasons: dict[int, list[str]] = {}

    def add(component_id: int, reason: str) -> None:
        reasons.setdefault(int(component_id), []).append(reason)

    water_order = component_ids[np.argsort(-water_counts[1:], kind="stable")]
    for component_id in water_order[:LARGEST_COMPONENT_COUNT]:
        add(int(component_id), f"largest_{LARGEST_COMPONENT_COUNT}_by_water_area")

    affected_ids = component_ids[fallback_counts[1:] > 0]
    if affected_ids.size:
        fallback_order = affected_ids[
            np.argsort(-fallback_counts[affected_ids], kind="stable")
        ]
        for component_id in fallback_order[:LARGEST_COMPONENT_COUNT]:
            add(int(component_id), f"largest_{LARGEST_COMPONENT_COUNT}_by_fallback_area")

    for component_id in component_ids[water_counts[1:] * cell_area_km2 >= MATERIAL_WATER_AREA_KM2]:
        add(int(component_id), f"water_area_ge_{MATERIAL_WATER_AREA_KM2:g}_km2")
    for component_id in component_ids[
        fallback_counts[1:] * cell_area_km2 >= MATERIAL_FALLBACK_AREA_KM2
    ]:
        add(int(component_id), f"fallback_area_ge_{MATERIAL_FALLBACK_AREA_KM2:g}_km2")
    return set(reasons), reasons


def component_report(
    latitude: np.ndarray,
    longitude: np.ndarray,
    water: np.ndarray,
    fallback_mask: np.ndarray,
    cell_area_km2: float,
) -> dict[str, Any]:
    structure = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=np.int8)
    labels, component_count = ndimage.label(water, structure=structure)
    water_counts = np.bincount(labels.ravel(), minlength=component_count + 1).astype(np.int64)
    water_counts[0] = 0
    fallback_counts = np.bincount(
        labels[fallback_mask], minlength=component_count + 1
    ).astype(np.int64)
    selected_ids, reasons = select_component_ids(
        water_counts, fallback_counts, cell_area_km2
    )

    components: list[dict[str, Any]] = []
    for component_id in sorted(
        selected_ids, key=lambda value: (-int(water_counts[value]), value)
    ):
        component = labels == component_id
        water_cell_count = int(water_counts[component_id])
        fallback_cell_count = int(fallback_counts[component_id])
        component_latitude = latitude[component]
        component_longitude = longitude[component]
        components.append(
            {
                "component_id": component_id,
                "water_cell_count": water_cell_count,
                "water_area_km2": water_cell_count * cell_area_km2,
                "fallback_cell_count": fallback_cell_count,
                "fallback_area_km2": fallback_cell_count * cell_area_km2,
                "fallback_fraction_of_component_water": (
                    fallback_cell_count / water_cell_count
                ),
                "centroid": {
                    "latitude": float(np.mean(component_latitude)),
                    "longitude": float(np.mean(component_longitude)),
                },
                "bounds": {
                    "latitude_min": float(np.min(component_latitude)),
                    "latitude_max": float(np.max(component_latitude)),
                    "longitude_min": float(np.min(component_longitude)),
                    "longitude_max": float(np.max(component_longitude)),
                },
                "selection_reasons": reasons[component_id],
            }
        )

    retained_water_cells = sum(int(water_counts[value]) for value in selected_ids)
    retained_fallback_cells = sum(int(fallback_counts[value]) for value in selected_ids)
    affected_count = int(np.count_nonzero(fallback_counts[1:]))
    return {
        "connectivity": "four-neighbour edge connectivity on the target grid",
        "total_component_count": int(component_count),
        "fallback_affected_component_count": affected_count,
        "retention": {
            "largest_by_water_area_count": LARGEST_COMPONENT_COUNT,
            "largest_by_fallback_area_count": LARGEST_COMPONENT_COUNT,
            "material_water_area_km2": MATERIAL_WATER_AREA_KM2,
            "material_fallback_area_km2": MATERIAL_FALLBACK_AREA_KM2,
            "rule": (
                "union of largest components by water area, largest affected components "
                "by fallback area, and components meeting either material-area threshold"
            ),
        },
        "retained_component_count": len(components),
        "omitted_component_count": int(component_count) - len(components),
        "retained_water_area_km2": retained_water_cells * cell_area_km2,
        "omitted_water_area_km2": (
            int(np.sum(water_counts)) - retained_water_cells
        )
        * cell_area_km2,
        "retained_fallback_area_km2": retained_fallback_cells * cell_area_km2,
        "omitted_fallback_area_km2": (
            int(np.sum(fallback_counts)) - retained_fallback_cells
        )
        * cell_area_km2,
        "components": components,
    }


def unit_sphere_points(latitude: np.ndarray, longitude: np.ndarray) -> np.ndarray:
    latitude_radians = np.deg2rad(np.asarray(latitude, dtype=np.float64))
    longitude_radians = np.deg2rad(np.asarray(longitude, dtype=np.float64))
    return np.column_stack(
        (
            np.cos(latitude_radians) * np.cos(longitude_radians),
            np.cos(latitude_radians) * np.sin(longitude_radians),
            np.sin(latitude_radians),
        )
    )


def read_stations(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8", errors="strict", newline="") as stream:
        reader = csv.reader(stream, delimiter=";")
        try:
            header = next(reader)
        except StopIteration as error:
            raise ValueError("observation CSV is empty") from error
        lower = [name.strip().lower() for name in header]
        required = ("meas_site", "nat_abbr", "latitude", "longitude")
        missing = [name for name in required if name not in lower]
        if missing:
            raise ValueError(f"observation CSV lacks station-coordinate columns: {missing}")
        position = {name: lower.index(name) for name in required}
        sites: dict[str, dict[str, Any]] = {}
        for row_number, row in enumerate(reader, start=2):
            if not row or len(row) <= max(position.values()):
                continue
            meas_site = row[position["meas_site"]].strip()
            abbreviation = row[position["nat_abbr"]].strip()
            if not meas_site or not abbreviation:
                continue
            try:
                latitude = float(row[position["latitude"]])
                longitude = float(row[position["longitude"]])
            except ValueError:
                continue
            if not (
                math.isfinite(latitude)
                and math.isfinite(longitude)
                and -90.0 <= latitude <= 90.0
                and -180.0 <= longitude <= 180.0
            ):
                continue
            key = f"{abbreviation}:{meas_site}"
            site = {
                "key": key,
                "abbreviation": abbreviation,
                "meas_site": meas_site,
                "latitude": latitude,
                "longitude": longitude,
            }
            previous = sites.get(key)
            if previous is not None and (
                previous["latitude"] != latitude or previous["longitude"] != longitude
            ):
                raise ValueError(
                    f"station coordinates change within observation CSV at row {row_number}: {key}"
                )
            sites[key] = site
    return [sites[key] for key in sorted(sites)]


def station_report(
    observations_path: Path | None,
    stations: list[dict[str, Any]],
    latitude: np.ndarray,
    longitude: np.ndarray,
    fallback_mask: np.ndarray,
    fallback_distance_km: np.ndarray,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "provided": observations_path is not None,
        "unique_station_count": len(stations),
        "distance_definition": (
            "great-circle distance from the station coordinate to the nearest target-grid "
            "cell using global SST fallback; distinct from that cell's source-donor distance"
        ),
        "sites": [],
    }
    if not stations:
        return result

    fallback_rows, fallback_columns = np.nonzero(fallback_mask)
    if not fallback_rows.size:
        result["sites"] = [
            {
                **site,
                "nearest_fallback_cell_distance_km": None,
                "nearest_fallback_cell": None,
            }
            for site in stations
        ]
        return result

    fallback_latitude = latitude[fallback_mask]
    fallback_longitude = longitude[fallback_mask]
    tree = cKDTree(unit_sphere_points(fallback_latitude, fallback_longitude))
    station_latitude = np.asarray([site["latitude"] for site in stations])
    station_longitude = np.asarray([site["longitude"] for site in stations])
    chord, nearest = tree.query(unit_sphere_points(station_latitude, station_longitude), k=1)
    arc_km = 2.0 * EARTH_MEAN_RADIUS_KM * np.arcsin(np.minimum(chord / 2.0, 1.0))
    for site, distance_km, nearest_index in zip(stations, arc_km, nearest, strict=True):
        y_index = int(fallback_rows[int(nearest_index)])
        x_index = int(fallback_columns[int(nearest_index)])
        result["sites"].append(
            {
                **site,
                "nearest_fallback_cell_distance_km": float(distance_km),
                "nearest_fallback_cell": {
                    "y_index": y_index,
                    "x_index": x_index,
                    "latitude": float(latitude[y_index, x_index]),
                    "longitude": float(longitude[y_index, x_index]),
                    "sst_source_donor_distance_km": float(
                        fallback_distance_km[y_index, x_index]
                    ),
                },
            }
        )
    return result


def build_report(
    forcing_path: Path,
    static_path: Path,
    observations_path: Path | None,
) -> dict[str, Any]:
    (
        latitude,
        longitude,
        water,
        fallback_mask,
        fallback_distance_km,
        dx_m,
        dy_m,
        spacing_source,
    ) = load_inputs(forcing_path, static_path)
    cell_area_km2 = dx_m * dy_m / 1_000_000.0
    water_count = int(np.count_nonzero(water))
    fallback_count = int(np.count_nonzero(fallback_mask))
    stations = read_stations(observations_path) if observations_path is not None else []
    return {
        "schema_version": 1,
        "inputs": {
            "regular_forcing": str(forcing_path.resolve()),
            "static_runtime_domain": str(static_path.resolve()),
            "swissmetnet_observations": (
                str(observations_path.resolve()) if observations_path is not None else None
            ),
        },
        "grid": {
            "shape": [int(latitude.shape[0]), int(latitude.shape[1])],
            "dx_m": dx_m,
            "dy_m": dy_m,
            "cell_area_km2": cell_area_km2,
            "area_basis": f"constant projected-grid cell area from {spacing_source}",
        },
        "summary": {
            "water_cell_count": water_count,
            "water_area_km2": water_count * cell_area_km2,
            "global_fallback_cell_count": fallback_count,
            "global_fallback_area_km2": fallback_count * cell_area_km2,
            "global_fallback_fraction_of_water": (
                fallback_count / water_count if water_count else None
            ),
            "sst_source_donor_distance_km": distribution(
                fallback_distance_km[fallback_mask]
            ),
        },
        "water_components": component_report(
            latitude, longitude, water, fallback_mask, cell_area_km2
        ),
        "stations": station_report(
            observations_path,
            stations,
            latitude,
            longitude,
            fallback_mask,
            fallback_distance_km,
        ),
    }


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
        os.replace(temporary_name, path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forcing", type=Path, required=True, help="one regular forcing record")
    parser.add_argument("--static", type=Path, required=True, help="matching runtime domain")
    parser.add_argument(
        "--observations",
        type=Path,
        help="optional staged semicolon-delimited SwissMetNet observation CSV",
    )
    parser.add_argument("--report", type=Path, required=True, help="output JSON report")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(argv)
    payload = build_report(args.forcing, args.static, args.observations)
    write_json_atomic(args.report, payload)
    print(
        "PASS "
        f"water_cells={payload['summary']['water_cell_count']} "
        f"fallback_cells={payload['summary']['global_fallback_cell_count']} "
        f"report={args.report}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
