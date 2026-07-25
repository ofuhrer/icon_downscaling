#!/usr/bin/env python3
"""Characterize qualification-output extremes by HICAR surface class."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import netCDF4
import numpy as np


def _read_2d(dataset: netCDF4.Dataset, name: str) -> np.ndarray:
    values = np.asarray(dataset.variables[name][:])
    values = np.squeeze(values)
    if values.ndim != 2:
        raise ValueError(f"{name} must reduce to 2-D, got {values.shape}")
    return values


def _class_counts(values: np.ndarray, mask: np.ndarray) -> dict[str, int]:
    selected = np.asarray(values)[mask].astype(np.int64)
    return {str(key): int(value) for key, value in sorted(Counter(selected).items())}


def _summarize_mask(
    mask: np.ndarray,
    landuse: np.ndarray,
    soil_type: np.ndarray,
    terrain: np.ndarray,
    latitude: np.ndarray,
    longitude: np.ndarray,
) -> dict[str, object]:
    count = int(np.count_nonzero(mask))
    summary: dict[str, object] = {
        "count": count,
        "landuse_counts": _class_counts(landuse, mask),
        "soil_type_counts": _class_counts(soil_type, mask),
    }
    if count:
        summary.update(
            {
                "terrain_m": [float(np.min(terrain[mask])), float(np.max(terrain[mask]))],
                "latitude_deg_n": [
                    float(np.min(latitude[mask])),
                    float(np.max(latitude[mask])),
                ],
                "longitude_deg_e": [
                    float(np.min(longitude[mask])),
                    float(np.max(longitude[mask])),
                ],
            }
        )
    return summary


def _collapse_event(values: np.ndarray, comparison: str, threshold: float) -> np.ndarray:
    axes = tuple(range(values.ndim - 2))
    if comparison == "ge":
        event = values >= threshold
    elif comparison == "lt":
        event = values < threshold
    else:
        raise ValueError(comparison)
    return np.any(event, axis=axes) if axes else event


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--static-file", required=True)
    parser.add_argument("--output-file", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    with netCDF4.Dataset(args.static_file) as static:
        landmask = _read_2d(static, "landmask")
        landuse = _read_2d(static, "landuse")
        soil_type = _read_2d(static, "soil_type")
        terrain = _read_2d(static, "topo")
        latitude = _read_2d(static, "lat")
        longitude = _read_2d(static, "lon")

    raw_land = landmask > 0
    # The Swiss namelist uses USGS: category 16 is water. HICAR replaces its
    # internal land mask from vegetation class during Noah-MP initialization.
    effective_land = raw_land & (landuse != 16)
    active_soil = effective_land & (landuse != 24)

    with netCDF4.Dataset(args.output_file) as output:
        soil_water = np.asarray(output.variables["soil_water_content"][:])
        total_water = np.asarray(output.variables["soil_column_total_water"][:])
        runoff_subsurface = np.asarray(output.variables["runoff_subsurface"][:])
        runoff_surface = np.asarray(output.variables["runoff_surface"][:])

    events = {
        "soil_water_content_ge_0.99": _collapse_event(soil_water, "ge", 0.99),
        "soil_column_total_water_ge_1499": _collapse_event(total_water, "ge", 1499.0),
        "runoff_subsurface_lt_-1e-8": _collapse_event(
            runoff_subsurface, "lt", -1.0e-8
        ),
        "runoff_surface_lt_-1e-8": _collapse_event(runoff_surface, "lt", -1.0e-8),
    }

    report = {
        "schema_version": 1,
        "static_file": str(Path(args.static_file).resolve()),
        "output_file": str(Path(args.output_file).resolve()),
        "surface_classes": {
            "raw_land": _summarize_mask(
                raw_land, landuse, soil_type, terrain, latitude, longitude
            ),
            "usgs_water_16_on_raw_land": _summarize_mask(
                raw_land & (landuse == 16),
                landuse,
                soil_type,
                terrain,
                latitude,
                longitude,
            ),
            "effective_noahmp_land": _summarize_mask(
                effective_land, landuse, soil_type, terrain, latitude, longitude
            ),
            "active_soil_excluding_usgs_ice_24": _summarize_mask(
                active_soil, landuse, soil_type, terrain, latitude, longitude
            ),
        },
        "events": {},
    }
    for name, event_mask in events.items():
        report["events"][name] = {
            "all_raw_land": _summarize_mask(
                raw_land & event_mask,
                landuse,
                soil_type,
                terrain,
                latitude,
                longitude,
            ),
            "effective_noahmp_land": _summarize_mask(
                effective_land & event_mask,
                landuse,
                soil_type,
                terrain,
                latitude,
                longitude,
            ),
            "active_soil_excluding_usgs_ice_24": _summarize_mask(
                active_soil & event_mask,
                landuse,
                soil_type,
                terrain,
                latitude,
                longitude,
            ),
        }

    output_path = Path(args.report)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    temporary.replace(output_path)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
