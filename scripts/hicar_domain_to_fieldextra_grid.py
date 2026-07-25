#!/usr/bin/env python3
"""Derive a fieldextra geolatlon target grid from a HICAR domain file."""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import netCDF4
import numpy as np


EARTH_RADIUS_M = 6_371_229.0
ICON_LAT_NAMES = ("clat", "lat_cell_centre", "cell_lat", "lat", "latitude")
ICON_LON_NAMES = ("clon", "lon_cell_centre", "cell_lon", "lon", "longitude")


def read_var(path: Path, name: str) -> np.ndarray:
    with netCDF4.Dataset(path) as ds:
        if name not in ds.variables:
            available = ", ".join(sorted(ds.variables))
            raise SystemExit(f"{path}: variable {name!r} not found; available: {available}")
        data = np.asarray(ds.variables[name][:], dtype=np.float64)
    data = np.squeeze(data)
    if data.ndim == 1:
        data = np.broadcast_to(data[:, None], (data.size, 1))
    if data.ndim != 2:
        raise SystemExit(f"{path}: variable {name!r} must be 1D or 2D after squeezing, got {data.shape}")
    return data


def read_first_existing(path: Path, names: tuple[str, ...]) -> tuple[str, np.ndarray]:
    with netCDF4.Dataset(path) as ds:
        for name in names:
            if name in ds.variables:
                var = ds.variables[name]
                data = np.asarray(var[:], dtype=np.float64)
                units = getattr(var, "units", "")
                if units == "radian" or units == "radians" or np.nanmax(np.abs(data)) <= math.pi + 0.01:
                    data = np.degrees(data)
                return name, np.squeeze(data)
    raise SystemExit(f"{path}: none of {', '.join(names)} found")


def inflate_bounds(lat: np.ndarray, lon: np.ndarray, border_km: float) -> tuple[float, float, float, float]:
    lat_min = float(np.nanmin(lat))
    lat_max = float(np.nanmax(lat))
    lon_min = float(np.nanmin(lon))
    lon_max = float(np.nanmax(lon))
    mid_lat = 0.5 * (lat_min + lat_max)

    border_m = border_km * 1000.0
    dlat = math.degrees(border_m / EARTH_RADIUS_M)
    dlon = math.degrees(border_m / (EARTH_RADIUS_M * max(math.cos(math.radians(mid_lat)), 0.1)))
    return lat_min - dlat, lat_max + dlat, lon_min - dlon, lon_max + dlon


def align_bounds(vmin: float, vmax: float, step: float) -> tuple[float, float]:
    lo = math.floor(vmin / step) * step
    hi = math.ceil(vmax / step) * step
    if hi <= lo:
        hi = lo + step
    return lo, hi


def microdeg(value: float) -> int:
    return int(round(value * 1_000_000.0))


def check_icon_coverage(
    icon_grid: Path,
    lat_min: float,
    lat_max: float,
    lon_min: float,
    lon_max: float,
    tolerance_deg: float,
) -> None:
    lat_name, icon_lat = read_first_existing(icon_grid, ICON_LAT_NAMES)
    lon_name, icon_lon = read_first_existing(icon_grid, ICON_LON_NAMES)
    icon_lon = ((icon_lon + 180.0) % 360.0) - 180.0

    ilat_min = float(np.nanmin(icon_lat))
    ilat_max = float(np.nanmax(icon_lat))
    ilon_min = float(np.nanmin(icon_lon))
    ilon_max = float(np.nanmax(icon_lon))

    missing = []
    if lat_min < ilat_min - tolerance_deg:
        missing.append(f"south {lat_min:.4f} < ICON {ilat_min:.4f}")
    if lat_max > ilat_max + tolerance_deg:
        missing.append(f"north {lat_max:.4f} > ICON {ilat_max:.4f}")
    if lon_min < ilon_min - tolerance_deg:
        missing.append(f"west {lon_min:.4f} < ICON {ilon_min:.4f}")
    if lon_max > ilon_max + tolerance_deg:
        missing.append(f"east {lon_max:.4f} > ICON {ilon_max:.4f}")
    if missing:
        raise SystemExit(
            "requested HICAR forcing subdomain is not covered by ICON grid "
            f"{icon_grid} ({lat_name}/{lon_name}): " + "; ".join(missing)
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain-file", required=True, type=Path)
    parser.add_argument("--lat-var", default="lat")
    parser.add_argument("--lon-var", default="lon")
    parser.add_argument("--border-km", type=float, default=10.0)
    parser.add_argument("--dlon-deg", type=float, default=0.01)
    parser.add_argument("--dlat-deg", type=float, default=0.01)
    parser.add_argument("--icon-grid", type=Path)
    parser.add_argument("--coverage-tolerance-deg", type=float, default=0.02)
    parser.add_argument("--skip-icon-coverage-check", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.border_km < 0:
        raise SystemExit("--border-km must be non-negative")
    if args.dlon_deg <= 0 or args.dlat_deg <= 0:
        raise SystemExit("--dlon-deg and --dlat-deg must be positive")

    lat = read_var(args.domain_file, args.lat_var)
    lon = read_var(args.domain_file, args.lon_var)
    lon = ((lon + 180.0) % 360.0) - 180.0

    lat_min, lat_max, lon_min, lon_max = inflate_bounds(lat, lon, args.border_km)
    lat_min, lat_max = align_bounds(lat_min, lat_max, args.dlat_deg)
    lon_min, lon_max = align_bounds(lon_min, lon_max, args.dlon_deg)

    if args.icon_grid and not args.skip_icon_coverage_check:
        check_icon_coverage(args.icon_grid, lat_min, lat_max, lon_min, lon_max, args.coverage_tolerance_deg)

    target_grid = (
        "geolatlon,"
        f"{microdeg(lon_min)},{microdeg(lat_min)},"
        f"{microdeg(lon_max)},{microdeg(lat_max)},"
        f"{microdeg(args.dlon_deg)},{microdeg(args.dlat_deg)}"
    )

    if args.verbose:
        print(
            f"domain bounds with border: lat={lat_min:.6f}..{lat_max:.6f}, "
            f"lon={lon_min:.6f}..{lon_max:.6f}",
            file=sys.stderr,
        )
    print(target_grid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

