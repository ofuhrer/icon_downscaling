#!/usr/bin/env python3
"""Validate terrain-radiation geometry and atomically add it to a HICAR static file."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

import netCDF4
import numpy as np


AZIMUTH_DEGREES = np.arange(0.0, 360.0, 4.0, dtype=np.float64)
REQUIRED_PROVENANCE = (
    "generator",
    "generator_version",
    "source_dem_sha256",
    "vertical_datum",
    "horizon_convention",
    "search_distance_km",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_published(path: Path, label: str) -> None:
    if not path.is_file():
        raise ValueError(f"{label} is missing: {path}")
    if not Path(f"{path}.ready").is_file():
        raise ValueError(f"{label} lacks publication marker: {path}.ready")


def finite_range(var: netCDF4.Variable, lower: float, upper: float, label: str) -> dict[str, float]:
    minimum = np.inf
    maximum = -np.inf
    if var.ndim == 3:
        slabs = (np.ma.asarray(var[index]).filled(np.nan) for index in range(var.shape[0]))
    else:
        slabs = (np.ma.asarray(var[:]).filled(np.nan),)
    for slab in slabs:
        values = np.asarray(slab, dtype=np.float64)
        if not np.all(np.isfinite(values)):
            raise ValueError(f"{label} contains non-finite values")
        minimum = min(minimum, float(values.min()))
        maximum = max(maximum, float(values.max()))
    if minimum < lower or maximum > upper:
        raise ValueError(f"{label} range [{minimum}, {maximum}] is outside [{lower}, {upper}]")
    return {"minimum": minimum, "maximum": maximum}


def validate_geometry(base_path: Path, geometry_path: Path) -> dict:
    with netCDF4.Dataset(base_path) as base, netCDF4.Dataset(geometry_path) as geometry:
        if "topo" not in base.variables or base.variables["topo"].ndim != 2:
            raise ValueError("base static file requires two-dimensional topo")
        if base.variables["topo"].dimensions != ("y", "x"):
            raise ValueError("base topo dimensions must be exactly (y, x)")
        shape = base.variables["topo"].shape
        missing = sorted({"hlm", "svf", "azimuth"} - set(geometry.variables))
        if missing:
            raise ValueError("geometry file lacks variables: " + ", ".join(missing))
        hlm = geometry.variables["hlm"]
        svf = geometry.variables["svf"]
        azimuth = np.asarray(geometry.variables["azimuth"][:], dtype=np.float64)
        if hlm.shape != (90, *shape):
            raise ValueError(f"hlm must have shape (90, {shape[0]}, {shape[1]}), got {hlm.shape}")
        if hlm.dimensions != ("azimuth", "y", "x") or svf.dimensions != ("y", "x"):
            raise ValueError("geometry dimensions must be hlm(azimuth,y,x) and svf(y,x)")
        if svf.shape != shape:
            raise ValueError(f"svf must have shape {shape}, got {svf.shape}")
        if azimuth.shape != (90,) or not np.allclose(azimuth, AZIMUTH_DEGREES, atol=1.0e-5):
            raise ValueError("azimuth must be the HICAR sector convention 0, 4, ..., 356 degrees")
        provenance = {name: getattr(geometry, name, None) for name in REQUIRED_PROVENANCE}
        missing_provenance = sorted(name for name, value in provenance.items() if value in (None, ""))
        if missing_provenance:
            raise ValueError("geometry lacks provenance attributes: " + ", ".join(missing_provenance))
        if not re.fullmatch(r"[0-9a-fA-F]{64}", str(provenance["source_dem_sha256"])):
            raise ValueError("source_dem_sha256 must contain exactly 64 hexadecimal digits")
        if str(provenance["horizon_convention"]) != "hlm_zenith_angle_degrees_flat_90":
            raise ValueError("unsupported horizon_convention")
        try:
            search_distance = float(provenance["search_distance_km"])
        except (TypeError, ValueError) as exc:
            raise ValueError("search_distance_km must be numeric") from exc
        if search_distance <= 0.0:
            raise ValueError("search_distance_km must be positive")
        ranges = {
            "hlm": finite_range(hlm, 0.0, 90.0, "hlm"),
            "svf": finite_range(svf, 0.0, 1.0, "svf"),
        }
        payload = {
            "shape_yx": list(shape),
            "azimuth_degrees": AZIMUTH_DEGREES.tolist(),
            "provenance": provenance,
            "ranges": ranges,
        }
    return payload


def validate_base_angles(path: Path) -> dict[str, dict[str, float]]:
    with netCDF4.Dataset(path) as base:
        missing = sorted({"slope_angle", "aspect_angle"} - set(base.variables))
        if missing:
            raise ValueError("base static file lacks terrain angles: " + ", ".join(missing))
        return {
            "slope_angle": finite_range(base.variables["slope_angle"], 0.0, np.pi / 2.0, "slope_angle"),
            "aspect_angle": finite_range(base.variables["aspect_angle"], 0.0, 2.0 * np.pi, "aspect_angle"),
        }


def publish(base: Path, geometry: Path, output: Path, manifest: Path) -> dict:
    require_published(base, "base static file")
    require_published(geometry, "terrain-radiation geometry")
    if output.exists() or Path(f"{output}.ready").exists():
        raise ValueError(f"refusing to overwrite published candidate: {output}")
    angle_ranges = validate_base_angles(base)
    geometry_report = validate_geometry(base, geometry)
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent, delete=False) as stream:
        temporary = Path(stream.name)
    try:
        shutil.copyfile(base, temporary)
        with netCDF4.Dataset(temporary, "a") as candidate, netCDF4.Dataset(geometry) as source:
            if "azimuth" in candidate.dimensions or any(name in candidate.variables for name in ("azimuth", "hlm", "svf")):
                raise ValueError("base static already contains terrain-radiation geometry")
            candidate.createDimension("azimuth", 90)
            azimuth = candidate.createVariable("azimuth", "f4", ("azimuth",))
            azimuth[:] = AZIMUTH_DEGREES
            azimuth.units = "degrees_clockwise_from_north"
            hlm = candidate.createVariable("hlm", "f4", ("azimuth", "y", "x"), zlib=True, complevel=2, shuffle=True)
            for index in range(90):
                hlm[index, :, :] = source.variables["hlm"][index, :, :]
            hlm.units = "degrees"
            hlm.long_name = "zenith angle to topographic horizon; flat unobstructed horizon is 90 degrees"
            svf = candidate.createVariable("svf", "f4", ("y", "x"), zlib=True, complevel=2, shuffle=True)
            svf[:] = source.variables["svf"][:]
            svf.units = "1"
            svf.long_name = "sky view factor"
            candidate.terrain_radiation_geometry_sha256 = sha256(geometry)
            candidate.terrain_radiation_horizon_convention = geometry_report["provenance"]["horizon_convention"]
            candidate.terrain_radiation_search_distance_km = float(
                geometry_report["provenance"]["search_distance_km"]
            )
        output_digest = sha256(temporary)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    report = {
        "schema": "icon-hicar-terrain-radiation-static-v1",
        "base_static": {"path": str(base.resolve()), "sha256": sha256(base)},
        "geometry": {"path": str(geometry.resolve()), "sha256": sha256(geometry), **geometry_report},
        "base_angle_ranges": angle_ranges,
        "output": {"path": str(output.resolve()), "sha256": output_digest},
    }
    manifest_tmp = manifest.with_name(f".{manifest.name}.tmp")
    manifest_tmp.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(manifest_tmp, manifest)
    ready_tmp = Path(f"{output}.ready.tmp")
    ready_tmp.write_text(f"sha256 {output_digest}  {output.name}\n", encoding="utf-8")
    os.replace(ready_tmp, Path(f"{output}.ready"))
    manifest_digest = sha256(manifest)
    manifest_ready_tmp = Path(f"{manifest}.ready.tmp")
    manifest_ready_tmp.write_text(
        f"sha256 {manifest_digest}  {manifest.name}\n", encoding="utf-8"
    )
    os.replace(manifest_ready_tmp, Path(f"{manifest}.ready"))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-static", required=True, type=Path)
    parser.add_argument("--geometry", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = publish(args.base_static, args.geometry, args.output, args.manifest)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(report["output"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
