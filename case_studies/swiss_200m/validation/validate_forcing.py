#!/usr/bin/env python3
"""Fail on a malformed hicarprep target forcing record."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys

import netCDF4
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from preprocessing.hicarprep.boundary import validate_boundary_sequence
from preprocessing.hicarprep.pipeline import forcing_geometry_for_serialization
from preprocessing.hicarprep.products import sha256


REQUIRED = (
    "P", "T", "QV", "QC", "QI", "U", "V", "W", "SST",
    "HFL", "HHL", "HSURF", "FR_LAND",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forcing-file", type=Path, required=True)
    parser.add_argument("--boundary-file", type=Path, required=True)
    parser.add_argument("--static-file", type=Path, required=True)
    parser.add_argument("--expected-valid-time")
    args = parser.parse_args()

    forcing_sha256 = sha256(args.forcing_file)
    static_sha256 = sha256(args.static_file)
    with netCDF4.Dataset(args.forcing_file) as forcing, netCDF4.Dataset(args.static_file) as static:
        if getattr(forcing, "product_type", "") != "hicarprep_target_forcing_record":
            raise SystemExit("forcing was not produced by hicarprep")
        if getattr(forcing, "water_representation", "") != "dry-air mixing ratio":
            raise SystemExit("forcing moisture is not in HICAR dry-air mixing ratios")
        if str(getattr(forcing, "static_sha256", "")) != static_sha256:
            raise SystemExit("forcing does not belong to the supplied runtime domain")
        missing = sorted(set(REQUIRED) - set(forcing.variables))
        if missing:
            raise SystemExit("missing variables: " + ", ".join(missing))
        if forcing["time"].size != 1:
            raise SystemExit("forcing record must contain exactly one time")
        value = netCDF4.num2date(
            forcing["time"][0], forcing["time"].units,
            calendar=getattr(forcing["time"], "calendar", "standard"),
        )
        valid = datetime(value.year, value.month, value.day, value.hour, value.minute, value.second)
        if args.expected_valid_time and valid != datetime.fromisoformat(
            args.expected_valid_time.replace("Z", "")
        ):
            raise SystemExit(f"forcing time is {valid.isoformat()}, expected {args.expected_valid_time}")
        for name in REQUIRED:
            values = np.ma.asarray(forcing[name][:]).filled(np.nan)
            if not np.isfinite(values).all():
                raise SystemExit(f"{name} contains non-finite values")
        pressure = np.asarray(forcing["P"][:])
        temperature = np.asarray(forcing["T"][:])
        qv = np.asarray(forcing["QV"][:])
        u = np.asarray(forcing["U"][:])
        v = np.asarray(forcing["V"][:])
        w = np.asarray(forcing["W"][:])
        if pressure.min() < 100.0 or pressure.max() > 120_000.0:
            raise SystemExit("pressure range is implausible")
        if temperature.min() < 150.0 or temperature.max() > 350.0:
            raise SystemExit("temperature range is implausible")
        if qv.min() < 0.0 or qv.max() > 0.1:
            raise SystemExit("water-vapour range is implausible")
        if np.max(np.hypot(u, v)) > 200.0:
            raise SystemExit("horizontal-wind speed exceeds 200 m s-1")
        if np.max(np.abs(w)) > 100.0:
            raise SystemExit("vertical-wind magnitude exceeds 100 m s-1")
        if forcing["W"].dimensions != ("time", "z", "y_1", "x_1"):
            raise SystemExit("W must be defined on target HFL mass levels")
        if str(getattr(forcing, "target_w_vertical_coordinate", "")) != (
            "authoritative_static_HFL"
        ):
            raise SystemExit("W lacks the authoritative target-HFL coordinate contract")
        if str(getattr(forcing, "target_w_terrain_wind_basis", "")) != (
            "HICAR_grid_relative"
        ):
            raise SystemExit("terrain-adjusted W was not built from HICAR grid-relative winds")
        if forcing["SST"].dimensions != ("time", "y_1", "x_1"):
            raise SystemExit("SST must be a two-dimensional time-dependent target field")
        if str(getattr(forcing["SST"], "units", "")).strip().lower() not in {"k", "kelvin"}:
            raise SystemExit("SST units must be kelvin")
        if "landmask" not in static.variables:
            raise SystemExit("runtime domain lacks landmask required for water-only SST validation")
        water = np.asarray(static["landmask"][:]) < 0.5
        if not np.any(water):
            raise SystemExit("runtime domain has no water cells for SST forcing")
        sst = np.asarray(forcing["SST"][0])
        if np.any((sst[water] < 180.0) | (sst[water] > 350.0)):
            raise SystemExit("SST lies outside 180..350 K on water cells")
        if not str(getattr(forcing, "sst_source_sha256", "")):
            raise SystemExit("forcing lacks exact valid-time SST provenance")
        hhl = np.asarray(forcing["HHL"][:])
        hfl = np.asarray(forcing["HFL"][:])
        if np.any(np.diff(hhl, axis=0) <= 0.0) or np.any(np.diff(hfl, axis=0) <= 0.0):
            raise SystemExit("forcing heights are not strictly bottom-to-top")
        if "HHL" not in static.variables or "HFL" not in static.variables:
            raise SystemExit("runtime domain lacks authoritative HHL/HFL geometry")
        static_hhl, static_hfl = forcing_geometry_for_serialization(
            static["HHL"][:], static["HFL"][:]
        )
        if not np.array_equal(hhl, static_hhl) or not np.array_equal(hfl, static_hfl):
            raise SystemExit("forcing HHL/HFL differ from the authoritative runtime geometry")
        if str(getattr(forcing, "geometry_serialization", "")) != (
            "static_sleve_with_one_ulp_top_cover"
        ):
            raise SystemExit("forcing lacks the required top-cover serialization contract")
        if not np.array_equal(forcing["lat_1"][:], static["lat"][:]) or not np.array_equal(
            forcing["lon_1"][:], static["lon"][:]
        ):
            raise SystemExit("forcing grid differs from the HICAR runtime domain")
    boundary = validate_boundary_sequence([args.boundary_file], minimum_states=1)
    with netCDF4.Dataset(args.boundary_file) as boundary_file:
        if str(getattr(boundary_file, "initial_condition_sha256", "")) != forcing_sha256:
            raise SystemExit("sparse LBC does not belong to the supplied forcing record")
        if str(getattr(boundary_file, "static_sha256", "")) != static_sha256:
            raise SystemExit("sparse LBC does not belong to the supplied runtime domain")
    boundary_time = datetime.fromisoformat(
        str(boundary["first_valid_time"]).replace("Z", "+00:00")
    ).replace(tzinfo=None)
    if boundary_time != valid:
        raise SystemExit(
            f"boundary time is {boundary_time.isoformat()}, forcing time is {valid.isoformat()}"
        )
    print(f"PASS {args.forcing_file} {args.boundary_file} {valid.isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
