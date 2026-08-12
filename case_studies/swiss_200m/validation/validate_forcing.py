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
    parser.add_argument(
        "--boundary-file",
        type=Path,
        help="optional sparse-LBC companion when that relaxation path is selected",
    )
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
        if not str(getattr(forcing, "sst_native_source_sha256", "")):
            raise SystemExit("forcing lacks native SST-source provenance")
        if str(getattr(forcing, "sst_target_product_sha256", "")) != str(
            getattr(forcing, "sst_source_sha256", "")
        ):
            raise SystemExit("forcing target-SST product identity is inconsistent")
        if str(getattr(forcing, "sst_source_variable", "")) != "SKT":
            raise SystemExit("forcing SST does not identify native SKT as its source")
        sst_valid_time = datetime.fromisoformat(
            str(getattr(forcing, "sst_valid_time", "")).replace("Z", "+00:00")
        ).replace(tzinfo=None)
        if sst_valid_time != valid:
            raise SystemExit("forcing SST provenance time differs from forcing time")
        if str(getattr(forcing, "sst_remap_policy", "")) != (
            "same-surface water support; RBF baseline on land"
        ):
            raise SystemExit("forcing lacks the same-surface SST remapping contract")
        water_cell_count = int(getattr(forcing, "sst_water_cell_count", -1))
        local_fallback_count = int(
            getattr(forcing, "sst_water_local_fallback_count", -1)
        )
        global_fallback_count = int(
            getattr(forcing, "sst_water_global_fallback_count", -1)
        )
        fallback_distance = float(
            getattr(forcing, "sst_maximum_fallback_distance_km", np.nan)
        )
        if water_cell_count != int(np.sum(water)):
            raise SystemExit("SST water-cell diagnostics disagree with the runtime domain")
        if not (
            0
            <= global_fallback_count
            <= local_fallback_count
            <= water_cell_count
        ):
            raise SystemExit("SST fallback counts are inconsistent")
        if not np.isfinite(fallback_distance) or fallback_distance < 0.0:
            raise SystemExit("SST fallback distance is missing or invalid")
        for name in ("SST_global_fallback_mask", "SST_global_fallback_distance_km"):
            if name not in forcing.variables:
                raise SystemExit(f"forcing lacks {name}")
        if forcing["SST_global_fallback_mask"].dimensions != ("y_1", "x_1") or (
            forcing["SST_global_fallback_distance_km"].dimensions
            != ("y_1", "x_1")
        ):
            raise SystemExit("SST fallback provenance is not on the target grid")
        global_fallback_mask = np.asarray(
            forcing["SST_global_fallback_mask"][:], dtype=bool
        )
        global_fallback_distance = np.asarray(
            np.ma.asarray(forcing["SST_global_fallback_distance_km"][:]).filled(
                np.nan
            ),
            dtype=np.float64,
        )
        if np.any(global_fallback_mask & ~water):
            raise SystemExit("SST global fallback includes target land cells")
        if int(np.count_nonzero(global_fallback_mask)) != global_fallback_count:
            raise SystemExit("SST global fallback mask disagrees with its count")
        if np.any(~np.isfinite(global_fallback_distance[global_fallback_mask])) or (
            np.any(global_fallback_distance[global_fallback_mask] < 0.0)
        ):
            raise SystemExit("SST global fallback distances are invalid")
        if np.any(np.isfinite(global_fallback_distance[~global_fallback_mask])):
            raise SystemExit("SST fallback distances exist outside the fallback mask")
        expected_global_maximum = (
            float(np.max(global_fallback_distance[global_fallback_mask]))
            if global_fallback_count
            else 0.0
        )
        reported_global_maximum = float(
            getattr(forcing, "sst_maximum_global_fallback_distance_km", np.nan)
        )
        if not np.isclose(
            reported_global_maximum,
            expected_global_maximum,
            rtol=0.0,
            atol=1.0e-9,
        ):
            raise SystemExit(
                "SST global fallback maximum disagrees with its distance field"
            )
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
    if args.boundary_file is not None:
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
    print(
        f"PASS {args.forcing_file}"
        + (f" {args.boundary_file}" if args.boundary_file is not None else "")
        + f" {valid.isoformat()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
