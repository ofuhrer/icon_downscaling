#!/usr/bin/env python3
"""Compare two HICAR REA-L cold-start static files on their common land cells."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import netCDF4
import numpy as np

from build_rea_l_land_initialization import HICAR_SOIL_BOUNDS_M, WATER_DENSITY_KG_M3


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def field_metrics(candidate: np.ndarray, baseline: np.ndarray) -> dict:
    candidate = np.asarray(candidate, dtype=np.float64)
    baseline = np.asarray(baseline, dtype=np.float64)
    finite = np.isfinite(candidate) & np.isfinite(baseline)
    if not np.all(finite):
        raise ValueError("comparison contains non-finite values")
    difference = candidate - baseline
    return {
        "count": int(difference.size),
        "baseline_mean": float(np.mean(baseline)),
        "candidate_mean": float(np.mean(candidate)),
        "mean_difference": float(np.mean(difference)),
        "rmse_difference": float(np.sqrt(np.mean(difference * difference))),
        "absolute_difference_p95": float(np.quantile(np.abs(difference), 0.95)),
        "difference_range": [float(np.min(difference)), float(np.max(difference))],
    }


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--native-land-state", type=Path)
    parser.add_argument("--diagnostic-lapse-rate-k-m", type=float, default=-0.0065)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    for path in (args.baseline, args.candidate):
        if not path.is_file() or not path.stat().st_size:
            raise SystemExit(f"missing or empty input: {path}")
    if args.output.exists() or Path(f"{args.output}.ready").exists():
        raise SystemExit(f"refusing to overwrite publication: {args.output}")

    with netCDF4.Dataset(args.baseline) as baseline, netCDF4.Dataset(args.candidate) as candidate:
        for name in ("lat", "lon", "landmask", "soil_type", "landuse"):
            if not np.array_equal(baseline.variables[name][:], candidate.variables[name][:]):
                raise ValueError(f"candidate changed static identity field {name}")
        land = np.asarray(candidate.variables["landmask"][:]) > 0
        landuse = np.asarray(candidate.variables["landuse"][:])
        active_soil = land & (landuse != 16) & (landuse != 24)
        metrics = {}
        for name in ("surface_temperature", "swe", "snow_height"):
            metrics[name] = field_metrics(
                np.asarray(candidate.variables[name][:])[land],
                np.asarray(baseline.variables[name][:])[land],
            )
        for name in ("soil_temperature", "soil_vwc"):
            candidate_values = np.asarray(candidate.variables[name][:])[:, active_soil]
            baseline_values = np.asarray(baseline.variables[name][:])[:, active_soil]
            metrics[name] = field_metrics(candidate_values, baseline_values)
            metrics[name]["layers"] = [
                field_metrics(candidate_values[index], baseline_values[index])
                for index in range(candidate_values.shape[0])
            ]
        thickness = np.diff(HICAR_SOIL_BOUNDS_M)[:, np.newaxis]
        candidate_column = np.sum(
            np.asarray(candidate.variables["soil_vwc"][:])[:, active_soil]
            * thickness
            * WATER_DENSITY_KG_M3,
            axis=0,
        )
        baseline_column = np.sum(
            np.asarray(baseline.variables["soil_vwc"][:])[:, active_soil]
            * thickness
            * WATER_DENSITY_KG_M3,
            axis=0,
        )
        metrics["soil_column_total_water"] = field_metrics(candidate_column, baseline_column)

    payload = {
        "schema_version": 1,
        "status": "PASS",
        "assessor": str(Path(__file__).resolve()),
        "assessor_sha256": sha256(Path(__file__).resolve()),
        "baseline": str(args.baseline.resolve()),
        "baseline_sha256": sha256(args.baseline),
        "candidate": str(args.candidate.resolve()),
        "candidate_sha256": sha256(args.candidate),
        "active_land_cells": int(np.count_nonzero(active_soil)),
        "metrics": metrics,
    }
    if args.native_land_state is not None:
        if (
            not args.native_land_state.is_file()
            or not Path(f"{args.native_land_state}.ready").is_file()
        ):
            raise ValueError(f"native land state is not published: {args.native_land_state}")
        with (
            netCDF4.Dataset(args.candidate) as candidate,
            netCDF4.Dataset(args.native_land_state) as native,
        ):
            for name in ("lat", "lon"):
                if not np.allclose(
                    candidate.variables[name][:], native.variables[name][:], atol=1.0e-7
                ):
                    raise ValueError(f"native land state differs on {name}")
            land = np.asarray(candidate.variables["landmask"][:]) > 0
            target_topography = np.asarray(candidate.variables["topo"][:])[land]
            source_topography = np.asarray(native.variables["HSURF"][:])[land]
        elevation = field_metrics(target_topography, source_topography)
        correction = args.diagnostic_lapse_rate_k_m * (target_topography - source_topography)
        payload["height_correction_diagnostic"] = {
            "native_land_state": str(args.native_land_state.resolve()),
            "native_land_state_sha256": sha256(args.native_land_state),
            "lapse_rate_k_m": args.diagnostic_lapse_rate_k_m,
            "target_minus_icon_topography_m": elevation,
            "temperature_correction_k": {
                "mean": float(np.mean(correction)),
                "rmse": float(np.sqrt(np.mean(correction * correction))),
                "absolute_p95": float(np.quantile(np.abs(correction), 0.95)),
                "range": [float(np.min(correction)), float(np.max(correction))],
            },
        }
    write_json_atomic(args.output, payload)
    Path(f"{args.output}.ready").write_text(sha256(args.output) + "\n")
    print(f"PASS: published {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
