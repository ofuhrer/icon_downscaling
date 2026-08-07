#!/usr/bin/env python3
"""Publish flat/open and single-sector blocked terrain-radiation gate fixtures."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile

import netCDF4
import numpy as np
from pyproj import CRS, Transformer


HERE = Path(__file__).resolve().parent
PUBLISHER_PATH = HERE / "publish_terrain_radiation_static.py"
AZIMUTH_DEGREES = np.arange(0.0, 360.0, 4.0, dtype=np.float32)


def load_publisher():
    spec = importlib.util.spec_from_file_location("terrain_radiation_publisher", PUBLISHER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def ready(path: Path) -> None:
    digest = sha256(path)
    marker_tmp = Path(f"{path}.ready.tmp")
    marker_tmp.write_text(f"sha256 {digest}  {path.name}\n", encoding="utf-8")
    os.replace(marker_tmp, Path(f"{path}.ready"))


def atomic_json(path: Path, payload: dict) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        ready(path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def create_base(path: Path, size: int, dx_m: float, center_lat: float, center_lon: float) -> None:
    if size < 5 or size % 2 == 0:
        raise ValueError("--size must be an odd integer of at least five")
    x = (np.arange(size, dtype=np.float64) - size // 2) * dx_m
    y = (np.arange(size, dtype=np.float64) - size // 2) * dx_m
    xx, yy = np.meshgrid(x, y)
    crs = CRS.from_proj4(
        f"+proj=aeqd +lat_0={center_lat} +lon_0={center_lon} "
        "+x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs"
    )
    lon, lat = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform(xx, yy)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(descriptor)
    temporary_path = Path(temporary)
    try:
        with netCDF4.Dataset(temporary_path, "w", format="NETCDF4") as dataset:
            dataset.createDimension("x", size)
            dataset.createDimension("y", size)
            dataset.createDimension("soil_layer", 4)
            dataset.createDimension("soil_bound", 2)
            dataset.createVariable("x", "f4", ("x",))[:] = x
            dataset.createVariable("y", "f4", ("y",))[:] = y
            mapping = dataset.createVariable("azimuthal_equidistant", "i4")
            mapping.grid_mapping_name = "azimuthal_equidistant"
            mapping.latitude_of_projection_origin = center_lat
            mapping.longitude_of_projection_origin = center_lon
            mapping.crs_wkt = crs.to_wkt()
            fields = {
                "lat": (lat, "f4"),
                "lon": (lon, "f4"),
                "topo": (np.zeros((size, size)), "f4"),
                "slope_angle": (np.zeros((size, size)), "f4"),
                "aspect_angle": (np.zeros((size, size)), "f4"),
                "landmask": (np.ones((size, size)), "i2"),
                "landuse": (np.full((size, size), 7), "i2"),
                "soil_type": (np.full((size, size), 6), "i2"),
                "surface_temperature": (np.full((size, size), 280.0), "f4"),
                "soil_deep_temperature": (np.full((size, size), 280.0), "f4"),
            }
            for name, (values, dtype) in fields.items():
                variable = dataset.createVariable(name, dtype, ("y", "x"), zlib=True)
                variable[:, :] = values
                variable.coordinates = "lon lat"
                variable.grid_mapping = "azimuthal_equidistant"
            dataset.createVariable("soil_layer", "i4", ("soil_layer",))[:] = np.arange(1, 5)
            dataset.createVariable(
                "soil_layer_bounds_cm", "f4", ("soil_layer", "soil_bound")
            )[:, :] = ((0, 10), (10, 30), (30, 70), (70, 150))
            dataset.createVariable(
                "soil_temperature", "f4", ("soil_layer", "y", "x"), zlib=True
            )[:, :, :] = 280.0
            dataset.createVariable(
                "soil_vwc", "f4", ("soil_layer", "y", "x"), zlib=True
            )[:, :, :] = 0.28
            dataset.createVariable(
                "soil_type_layer", "i2", ("soil_layer", "y", "x"), zlib=True
            )[:, :, :] = 6
            dataset.Conventions = "CF-1.8"
            dataset.title = "Synthetic flat HICAR terrain-radiation qualification domain"
            dataset.hicar_dx_m = dx_m
            dataset.hicar_static_quality = "synthetic_analytic_gate_v1"
            dataset.synthetic_terrain = "flat zero-elevation horizontal plane"
        os.replace(temporary_path, path)
        ready(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def create_geometry(
    path: Path,
    shape: tuple[int, int],
    source_dem_sha256: str,
    blocked_index: int | None,
    horizon_elevation_deg: float,
) -> float:
    hlm = np.full((90, *shape), 90.0, dtype=np.float32)
    if blocked_index is None:
        svf_value = 1.0
        label = "flat_open"
    else:
        hlm[blocked_index, :, :] = 90.0 - horizon_elevation_deg
        # Horizontal-surface isotropic SVF, with one of 90 equal azimuth
        # sectors obstructed to a uniform horizon elevation.
        svf_value = 1.0 - np.sin(np.deg2rad(horizon_elevation_deg)) ** 2 / 90.0
        label = "single_sector_blocked"
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(descriptor)
    temporary_path = Path(temporary)
    try:
        with netCDF4.Dataset(temporary_path, "w", format="NETCDF4") as dataset:
            dataset.createDimension("azimuth", 90)
            dataset.createDimension("y", shape[0])
            dataset.createDimension("x", shape[1])
            azimuth = dataset.createVariable("azimuth", "f4", ("azimuth",))
            azimuth[:] = AZIMUTH_DEGREES
            azimuth.units = "degrees_clockwise_from_north"
            horizon = dataset.createVariable(
                "hlm", "f4", ("azimuth", "y", "x"), zlib=True, complevel=2, shuffle=True
            )
            horizon[:, :, :] = hlm
            horizon.units = "degrees"
            sky = dataset.createVariable("svf", "f4", ("y", "x"), zlib=True)
            sky[:, :] = svf_value
            sky.units = "1"
            dataset.generator = "prepare_synthetic_terrain_radiation_gate.py"
            dataset.generator_version = "1"
            dataset.source_dem_sha256 = source_dem_sha256
            dataset.vertical_datum = "synthetic_zero_reference"
            dataset.horizon_convention = "hlm_zenith_angle_degrees_flat_90"
            dataset.search_distance_km = 1.0
            dataset.synthetic_geometry = label
            dataset.synthetic_svf_formula = "1 - sector_fraction * sin(horizon_elevation)^2"
        os.replace(temporary_path, path)
        ready(path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return float(svf_value)


def git_identity(root: Path) -> dict[str, str]:
    try:
        commit = subprocess.run(
            ("git", "-C", str(root), "rev-parse", "HEAD"),
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "unavailable"
    source = root / "src" / "utilities" / "atm_utilities.F90"
    driver = root / "src" / "physics" / "ra_driver.F90"
    return {
        "commit": commit,
        "atm_utilities_sha256": sha256(source) if source.is_file() else "unavailable",
        "ra_driver_sha256": sha256(driver) if driver.is_file() else "unavailable",
    }


def prepare(
    output_dir: Path,
    size: int,
    dx_m: float,
    blocked_azimuth_deg: float,
    horizon_elevation_deg: float,
    hicar_root: Path,
) -> dict:
    if dx_m <= 0:
        raise ValueError("--dx-m must be positive")
    if blocked_azimuth_deg not in AZIMUTH_DEGREES:
        raise ValueError("--blocked-azimuth-deg must be one of 0, 4, ..., 356")
    if not 0.0 < horizon_elevation_deg < 90.0:
        raise ValueError("--horizon-elevation-deg must be between zero and 90")
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "base": output_dir / "base_flat.nc",
        "flat_geometry": output_dir / "geometry_flat_open.nc",
        "blocked_geometry": output_dir / "geometry_single_sector_blocked.nc",
        "flat_static": output_dir / "static_flat_open.nc",
        "blocked_static": output_dir / "static_single_sector_blocked.nc",
        "flat_manifest": output_dir / "static_flat_open.manifest.json",
        "blocked_manifest": output_dir / "static_single_sector_blocked.manifest.json",
        "contract": output_dir / "experiment_contract.json",
    }
    occupied = [path for path in paths.values() if path.exists() or Path(f"{path}.ready").exists()]
    if occupied:
        raise ValueError("refusing to overwrite synthetic gate artifacts: " + ", ".join(map(str, occupied)))

    create_base(paths["base"], size, dx_m, 46.815, 8.225)
    shape = (size, size)
    synthetic_dem_sha = hashlib.sha256(np.zeros(shape, dtype=np.float32).tobytes()).hexdigest()
    create_geometry(paths["flat_geometry"], shape, synthetic_dem_sha, None, horizon_elevation_deg)
    blocked_index = int(np.where(AZIMUTH_DEGREES == blocked_azimuth_deg)[0][0])
    blocked_svf = create_geometry(
        paths["blocked_geometry"], shape, synthetic_dem_sha, blocked_index, horizon_elevation_deg
    )
    publisher = load_publisher()
    publisher.publish(
        paths["base"], paths["flat_geometry"], paths["flat_static"], paths["flat_manifest"]
    )
    publisher.publish(
        paths["base"], paths["blocked_geometry"], paths["blocked_static"], paths["blocked_manifest"]
    )
    artifacts = {
        name: {"path": str(path.resolve()), "sha256": sha256(path)}
        for name, path in paths.items()
        if name != "contract"
    }
    threshold = horizon_elevation_deg
    contract = {
        "schema": "hicar-terrain-radiation-synthetic-gate/v2",
        "scope": "analytic_geometry_and_experiment_design_no_model_run",
        "hicar_source": git_identity(hicar_root),
        "artifacts": artifacts,
        "geometry": {
            "azimuth_convention": "degrees clockwise from north; sectors start at 0 degrees",
            "blocked_azimuth_degrees": blocked_azimuth_deg,
            "blocked_zero_based_sector": blocked_index,
            "blocked_fortran_sector": blocked_index + 1,
            "horizon_elevation_degrees": horizon_elevation_deg,
            "hlm_zenith_angle_degrees": 90.0 - horizon_elevation_deg,
            "flat_open_svf": 1.0,
            "blocked_svf": blocked_svf,
        },
        "analytic_expectations": {
            "hicar_visibility_rule": "solar_elevation_radians >= (90 - hlm_degrees) * pi/180",
            "at_blocked_azimuth": [
                {"solar_elevation_degrees": threshold - 1.0, "visible": False, "direct_flux_ratio": 0.0},
                {"solar_elevation_degrees": threshold, "visible": True},
                {"solar_elevation_degrees": threshold + 1.0, "visible": True},
            ],
            "flat_open_diffuse_flux_ratio": 1.0,
            "blocked_diffuse_flux_ratio": blocked_svf,
            "raw_horizontal_direct_and_diffuse_are_invariant_across_profiles": True,
        },
        "model_experiment_matrix": [
            {"case": "flat_off", "static": "flat_static", "terrain_radiation_profile": "off"},
            {"case": "flat_direct", "static": "flat_static", "terrain_radiation_profile": "direct"},
            {"case": "flat_direct_diffuse", "static": "flat_static", "terrain_radiation_profile": "direct-diffuse"},
            {"case": "blocked_direct", "static": "blocked_static", "terrain_radiation_profile": "direct"},
            {"case": "blocked_direct_diffuse", "static": "blocked_static", "terrain_radiation_profile": "direct-diffuse"},
        ],
        "gates": {
            "flat_identity": "within each enabled flat run, corrected direct and diffuse fluxes must match that run's raw horizontal RRTMGP components within configured numerical tolerance",
            "flat_off_cross_run": "flat_off versus enabled-run drift is retained as a coupled-trajectory diagnostic and is not a component-identity gate",
            "blocked_threshold": "direct shortwave must be zero below and nonzero at/above the analytic horizon threshold when raw direct is positive",
            "causal_components": "raw horizontal direct/diffuse must be identical; only corrected components may differ",
            "restart": "continuous and split trajectories must agree for every saved field at common times",
        },
        "restart_design": {
            "continuous_duration_minutes": 180,
            "split_at_minutes": 90,
            "comparison_times_minutes": [90, 100, 110, 120, 130, 140, 150, 160, 170, 180],
            "comparison": "bitwise where deterministic, otherwise the project restart tolerance",
        },
        "remaining_inputs_before_model_execution": [
            "forcing on this exact synthetic grid with a sun path crossing the blocked sector",
            "rendered profile namelists bound to the chosen HICAR executable",
            "checksum-bound continuous and split-run plans",
        ],
        "promotion_limit": "These fixtures qualify geometry expectations only; they do not qualify national terrain radiation.",
    }
    atomic_json(paths["contract"], contract)
    return contract


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--size", type=int, default=21)
    parser.add_argument("--dx-m", type=float, default=200.0)
    parser.add_argument("--blocked-azimuth-deg", type=float, default=88.0)
    parser.add_argument("--horizon-elevation-deg", type=float, default=30.0)
    parser.add_argument("--hicar-root", type=Path, default=HERE.parents[2] / "HICAR")
    args = parser.parse_args()
    try:
        contract = prepare(
            args.output_dir, args.size, args.dx_m, args.blocked_azimuth_deg,
            args.horizon_elevation_deg, args.hicar_root,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(contract["artifacts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
