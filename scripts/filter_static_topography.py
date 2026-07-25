#!/usr/bin/env python3
"""Create a terrain-filtered sensitivity copy of a HICAR static NetCDF file."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

import netCDF4
import numpy as np

from hicar_static_topography import (
    block_mean_change_metrics,
    filter_land_topography,
    nominal_shapiro_response,
    terrain_metrics,
)


FILTER_METHOD = "land-aware high-order Shapiro"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def put_2d(ds: netCDF4.Dataset, name: str, data: np.ndarray, long_name: str) -> None:
    if name in ds.variables:
        raise ValueError(f"input already contains diagnostic variable {name!r}; refusing to stack filters")
    var = ds.createVariable(name, "f4", ("y", "x"), zlib=True)
    var[:, :] = np.asarray(data, dtype=np.float32)
    var.units = "m"
    var.long_name = long_name
    var.coordinates = "lon lat"


def verify_unchanged_variables(source: Path, candidate: Path, changed: set[str]) -> list[str]:
    """Verify exact values and schemas for every existing non-terrain variable."""
    verified = []
    with netCDF4.Dataset(source) as reference, netCDF4.Dataset(candidate) as result:
        for name, reference_var in reference.variables.items():
            if name in changed:
                continue
            if name not in result.variables:
                raise ValueError(f"filtered file lost non-terrain variable {name!r}")
            result_var = result.variables[name]
            if reference_var.dimensions != result_var.dimensions or reference_var.dtype != result_var.dtype:
                raise ValueError(f"filtered file changed schema of non-terrain variable {name!r}")
            if not np.ma.allequal(reference_var[:], result_var[:]):
                raise ValueError(f"filtered file changed values of non-terrain variable {name!r}")
            verified.append(name)
    return verified


def filter_static_file(
    source: Path,
    output: Path,
    *,
    passes: int,
    order: int,
    strength: float,
    water_policy: str,
    sea_level_m: float,
    overwrite: bool = False,
) -> dict[str, object]:
    source = source.resolve()
    output = output.resolve()
    if source == output:
        raise ValueError("input and output must be different files")
    if not source.is_file():
        raise FileNotFoundError(source)
    if output.exists() and not overwrite:
        raise FileExistsError(f"output exists (use --overwrite to replace it): {output}")

    output.parent.mkdir(parents=True, exist_ok=True)
    ready = Path(str(output) + ".ready")
    ready.unlink(missing_ok=True)
    source_digest = sha256(source)

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent, delete=False) as handle:
            temporary_path = Path(handle.name)
        shutil.copy2(source, temporary_path)

        with netCDF4.Dataset(temporary_path, "r+") as ds:
            required = {"topo", "landmask"}
            missing = sorted(required - set(ds.variables))
            if missing:
                raise ValueError(f"input is missing required variables: {', '.join(missing)}")
            if hasattr(ds, "topography_filter_method"):
                raise ValueError("input is already marked as terrain-filtered; start from the unfiltered static file")

            landmask = np.asarray(ds.variables["landmask"][:])
            has_blend = all(name in ds.variables for name in ("topo_highres", "topo_driving", "topo_blend_weight"))
            partial_blend = any(name in ds.variables for name in ("topo_highres", "topo_driving", "topo_blend_weight"))
            if partial_blend and not has_blend:
                raise ValueError("boundary-blend diagnostics are incomplete; need topo_highres, topo_driving, and topo_blend_weight")

            terrain_name = "topo_highres" if has_blend else "topo"
            unfiltered = np.ma.asarray(ds.variables[terrain_name][:]).filled(np.nan).astype(np.float32)
            before = terrain_metrics(unfiltered, landmask)
            filtered = filter_land_topography(
                unfiltered,
                landmask,
                passes=passes,
                order=order,
                strength=strength,
                water_policy=water_policy,
                sea_level_m=sea_level_m,
            )
            if hasattr(ds, "hicar_dx_m"):
                grid_spacing_m = float(ds.hicar_dx_m)
            elif "x" in ds.variables and ds.variables["x"].size > 1:
                grid_spacing_m = float(np.median(np.diff(np.asarray(ds.variables["x"][:]))))
            else:
                raise ValueError("input must provide global hicar_dx_m or an x coordinate with at least two points")
            scale_changes = {}
            for scale_km in (1.0, 5.0, 10.0):
                block_cells = max(1, int(round(scale_km * 1000.0 / grid_spacing_m)))
                scale_changes[f"{scale_km:g}_km"] = block_mean_change_metrics(
                    unfiltered, filtered, landmask, block_cells
                )
            land_delta = (filtered - unfiltered)[landmask != 0].astype(np.float64)
            point_change = {
                "mean_change_m": float(np.mean(land_delta)),
                "rms_change_m": float(np.sqrt(np.mean(land_delta * land_delta))),
                "p99_absolute_change_m": float(np.percentile(np.abs(land_delta), 99.0)),
                "max_absolute_change_m": float(np.max(np.abs(land_delta))),
            }
            nominal_response = {
                f"{wavelength:g}_cells": nominal_shapiro_response(order, strength, wavelength) ** passes
                for wavelength in (2.0, 3.0, 4.0, 5.0, 8.0, 10.0, 25.0, 50.0)
            }

            if has_blend:
                driving = np.ma.asarray(ds.variables["topo_driving"][:]).filled(np.nan).astype(np.float32)
                weight = np.ma.asarray(ds.variables["topo_blend_weight"][:]).filled(np.nan).astype(np.float32)
                if driving.shape != filtered.shape or weight.shape != filtered.shape:
                    raise ValueError("boundary-blend terrain fields do not have matching shapes")
                final_topography = ((1.0 - weight) * driving + weight * filtered).astype(np.float32)
                if water_policy == "sea-level":
                    final_topography[landmask == 0] = sea_level_m
                ds.variables["topo_highres"][:, :] = filtered
                put_2d(ds, "topo_highres_unfiltered", unfiltered, "unfiltered high-resolution terrain height")
                put_2d(ds, "topo_highres_filter_delta", filtered - unfiltered, "filtered minus unfiltered high-resolution terrain height")
            else:
                final_topography = filtered
                put_2d(ds, "topo_unfiltered", unfiltered, "unfiltered terrain height")
                put_2d(ds, "topo_filter_delta", filtered - unfiltered, "filtered minus unfiltered terrain height")

            ds.variables["topo"][:, :] = final_topography
            after = terrain_metrics(filtered, landmask)
            timestamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            previous_history = getattr(ds, "history", "")
            ds.history = f"{previous_history}\n{timestamp}: terrain sensitivity created by filter_static_topography.py".strip()
            ds.topography_filter_method = FILTER_METHOD
            ds.topography_filter_passes = passes
            ds.topography_filter_order = order
            ds.topography_filter_strength = strength
            ds.topography_filter_water_policy = water_policy
            ds.topography_filter_sea_level_m = sea_level_m
            ds.topography_filter_processing_order = "filter high-resolution terrain first, then reapply the existing driving-terrain boundary blend"
            ds.topography_filter_source = str(source)
            ds.topography_filter_source_sha256 = source_digest
            ds.topography_filter_before_metrics_json = json.dumps(asdict(before), sort_keys=True)
            ds.topography_filter_after_metrics_json = json.dumps(asdict(after), sort_keys=True)
            ds.topography_filter_point_change_json = json.dumps(point_change, sort_keys=True)
            ds.topography_filter_scale_change_json = json.dumps(scale_changes, sort_keys=True)
            ds.topography_filter_nominal_1d_response_json = json.dumps(nominal_response, sort_keys=True)

        with netCDF4.Dataset(temporary_path) as check:
            for name in ("topo", "landmask", "lat", "lon"):
                if name not in check.variables:
                    raise ValueError(f"filtered file is missing required variable {name!r}")
                if not np.isfinite(np.ma.asarray(check.variables[name][:]).filled(np.nan)).all():
                    raise ValueError(f"filtered file contains non-finite {name!r} values")
            if check.variables["topo"].shape != check.variables["landmask"].shape:
                raise ValueError("filtered topo and landmask shapes differ")

        changed_variables = {"topo", terrain_name}
        unchanged_variables = verify_unchanged_variables(source, temporary_path, changed_variables)
        os.replace(temporary_path, output)
        temporary_path = None
        output_digest = sha256(output)
        ready_tmp = Path(str(ready) + ".tmp")
        ready_tmp.write_text(f"sha256 {output_digest}  {output.name}\n", encoding="utf-8")
        os.replace(ready_tmp, ready)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    return {
        "source": str(source),
        "source_sha256": source_digest,
        "output": str(output),
        "output_sha256": output_digest,
        "ready": str(ready),
        "filter_method": FILTER_METHOD,
        "passes": passes,
        "order": order,
        "strength": strength,
        "water_policy": water_policy,
        "before": asdict(before),
        "after": asdict(after),
        "point_change": point_change,
        "block_mean_change": scale_changes,
        "nominal_1d_amplitude_response": nominal_response,
        "unchanged_variables_verified": unchanged_variables,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Unfiltered HICAR static NetCDF file.")
    parser.add_argument("--output", required=True, type=Path, help="New filtered sensitivity file.")
    parser.add_argument("--passes", required=True, type=int, help="Number of high-order Shapiro passes (at least one).")
    parser.add_argument(
        "--order",
        type=int,
        default=8,
        help="Even Shapiro order. 8 is narrowly grid-scale selective; 4 is a broader sensitivity.",
    )
    parser.add_argument("--strength", type=float, default=1.0, help="Relaxation per pass in (0,1].")
    parser.add_argument(
        "--water-policy",
        choices=("preserve", "sea-level"),
        default="preserve",
        help="Preserve source water elevations (safe for lakes) or set all water cells to --sea-level-m.",
    )
    parser.add_argument("--sea-level-m", type=float, default=0.0)
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing output sensitivity file.")
    parser.add_argument("--report", type=Path, help="Optional JSON manifest; defaults to stdout only.")
    args = parser.parse_args()

    report = filter_static_file(
        args.input,
        args.output,
        passes=args.passes,
        order=args.order,
        strength=args.strength,
        water_policy=args.water_policy,
        sea_level_m=args.sea_level_m,
        overwrite=args.overwrite,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        report_tmp = Path(str(args.report) + ".tmp")
        report_tmp.write_text(rendered + "\n", encoding="utf-8")
        os.replace(report_tmp, args.report)
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
