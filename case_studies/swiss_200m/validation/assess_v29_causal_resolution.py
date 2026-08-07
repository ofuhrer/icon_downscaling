#!/usr/bin/env python3
"""Assess the V29 causal baseline on a common REA-L grid.

The script never runs HICAR.  It maps each REA-L cell to its nearest valid
200-m HICAR active-soil cell, area-weights every comparison on that common
grid, and reconstructs HICAR 3-hour flux means from seven 30-minute samples.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import netCDF4
import numpy as np
from scipy.spatial import cKDTree

W_SO_BOUNDS = np.array((0.0, .01, .03, .09, .27, .81, 2.43, 7.29, 21.87))
FLUXES = ("swtb", "swtd", "hfss", "hfls")
REF_FLUXES = {"swtb": "sw_direct_down_interval_ref", "swtd": "sw_diffuse_down_interval_ref", "hfss": "sensible_heat_flux_interval_ref", "hfls": "latent_heat_flux_interval_ref"}

def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()

def atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as f:
        json.dump(value, f, indent=2, sort_keys=True); f.write("\n"); temp = f.name
    os.replace(temp, path)

def nc_times(ds: netCDF4.Dataset) -> list[datetime]:
    t = ds.variables["time"]
    return [datetime(x.year, x.month, x.day, x.hour, x.minute, x.second)
            for x in netCDF4.num2date(t[:], t.units, calendar=getattr(t, "calendar", "standard"))]

def weighted(values: np.ndarray, mask: np.ndarray, latitude: np.ndarray) -> float:
    v = np.asarray(np.ma.filled(values, np.nan), dtype=float)
    good = mask & np.isfinite(v)
    if not np.any(good): raise ValueError("common grid has no finite values")
    w = np.cos(np.deg2rad(latitude))
    return float(np.sum(v[good] * w[good]) / np.sum(w[good]))

def history_index(histories: list[netCDF4.Dataset]) -> dict[datetime, tuple[netCDF4.Dataset, int]]:
    """Index a published, potentially segmented HICAR history without overlap."""
    indexed: dict[datetime, tuple[netCDF4.Dataset, int]] = {}
    for history in histories:
        for index, valid in enumerate(nc_times(history)):
            if valid in indexed:
                raise ValueError(f"HICAR history has duplicate {valid.isoformat()}")
            indexed[valid] = (history, index)
    return indexed

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--contract", required=True, type=Path)
    p.add_argument("--static-file", required=True, type=Path)
    p.add_argument("--hicar-history", required=True, type=Path, action="append")
    p.add_argument("--land-publication", required=True, type=Path)
    p.add_argument("--reference-dir", required=True, type=Path)
    p.add_argument("--report", required=True, type=Path)
    a = p.parse_args()
    contract = json.loads(a.contract.read_text())
    if contract.get("status") != "PREDECLARED_NOT_YET_RUN" or contract.get("baseline", {}).get("output_profile") != "causal_surface_30min":
        raise SystemExit("invalid causal-resolution contract")
    land_pub = json.loads(a.land_publication.read_text())
    if land_pub.get("status") != "PASS" or len(land_pub.get("records", [])) != 9:
        raise SystemExit("land history is not a nine-record PASS publication")
    records = land_pub["records"]
    with netCDF4.Dataset(a.static_file) as st, ExitStack() as stack:
        histories = [stack.enter_context(netCDF4.Dataset(path)) for path in a.hicar_history]
        hi = histories[0]
        indexed_history = history_index(histories)
        land = (np.asarray(st.variables["landmask"][:]) > 0) & ~np.isin(np.asarray(st.variables["landuse"][:]), (16, 24))
        lat = np.asarray(hi.variables["lat"][:]); lon = np.asarray(hi.variables["lon"][:])
        if lat.shape != land.shape or lon.shape != land.shape: raise SystemExit("HICAR coordinates/static mask mismatch")
        tree = cKDTree(np.column_stack((lat[land], lon[land])))
        flat_active = np.flatnonzero(land)
        required = {"cldfrac", "precipitation", "snowfall", "graupel", "tsfe", "soil_column_total_water", *FLUXES}
        for history in histories:
            missing = sorted(required - set(history.variables))
            if missing: raise SystemExit("HICAR causal history lacks: " + ", ".join(missing))
            if np.asarray(history.variables["lat"][:]).shape != land.shape or np.asarray(history.variables["lon"][:]).shape != land.shape:
                raise SystemExit("HICAR coordinates/static mask mismatch")
        result = []
        base = None
        distance_summary = None
        for item in records:
            valid = datetime.fromisoformat(item["valid_time"])
            land_path = Path(item["payload"])
            with netCDF4.Dataset(land_path) as src:
                rlat = np.asarray(src.variables["lat_1"][:]); rlon = np.asarray(src.variables["lon_1"][:])
                _, nearest = tree.query(np.column_stack((rlat.ravel(), rlon.ravel())), k=1)
                mapped = flat_active[nearest].reshape(rlat.shape)
                wso = np.asarray(src.variables["W_SO"][:], dtype=float)
                source_water = np.sum(wso[:6], axis=0) + wso[6] * ((1.5 - W_SO_BOUNDS[6]) / (W_SO_BOUNDS[7] - W_SO_BOUNDS[6]))
                mask = np.isfinite(np.asarray(src.variables["SKT"][:])) & np.isfinite(source_water)
                if base is None:
                    base = {"source_skin": weighted(src.variables["SKT"][:], mask, rlat), "source_water": weighted(source_water, mask, rlat)}
                    distance_summary = {"maximum_degrees": float(np.max(tree.query(np.column_stack((rlat.ravel(), rlon.ravel())), k=1)[0])), "median_degrees": float(np.median(tree.query(np.column_stack((rlat.ravel(), rlon.ravel())), k=1)[0]))}
                if valid not in indexed_history:
                    raise ValueError(f"HICAR history lacks unique {valid.isoformat()}")
                end_history, end_index = indexed_history[valid]
                end = (end_history, end_index)
                def sampled(name: str, source: tuple[netCDF4.Dataset, int]) -> np.ndarray:
                    history, index = source
                    return np.asarray(history.variables[name][index]).reshape(-1)[mapped.ravel()].reshape(mask.shape)
                endpoint = {name: weighted(sampled(name, end), mask, rlat) for name in ("cldfrac", "precipitation", "snowfall", "graupel", "tsfe", "soil_column_total_water")}
                source_skin = weighted(src.variables["SKT"][:], mask, rlat)
                source_water_mean = weighted(source_water, mask, rlat)
                if valid == datetime.fromisoformat(records[0]["valid_time"]):
                    base.update(hicar_skin=endpoint["tsfe"], hicar_water=endpoint["soil_column_total_water"])
                    result.append({"valid_time": valid.isoformat(), "initial_state": {"hicar_skin": endpoint["tsfe"], "rea_l_skin": source_skin, "hicar_soil_water": endpoint["soil_column_total_water"], "rea_l_soil_water": source_water_mean}})
                    continue
                samples = []
                for seconds_before_end in range(10800, -1, -1800):
                    sample_time = valid - timedelta(seconds=seconds_before_end)
                    if sample_time not in indexed_history:
                        raise SystemExit(f"HICAR causal history lacks interval sample {sample_time.isoformat()}")
                    samples.append(indexed_history[sample_time])
                means = {}
                for name in FLUXES:
                    series = np.array([weighted(sampled(name, source), mask, rlat) for source in samples])
                    means[name] = float(np.trapz(series, dx=1800.0) / 10800.0)
                ref_path = a.reference_dir / f"rea_l_surface_reference_{valid:%Y%m%d_%H%M}.nc"
                with netCDF4.Dataset(ref_path) as ref:
                    ref_cloud = weighted(ref.variables["cloud_area_fraction_ref"][0], mask, rlat)
                    ref_rain = weighted(ref.variables["rain_interval_ref"][0], mask, rlat)
                    ref_flux = {name: weighted(ref.variables[REF_FLUXES[name]][0], mask, rlat) for name in FLUXES}
                hicar_rain = endpoint["precipitation"] - endpoint["snowfall"] - endpoint["graupel"]
                row = {"valid_time": valid.isoformat(), "cloud_deficit": endpoint["cldfrac"] - ref_cloud, "rain_deficit": hicar_rain - ref_rain,
                       "land_divergence": {"skin_temperature_change_difference": (endpoint["tsfe"] - source_skin) - (base["hicar_skin"] - base["source_skin"] if "hicar_skin" in base else 0.0), "soil_water_change_difference": (endpoint["soil_column_total_water"] - source_water_mean) - (base["hicar_water"] - base["source_water"] if "hicar_water" in base else 0.0)},
                       "flux_means": {name: {"hicar": means[name], "rea_l": ref_flux[name], "difference": means[name] - ref_flux[name]} for name in FLUXES}}
                result.append(row)
    cloud = [r["cloud_deficit"] <= -.05 and r["rain_deficit"] <= -.05 for r in result[1:]]
    land = [abs(r["land_divergence"]["skin_temperature_change_difference"]) >= 1 or abs(r["land_divergence"]["soil_water_change_difference"]) >= 3 for r in result[1:]]
    first_cloud = next((i for i in range(len(cloud) - 1) if cloud[i] and cloud[i+1]), None)
    first_land = next((i for i in range(len(land) - 1) if land[i] and land[i+1]), None)
    decision = "CLOUD_PATHWAY_CORRECTION" if first_cloud is not None and (first_land is None or first_cloud < first_land) else "LAND_PATHWAY_CORRECTION" if first_land is not None and (first_cloud is None or first_land < first_cloud) else "REQUIRE_DISCRIMINATING_CORRECTION_PAIR"
    payload = {"schema_version": 1, "status": "PASS_NON_PROMOTING", "decision": decision, "contract_sha256": digest(a.contract), "hicar_history_sha256": [digest(path) for path in a.hicar_history], "land_publication_sha256": digest(a.land_publication), "common_grid": {"shape": list(rlat.shape), "active_cells": int(np.count_nonzero(mask)), "nearest_hicar_distance": distance_summary}, "records": result}
    atomic(a.report, payload); Path(f"{a.report}.ready").touch(); print(f"PASS_NON_PROMOTING: {a.report}")
    return 0
if __name__ == "__main__": raise SystemExit(main())
