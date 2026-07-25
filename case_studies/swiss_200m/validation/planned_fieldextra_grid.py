#!/usr/bin/env python3
"""Derive the bordered fieldextra grid from the Switzerland domain plan."""
import argparse
import json
import math
import sys
from pathlib import Path

EARTH_RADIUS_M = 6_371_229.0
CASE = Path(__file__).resolve().parents[1]


def align(value: float, step: float, direction: str) -> float:
    scaled = value / step
    return (math.floor(scaled) if direction == "down" else math.ceil(scaled)) * step


def inverse_aeqd(x: float, y: float, lat0: float, lon0: float) -> tuple:
    """Spherical inverse AEQD matching fieldextra's configured Earth radius."""
    rho = math.hypot(x, y)
    if rho == 0.0:
        return lon0, lat0
    c = rho / EARTH_RADIUS_M
    sin_c = math.sin(c)
    cos_c = math.cos(c)
    lat = math.asin(cos_c * math.sin(lat0) + y * sin_c * math.cos(lat0) / rho)
    lon = lon0 + math.atan2(
        x * sin_c,
        rho * math.cos(lat0) * cos_c - y * math.sin(lat0) * sin_c,
    )
    return lon, lat


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=CASE / "config" / "domain.json")
    parser.add_argument("--edge-samples", type=int, default=257)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if args.edge_samples < 2:
        raise SystemExit("--edge-samples must be at least 2")

    plan = json.loads(args.plan.read_text())
    half_x = plan["width_km"] * 500.0
    half_y = plan["height_km"] * 500.0
    axis_x = [(-half_x + 2.0 * half_x * i / (args.edge_samples - 1)) for i in range(args.edge_samples)]
    axis_y = [(-half_y + 2.0 * half_y * i / (args.edge_samples - 1)) for i in range(args.edge_samples)]
    edge_points = (
        [(x, -half_y) for x in axis_x]
        + [(x, half_y) for x in axis_x]
        + [(-half_x, y) for y in axis_y]
        + [(half_x, y) for y in axis_y]
    )
    lat0 = math.radians(plan["center_lat"])
    lon0 = math.radians(plan["center_lon"])
    geographic = [inverse_aeqd(x, y, lat0, lon0) for x, y in edge_points]
    lon = [math.degrees(point[0]) for point in geographic]
    lat = [math.degrees(point[1]) for point in geographic]
    border_m = plan["forcing_border_km"] * 1000.0
    mid_lat = 0.5 * (min(lat) + max(lat))
    dlat = math.degrees(border_m / EARTH_RADIUS_M)
    dlon = math.degrees(border_m / (EARTH_RADIUS_M * max(math.cos(math.radians(mid_lat)), 0.1)))
    dlat_grid = plan["forcing_grid_spacing_deg"]
    dlon_grid = plan["forcing_grid_spacing_deg"]
    lat_min = align(min(lat) - dlat, dlat_grid, "down")
    lat_max = align(max(lat) + dlat, dlat_grid, "up")
    lon_min = align(min(lon) - dlon, dlon_grid, "down")
    lon_max = align(max(lon) + dlon, dlon_grid, "up")
    target = (
        f"geolatlon,{round(lon_min * 1_000_000)},{round(lat_min * 1_000_000)},"
        f"{round(lon_max * 1_000_000)},{round(lat_max * 1_000_000)},"
        f"{round(dlon_grid * 1_000_000)},{round(dlat_grid * 1_000_000)}"
    )
    if args.verbose:
        print(
            f"fieldextra bounds including {plan['forcing_border_km']:g} km forcing margin: "
            f"lat={lat_min:.5f}..{lat_max:.5f}, lon={lon_min:.5f}..{lon_max:.5f}",
            file=sys.stderr,
        )
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
