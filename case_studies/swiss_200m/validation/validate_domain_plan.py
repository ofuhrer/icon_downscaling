#!/usr/bin/env python3
"""Validate planned or generated geometry for the Switzerland 100 m case."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from pyproj import CRS, Transformer


CASE = Path(__file__).resolve().parents[1]
PLAN = json.loads((CASE / "config" / "domain.json").read_text())


def projected_margins_km() -> dict[str, float]:
    crs = CRS.from_proj4(
        f"+proj=aeqd +lat_0={PLAN['center_lat']} +lon_0={PLAN['center_lon']} "
        "+x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs"
    )
    transform = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    bbox = PLAN["switzerland_bbox"]
    corners = np.array([
        transform.transform(bbox["west_lon"], bbox["south_lat"]),
        transform.transform(bbox["west_lon"], bbox["north_lat"]),
        transform.transform(bbox["east_lon"], bbox["south_lat"]),
        transform.transform(bbox["east_lon"], bbox["north_lat"]),
    ])
    half_width = PLAN["width_km"] * 500.0
    half_height = PLAN["height_km"] * 500.0
    return {
        "west": float((corners[:, 0].min() + half_width) / 1000.0),
        "east": float((half_width - corners[:, 0].max()) / 1000.0),
        "south": float((corners[:, 1].min() + half_height) / 1000.0),
        "north": float((half_height - corners[:, 1].max()) / 1000.0),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--static-file", type=Path)
    args = parser.parse_args()

    expected_nx = int(round(PLAN["width_km"] * 1000 / PLAN["dx_m"])) + 1
    expected_ny = int(round(PLAN["height_km"] * 1000 / PLAN["dx_m"])) + 1
    if (PLAN["nx"], PLAN["ny"]) != (expected_nx, expected_ny):
        raise SystemExit("configured nx/ny disagree with width, height, and spacing")

    margins = projected_margins_km()
    minimum = PLAN["hicar_external_border_km"]
    if min(margins.values()) < minimum:
        raise SystemExit(f"Swiss bbox margin {margins} is below the required {minimum:g} km")
    residual = min(margins.values()) - PLAN["topography_blend_width_km"]
    if residual < PLAN["minimum_unblended_highres_buffer_km"]:
        raise SystemExit(
            f"unblended high-resolution buffer {residual:.3f} km is below the required "
            f"{PLAN['minimum_unblended_highres_buffer_km']:g} km"
        )

    if args.static_file:
        import netCDF4

        with netCDF4.Dataset(args.static_file) as ds:
            if len(ds.dimensions["x"]) != expected_nx or len(ds.dimensions["y"]) != expected_ny:
                raise SystemExit("static-file dimensions do not match domain plan")
            for name in ("x", "y", "lat", "lon", "topo", "landuse", "landmask", "soil_type", "soil_vwc", "soil_temperature"):
                if name not in ds.variables:
                    raise SystemExit(f"static-file missing required field: {name}")
            for name in ("topo", "lat", "lon"):
                if not np.isfinite(ds.variables[name][:]).all():
                    raise SystemExit(f"static-file contains non-finite {name}")

    print(json.dumps({"nx": expected_nx, "ny": expected_ny, "cells": expected_nx * expected_ny,
                      "swiss_bbox_margins_km": margins,
                      "minimum_unblended_highres_buffer_km": residual}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
