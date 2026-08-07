#!/usr/bin/env python3
"""Assess V29 cloud and land-state anomalies on one REA-L target grid.

This is deliberately an observational causal screen: every HICAR and REA-L
land/cloud state is sampled at the same valid time and evaluated on the same
REA-L cells.  It never compares HICAR flux snapshots to REA-L interval means;
those require a separate interval diagnostic before a correction is selected.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import netCDF4
import numpy as np
from scipy.spatial import cKDTree

from build_rea_l_land_initialization import remap_layer_integrated_soil_water, remap_soil_temperature


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as out:
        json.dump(payload, out, indent=2, sort_keys=True)
        out.write("\n")
        temporary = out.name
    os.replace(temporary, path)


def hicar_times(history: netCDF4.Dataset) -> list[datetime]:
    time = history.variables["time"]
    return [datetime(v.year, v.month, v.day, v.hour, v.minute, v.second)
            for v in netCDF4.num2date(time[:], time.units, calendar=getattr(time, "calendar", "standard"))]


def sample_indices(lat: np.ndarray, lon: np.ndarray, target_lat: np.ndarray, target_lon: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    valid = np.isfinite(lat) & np.isfinite(lon)
    if not np.any(valid):
        raise ValueError("HICAR coordinate grid has no finite points")
    source_flat = np.flatnonzero(valid.ravel())
    tree = cKDTree(np.column_stack((lat.ravel()[source_flat], lon.ravel()[source_flat])))
    distance, selected = tree.query(np.column_stack((target_lat.ravel(), target_lon.ravel())), k=1)
    return source_flat[selected], distance.reshape(target_lat.shape)


def sample_2d(values: np.ndarray, index: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    return np.asarray(values, dtype=np.float64).reshape(-1)[index].reshape(shape)


def sample_layer(values: np.ndarray, index: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64)
    return array.reshape(array.shape[0], -1)[:, index].reshape((array.shape[0],) + shape)


def means(error: np.ndarray, mask: np.ndarray) -> dict[str, float]:
    selected = np.asarray(error, dtype=np.float64)[mask]
    if not selected.size or not np.all(np.isfinite(selected)):
        raise ValueError("common mask selected non-finite or no values")
    return {"bias": float(np.mean(selected)), "rmse": float(np.sqrt(np.mean(selected ** 2)))}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hicar-history", required=True, type=Path)
    parser.add_argument("--hicar-static", required=True, type=Path)
    parser.add_argument("--land-publication", required=True, type=Path)
    parser.add_argument("--land-dir", required=True, type=Path)
    parser.add_argument("--surface-reference-dir", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    publication = json.loads(args.land_publication.read_text())
    if publication.get("status") != "PASS" or len(publication.get("records", [])) != 9:
        raise SystemExit("land-state publication is not a complete PASS")
    if args.report.exists() or Path(f"{args.report}.ready").exists():
        raise SystemExit(f"refusing to overwrite published report: {args.report}")
    with netCDF4.Dataset(args.hicar_static) as static, netCDF4.Dataset(args.hicar_history) as history:
        active = (np.asarray(static.variables["landmask"][:]) > 0) & (np.asarray(static.variables["landuse"][:]) != 16) & (np.asarray(static.variables["landuse"][:]) != 24)
        required = {"lat", "lon", "cldfrac", "tsfe", "soil_column_total_water", "soil_temperature", "soil_water_content"}
        missing = required - set(history.variables)
        if missing:
            raise SystemExit(f"HICAR history lacks {sorted(missing)}")
        times = hicar_times(history)
        if [r["valid_time"] for r in publication["records"]] != [v.isoformat() for v in times]:
            raise SystemExit("HICAR and published REA-L land timestamps differ")
        first_land = Path(publication["records"][0]["payload"])
        with netCDF4.Dataset(first_land) as land:
            target_lat, target_lon = np.asarray(land.variables["lat_1"][:]), np.asarray(land.variables["lon_1"][:])
        index, distance = sample_indices(np.asarray(history.variables["lat"][:]), np.asarray(history.variables["lon"][:]), target_lat, target_lon)
        # The fieldextra source grid deliberately extends beyond the HICAR
        # boundary/relaxation domain. Retain only cells with a nearby HICAR
        # sample, rather than incorrectly assigning every outer source cell to
        # the closest HICAR edge cell.
        covered = distance <= 0.01
        target_active = (sample_2d(active, index, target_lat.shape) > 0) & covered
        if np.count_nonzero(target_active) < 100:
            raise SystemExit("common HICAR/REA-L active mask has insufficient coverage")
        records, initial = [], None
        for position, (valid, land_record) in enumerate(zip(times, publication["records"])):
            land_path = Path(land_record["payload"])
            surface_path = args.surface_reference_dir / f"rea_l_surface_reference_{valid:%Y%m%d_%H%M}.nc"
            if not surface_path.is_file() or not Path(f"{surface_path}.ready").is_file():
                raise SystemExit(f"missing published surface reference: {surface_path}")
            with netCDF4.Dataset(land_path) as land, netCDF4.Dataset(surface_path) as surface:
                source_temp = remap_soil_temperature(np.asarray(land.variables["T_SO"][:]))
                source_vwc, source_mass = remap_layer_integrated_soil_water(np.asarray(land.variables["W_SO"][:]))
                source_column = np.sum(source_mass, axis=0)
                common = target_active & np.isfinite(land.variables["SKT"][:]) & np.isfinite(source_column)
                hicar_temp = sample_layer(history.variables["soil_temperature"][position], index, target_lat.shape)
                hicar_vwc = sample_layer(history.variables["soil_water_content"][position], index, target_lat.shape)
                fields = {
                    "skin_temperature": (sample_2d(history.variables["tsfe"][position], index, target_lat.shape), np.asarray(land.variables["SKT"][:])),
                    "soil_column_water": (sample_2d(history.variables["soil_column_total_water"][position], index, target_lat.shape), source_column),
                    "soil_temperature": (np.mean(hicar_temp, axis=0), np.mean(source_temp, axis=0)),
                    "soil_vwc": (np.mean(hicar_vwc, axis=0), np.mean(source_vwc, axis=0)),
                }
                if "cloud_area_fraction_ref" in surface.variables:
                    fields["cloud_fraction"] = (
                        sample_2d(history.variables["cldfrac"][position], index, target_lat.shape),
                        np.asarray(surface.variables["cloud_area_fraction_ref"][0]),
                    )
                errors = {name: means(left - right, common) for name, (left, right) in fields.items()}
                if initial is None:
                    initial = errors
                records.append({"valid_time": valid.isoformat(), "common_cells": int(np.count_nonzero(common)),
                                "errors": errors,
                                "change_in_bias_from_initial": {
                                    name: None if name not in initial else value["bias"] - initial[name]["bias"]
                                    for name, value in errors.items()
                                }})
    payload = {"schema_version": 1, "status": "PASS_NON_PROMOTING", "decision": "INTERVAL_FLUX_DIAGNOSTIC_REQUIRED",
               "reason": "Common-grid, simultaneous cloud and land states are now available; causal correction selection still requires HICAR interval-mean radiation and turbulent flux diagnostics.",
               "common_grid": "REA-L 320x640 target grid; nearest HICAR 200 m sample, active USGS land mask, finite source cloud/land state",
               "maximum_nearest_sample_distance_degrees": float(np.max(distance)),
               "maximum_retained_sample_distance_degrees": float(np.max(distance[covered])),
               "covered_rea_l_cells": int(np.count_nonzero(covered)), "hicar_history": str(args.hicar_history.resolve()),
               "hicar_history_sha256": sha256(args.hicar_history), "land_publication": str(args.land_publication.resolve()),
               "land_publication_sha256": sha256(args.land_publication), "records": records}
    atomic_json(args.report, payload)
    Path(f"{args.report}.ready").touch()
    print(f"PASS_NON_PROMOTING: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
