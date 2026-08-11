#!/usr/bin/env python3
"""Build audited HORAYZON geometry for the Swiss 200 m HICAR domain.

The target terrain is copied exactly from an existing HICAR static file.  A
driving-model terrain source supplies the outer horizon band, so horizon rays
never encounter an artificial file edge or a discontinuous high-resolution
DEM outside HICAR's relaxed lateral boundary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile

import netCDF4
import numpy as np


AZIMUTH_DEGREES = np.arange(0.0, 360.0, 4.0, dtype=np.float64)
EGM2008_GRID_SHA256 = "4191d471eefebf24091b56dbc604353cb3b8cf8cc70e448bb9ae56a272bef17a"
EXPECTED_TARGET_SHAPE = (1431, 2061)
EXPECTED_DX_M = 200.0


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def publish_ready(path: Path) -> str:
    digest = sha256(path)
    ready = Path(f"{path}.ready")
    temporary = ready.with_name(f".{ready.name}.tmp")
    temporary.write_text(f"sha256 {digest}  {path.name}\n", encoding="utf-8")
    os.replace(temporary, ready)
    return digest


def spacing(values: np.ndarray, name: str) -> float:
    delta = np.diff(np.asarray(values, dtype=np.float64))
    if delta.size == 0 or not np.all(np.isfinite(delta)) or np.any(delta <= 0.0):
        raise ValueError(f"{name} must be finite and strictly increasing")
    representative = float(np.median(delta))
    if not np.allclose(delta, representative, atol=1.0e-4, rtol=0.0):
        raise ValueError(f"{name} must be regularly spaced")
    return representative


def extension_cells(search_distance_km: float, dx_m: float) -> int:
    if search_distance_km <= 0.0:
        raise ValueError("horizon search distance must be positive")
    # One additional cell ensures that the last ray can intersect a complete
    # terrain-mesh cell at the requested distance.
    return int(math.ceil(search_distance_km * 1000.0 / dx_m)) + 1


def extended_axis(values: np.ndarray, cells: int, dx_m: float) -> np.ndarray:
    start = float(values[0]) - cells * dx_m
    return start + np.arange(values.size + 2 * cells, dtype=np.float64) * dx_m


def boundary_values(values: np.ndarray) -> np.ndarray:
    return np.concatenate((values[0], values[-1], values[1:-1, 0], values[1:-1, -1]))


def require_target_static(path: Path) -> dict:
    with netCDF4.Dataset(path) as dataset:
        missing = sorted({"x", "y", "lat", "lon", "topo"} - set(dataset.variables))
        if missing:
            raise ValueError("base static lacks variables: " + ", ".join(missing))
        x = np.asarray(dataset.variables["x"][:], dtype=np.float64)
        y = np.asarray(dataset.variables["y"][:], dtype=np.float64)
        topo = np.ma.asarray(dataset.variables["topo"][:]).filled(np.nan).astype(np.float32)
        if topo.shape != (y.size, x.size):
            raise ValueError("base topo dimensions do not match y,x")
        if topo.shape != EXPECTED_TARGET_SHAPE:
            raise ValueError(
                f"selected Swiss target must be {EXPECTED_TARGET_SHAPE}, got {topo.shape}"
            )
        dx = spacing(x, "x")
        dy = spacing(y, "y")
        if not np.isclose(dx, EXPECTED_DX_M) or not np.isclose(dy, EXPECTED_DX_M):
            raise ValueError(f"selected Swiss target must be 200 m, got dx={dx}, dy={dy}")
        if not np.all(np.isfinite(topo)):
            raise ValueError("base topo contains non-finite values")
        projection = getattr(dataset, "hicar_projection", "")
        if not projection and "azimuthal_equidistant" in dataset.variables:
            projection = getattr(dataset.variables["azimuthal_equidistant"], "crs_wkt", "")
        if not projection:
            raise ValueError("base static lacks its projected CRS")
    return {"x": x, "y": y, "topo": topo, "projection": projection, "dx_m": dx}


def interpolate_driving_topography(
    source: Path,
    latitude: np.ndarray,
    longitude: np.ndarray,
    variable: str,
    latitude_variable: str,
    longitude_variable: str,
) -> np.ndarray:
    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root / "scripts"))
    from prepare_static_inputs import interp_to_domain  # noqa: PLC0415

    return interp_to_domain(
        source,
        variable,
        latitude_variable,
        longitude_variable,
        latitude,
        longitude,
        "linear",
    ).astype(np.float32)


def prepare_extended_dem(
    base_static: Path,
    driving_topography: Path,
    output: Path,
    search_distance_km: float,
    driving_variable: str,
    driving_latitude_variable: str,
    driving_longitude_variable: str,
    max_edge_mismatch_m: float,
) -> dict:
    from pyproj import CRS, Transformer  # noqa: PLC0415

    target = require_target_static(base_static)
    border = extension_cells(search_distance_km, target["dx_m"])
    x_ext = extended_axis(target["x"], border, target["dx_m"])
    y_ext = extended_axis(target["y"], border, target["dx_m"])
    xx, yy = np.meshgrid(x_ext, y_ext)
    transformer = Transformer.from_crs(CRS.from_user_input(target["projection"]), "EPSG:4326", always_xy=True)
    longitude, latitude = transformer.transform(xx, yy)
    del xx, yy
    driving = interpolate_driving_topography(
        driving_topography,
        latitude,
        longitude,
        driving_variable,
        driving_latitude_variable,
        driving_longitude_variable,
    )
    if not np.all(np.isfinite(driving)):
        raise ValueError("driving topography does not cover the extended horizon domain")

    ys = slice(border, border + target["y"].size)
    xs = slice(border, border + target["x"].size)
    edge_difference = np.abs(boundary_values(driving[ys, xs] - target["topo"]))
    edge_max = float(np.max(edge_difference))
    edge_p99 = float(np.percentile(edge_difference, 99.0))
    if edge_max > max_edge_mismatch_m:
        raise ValueError(
            f"driving terrain disagrees with base-static edge by {edge_max:.3f} m; "
            f"limit is {max_edge_mismatch_m:.3f} m"
        )
    extended_topo = driving
    extended_topo[ys, xs] = target["topo"]

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() or Path(f"{output}.ready").exists():
        raise ValueError(f"refusing to overwrite extended DEM: {output}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with netCDF4.Dataset(temporary, "w", format="NETCDF4") as dataset:
            dataset.createDimension("x", x_ext.size)
            dataset.createDimension("y", y_ext.size)
            for name, values, dimensions, units in (
                ("x", x_ext, ("x",), "m"),
                ("y", y_ext, ("y",), "m"),
                ("lat", latitude, ("y", "x"), "degrees_north"),
                ("lon", longitude, ("y", "x"), "degrees_east"),
                ("topo", extended_topo, ("y", "x"), "m"),
            ):
                variable_out = dataset.createVariable(
                    name,
                    "f4",
                    dimensions,
                    zlib=len(dimensions) == 2,
                    complevel=2,
                    shuffle=len(dimensions) == 2,
                )
                variable_out[:] = values
                variable_out.units = units
            dataset.Conventions = "CF-1.8"
            dataset.generator = "prepare_terrain_radiation_geometry.py"
            dataset.target_static_sha256 = sha256(base_static)
            dataset.driving_topography_sha256 = sha256(driving_topography)
            dataset.source_vertical_datum = (
                "target interior: Copernicus DEM EGM2008 orthometric blended to REA-L-CH1 "
                "HSURF; outer band: REA-L-CH1 HSURF treated as mean-sea-level orthometric height"
            )
            dataset.target_y_start = border
            dataset.target_x_start = border
            dataset.target_ny = target["y"].size
            dataset.target_nx = target["x"].size
            dataset.search_distance_km = search_distance_km
            dataset.actual_extension_km = border * target["dx_m"] / 1000.0
            dataset.edge_mismatch_max_m = edge_max
            dataset.edge_mismatch_p99_m = edge_p99
            dataset.hicar_projection = target["projection"]
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    digest = publish_ready(output)
    return {
        "path": str(output.resolve()),
        "sha256": digest,
        "shape_yx": [y_ext.size, x_ext.size],
        "target_slice": {"y_start": border, "x_start": border},
        "actual_extension_km": border * target["dx_m"] / 1000.0,
        "edge_mismatch_max_m": edge_max,
        "edge_mismatch_p99_m": edge_p99,
    }


def egm2008_to_ellipsoidal(
    longitude: np.ndarray,
    latitude: np.ndarray,
    orthometric_height: np.ndarray,
    grid: Path,
) -> np.ndarray:
    from pyproj import CRS, datadir  # noqa: PLC0415
    from pyproj.transformer import TransformerGroup  # noqa: PLC0415

    grid_digest = sha256(grid)
    if grid_digest != EGM2008_GRID_SHA256:
        raise ValueError(
            f"unexpected EGM2008 grid checksum {grid_digest}; expected {EGM2008_GRID_SHA256}"
        )
    datadir.append_data_dir(str(grid.parent))
    group = TransformerGroup(CRS.from_epsg(9518), CRS.from_epsg(4979), always_xy=True)
    candidates = [
        transformer
        for transformer in group.transformers
        if "EGM2008 height (1)" in transformer.description
        and transformer.accuracy >= 0.0
        and transformer.accuracy <= 0.2
    ]
    if not group.best_available or not candidates:
        raise ValueError("the official EGM2008 grid transformation is not available to pyproj")
    transformer = candidates[0]
    result = np.empty(orthometric_height.shape, dtype=np.float32)
    for start in range(0, orthometric_height.shape[0], 128):
        stop = min(start + 128, orthometric_height.shape[0])
        _, _, converted = transformer.transform(
            longitude[start:stop], latitude[start:stop], orthometric_height[start:stop]
        )
        result[start:stop] = converted
    if not np.all(np.isfinite(result)):
        raise ValueError("EGM2008 conversion produced non-finite ellipsoidal heights")
    return result


def compute_geometry(extended_dem: Path, output: Path, egm2008_grid: Path) -> dict:
    try:
        import horayzon as hray  # noqa: PLC0415
    except ImportError as exc:
        raise ValueError("HORAYZON is not installed in the selected Python environment") from exc

    with netCDF4.Dataset(extended_dem) as dataset:
        x = np.asarray(dataset.variables["x"][:], dtype=np.float64)
        y = np.asarray(dataset.variables["y"][:], dtype=np.float64)
        latitude = np.ma.asarray(dataset.variables["lat"][:]).filled(np.nan).astype(np.float64)
        longitude = np.ma.asarray(dataset.variables["lon"][:]).filled(np.nan).astype(np.float64)
        elevation = np.ma.asarray(dataset.variables["topo"][:]).filled(np.nan).astype(np.float64)
        y_start = int(dataset.target_y_start)
        x_start = int(dataset.target_x_start)
        target_ny = int(dataset.target_ny)
        target_nx = int(dataset.target_nx)
        search_distance_km = float(dataset.search_distance_km)
    if elevation.shape != latitude.shape or elevation.shape != longitude.shape:
        raise ValueError("extended DEM topo/lat/lon shapes differ")
    dx = spacing(x, "extended x")
    dy = spacing(y, "extended y")
    if not np.isclose(dx, EXPECTED_DX_M) or not np.isclose(dy, EXPECTED_DX_M):
        raise ValueError("extended DEM is not on the selected 200 m grid")

    # HORAYZON's curved-grid examples use north-to-south row order.  The HICAR
    # static is south-to-north, so compute flipped and restore HICAR order on
    # output.  Symmetric extension means the target slice is unchanged.
    latitude = latitude[::-1].copy()
    longitude = longitude[::-1].copy()
    elevation = elevation[::-1].copy()
    ellipsoidal = egm2008_to_ellipsoidal(longitude, latitude, elevation, egm2008_grid)
    x_ecef, y_ecef, z_ecef = hray.transform.lonlat2ecef(
        longitude, latitude, ellipsoidal, ellps="WGS84"
    )
    transformer = hray.transform.TransformerEcef2enu(
        lon_or=float(longitude[longitude.shape[0] // 2, longitude.shape[1] // 2]),
        lat_or=float(latitude[latitude.shape[0] // 2, latitude.shape[1] // 2]),
        ellps="WGS84",
    )
    x_enu, y_enu, z_enu = hray.transform.ecef2enu(x_ecef, y_ecef, z_ecef, transformer)
    target_slice = (
        slice(y_start, y_start + target_ny),
        slice(x_start, x_start + target_nx),
    )
    target_lon = longitude[target_slice]
    target_lat = latitude[target_slice]
    vec_norm_ecef = hray.direction.surf_norm(target_lon, target_lat)
    vec_north_ecef = hray.direction.north_dir(
        x_ecef[target_slice], y_ecef[target_slice], z_ecef[target_slice], vec_norm_ecef, ellps="WGS84"
    )
    vec_norm_enu = hray.transform.ecef2enu_vector(vec_norm_ecef, transformer)
    vec_north_enu = hray.transform.ecef2enu_vector(vec_north_ecef, transformer)
    del vec_norm_ecef, vec_north_ecef

    vertices = hray.auxiliary.rearrange_pad_buffer(x_enu, y_enu, z_enu)
    horizon, azimuth = hray.horizon.horizon_gridded(
        vertices,
        elevation.shape[0],
        elevation.shape[1],
        vec_norm_enu,
        vec_north_enu,
        y_start,
        x_start,
        dist_search=search_distance_km,
        azim_num=90,
    )
    azimuth_degrees = np.mod(np.rad2deg(azimuth), 360.0)
    if azimuth_degrees.shape != (90,) or not np.allclose(
        azimuth_degrees, AZIMUTH_DEGREES, atol=1.0e-5, rtol=0.0
    ):
        raise ValueError(f"HORAYZON returned unexpected azimuths: {azimuth_degrees}")

    slope_slice = (
        slice(y_start - 1, y_start + target_ny + 1),
        slice(x_start - 1, x_start + target_nx + 1),
    )
    slope_norm_ecef = hray.direction.surf_norm(
        longitude[slope_slice], latitude[slope_slice]
    )
    slope_north_ecef = hray.direction.north_dir(
        x_ecef[slope_slice],
        y_ecef[slope_slice],
        z_ecef[slope_slice],
        slope_norm_ecef,
        ellps="WGS84",
    )
    slope_norm_enu = hray.transform.ecef2enu_vector(slope_norm_ecef, transformer)
    slope_north_enu = hray.transform.ecef2enu_vector(slope_north_ecef, transformer)
    rotation = hray.transform.rotation_matrix_glob2loc(slope_north_enu, slope_norm_enu)
    del (
        x_ecef,
        y_ecef,
        z_ecef,
        vec_north_enu,
        vec_norm_enu,
        slope_norm_ecef,
        slope_north_ecef,
        slope_norm_enu,
        slope_north_enu,
    )
    vec_tilt = hray.topo_param.slope_plane_meth(
        x_enu[slope_slice],
        y_enu[slope_slice],
        z_enu[slope_slice],
        rot_mat=rotation,
        output_rot=True,
    )[1:-1, 1:-1]
    del rotation, x_enu, y_enu, z_enu, vertices
    svf = hray.topo_param.sky_view_factor(azimuth, horizon, vec_tilt)
    slope = np.arccos(np.clip(vec_tilt[:, :, 2], -1.0, 1.0))
    aspect = np.pi / 2.0 - np.arctan2(vec_tilt[:, :, 1], vec_tilt[:, :, 0])
    aspect %= 2.0 * np.pi
    # HICAR expects zenith angle to the terrain horizon and treats a negative
    # terrain elevation as an unobstructed (90 degree) horizon.
    hlm = 90.0 - np.maximum(0.0, np.rad2deg(horizon))
    del horizon, vec_tilt

    # Restore HICAR's south-to-north y orientation.
    hlm = np.moveaxis(hlm[::-1], 2, 0).astype(np.float32)
    svf = svf[::-1].astype(np.float32)
    slope = slope[::-1].astype(np.float32)
    aspect = aspect[::-1].astype(np.float32)
    ranges = validate_arrays(hlm, svf, slope, aspect)

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() or Path(f"{output}.ready").exists():
        raise ValueError(f"refusing to overwrite geometry: {output}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with netCDF4.Dataset(temporary, "w", format="NETCDF4") as dataset:
            dataset.createDimension("azimuth", 90)
            dataset.createDimension("y", target_ny)
            dataset.createDimension("x", target_nx)
            azimuth_out = dataset.createVariable("azimuth", "f4", ("azimuth",))
            azimuth_out[:] = AZIMUTH_DEGREES
            azimuth_out.units = "degrees_clockwise_from_north"
            for name, values, dimensions, units, long_name in (
                ("hlm", hlm, ("azimuth", "y", "x"), "degrees", "zenith angle to topographic horizon; flat unobstructed horizon is 90 degrees"),
                ("svf", svf, ("y", "x"), "1", "sky view factor for isotropic radiation on the local slope"),
                ("slope_rad", slope, ("y", "x"), "radian", "terrain slope angle"),
                ("aspect_rad", aspect, ("y", "x"), "radian", "terrain slope aspect clockwise from north"),
            ):
                chunks = (1, min(128, target_ny), min(256, target_nx)) if len(dimensions) == 3 else (min(128, target_ny), min(256, target_nx))
                variable = dataset.createVariable(
                    name,
                    "f4",
                    dimensions,
                    zlib=True,
                    complevel=2,
                    shuffle=True,
                    chunksizes=chunks,
                )
                if len(dimensions) == 3:
                    for index in range(90):
                        variable[index] = values[index]
                else:
                    variable[:] = values
                variable.units = units
                variable.long_name = long_name
            dataset.Conventions = "CF-1.8"
            dataset.generator = "prepare_terrain_radiation_geometry.py"
            dataset.generator_version = "horayzon-curved-egm2008-v1"
            dataset.horayzon_version = getattr(hray, "__version__", "unknown")
            dataset.source_dem_sha256 = sha256(extended_dem)
            dataset.source_vertical_datum = "EGM2008 orthometric / REA-L mean-sea-level model terrain"
            dataset.ellipsoidal_conversion = "EPSG:9518 to EPSG:4979 using us_nga_egm08_25.tif"
            dataset.egm2008_grid_sha256 = EGM2008_GRID_SHA256
            dataset.horizon_convention = "hlm_zenith_angle_degrees_flat_90"
            dataset.search_distance_km = search_distance_km
            dataset.azimuth_sector_count = 90
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    digest = publish_ready(output)
    return {"path": str(output.resolve()), "sha256": digest, "ranges": ranges}


def validate_arrays(
    hlm: np.ndarray, svf: np.ndarray, slope: np.ndarray, aspect: np.ndarray
) -> dict[str, list[float]]:
    expected = (90, *EXPECTED_TARGET_SHAPE)
    if hlm.shape != expected:
        raise ValueError(f"hlm shape must be {expected}, got {hlm.shape}")
    for name, values, lower, upper in (
        ("hlm", hlm, 0.0, 90.0),
        ("svf", svf, 0.0, 1.0),
        ("slope_rad", slope, 0.0, np.pi / 2.0),
        ("aspect_rad", aspect, 0.0, 2.0 * np.pi),
    ):
        if not np.all(np.isfinite(values)):
            raise ValueError(f"{name} contains non-finite values")
        minimum = float(np.min(values))
        maximum = float(np.max(values))
        if minimum < lower - 1.0e-6 or maximum > upper + 1.0e-6:
            raise ValueError(f"{name} range [{minimum}, {maximum}] is invalid")
    return {
        "hlm": [float(np.min(hlm)), float(np.max(hlm))],
        "svf": [float(np.min(svf)), float(np.max(svf))],
        "slope_rad": [float(np.min(slope)), float(np.max(slope))],
        "aspect_rad": [float(np.min(aspect)), float(np.max(aspect))],
    }


def buffered_copy(source: Path, target: Path) -> None:
    with source.open("rb") as source_stream, target.open("wb") as target_stream:
        shutil.copyfileobj(source_stream, target_stream, length=8 * 1024 * 1024)
        target_stream.flush()
        os.fsync(target_stream.fileno())


def merge_static(base_static: Path, geometry: Path, output: Path) -> dict:
    with netCDF4.Dataset(geometry) as dataset:
        if dataset.variables["hlm"].shape != (90, *EXPECTED_TARGET_SHAPE):
            raise ValueError("geometry does not match the selected Swiss target")
        if str(dataset.horizon_convention) != "hlm_zenith_angle_degrees_flat_90":
            raise ValueError("geometry horizon convention is unsupported")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() or Path(f"{output}.ready").exists():
        raise ValueError(f"refusing to overwrite terrain-radiation static: {output}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        buffered_copy(base_static, temporary)
        if sha256(temporary) != sha256(base_static):
            raise ValueError("buffered base-static copy is not byte-identical")
        with netCDF4.Dataset(temporary, "a") as target, netCDF4.Dataset(geometry) as source:
            existing = sorted({"azimuth", "hlm", "svf", "slope_rad", "aspect_rad"} & set(target.variables))
            if existing:
                raise ValueError("base static already contains terrain-radiation fields: " + ", ".join(existing))
            target.createDimension("azimuth", 90)
            azimuth = target.createVariable("azimuth", "f4", ("azimuth",))
            azimuth[:] = source.variables["azimuth"][:]
            azimuth.setncatts({name: source.variables["azimuth"].getncattr(name) for name in source.variables["azimuth"].ncattrs()})
            for name in ("hlm", "svf", "slope_rad", "aspect_rad"):
                source_variable = source.variables[name]
                chunks = (1, 128, 256) if source_variable.ndim == 3 else (128, 256)
                target_variable = target.createVariable(
                    name,
                    "f4",
                    source_variable.dimensions,
                    zlib=True,
                    complevel=2,
                    shuffle=True,
                    chunksizes=chunks,
                )
                if source_variable.ndim == 3:
                    for index in range(90):
                        target_variable[index] = source_variable[index]
                else:
                    target_variable[:] = source_variable[:]
                target_variable.setncatts({attribute: source_variable.getncattr(attribute) for attribute in source_variable.ncattrs()})
            target.terrain_radiation_geometry_sha256 = sha256(geometry)
            target.terrain_radiation_horizon_convention = source.horizon_convention
            target.terrain_radiation_search_distance_km = source.search_distance_km
            target.terrain_radiation_source_dem_sha256 = source.source_dem_sha256
            target.terrain_radiation_vertical_datum = source.source_vertical_datum
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    digest = publish_ready(output)
    return {"path": str(output.resolve()), "sha256": digest, "size_bytes": output.stat().st_size}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-static", required=True, type=Path)
    parser.add_argument("--driving-topography", required=True, type=Path)
    parser.add_argument("--extended-dem", required=True, type=Path)
    parser.add_argument("--geometry", required=True, type=Path)
    parser.add_argument("--output-static", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--egm2008-grid", required=True, type=Path)
    parser.add_argument("--search-distance-km", type=float, default=20.0)
    parser.add_argument("--driving-variable", default="HSURF")
    parser.add_argument("--driving-latitude-variable", default="lat")
    parser.add_argument("--driving-longitude-variable", default="lon")
    parser.add_argument("--max-edge-mismatch-m", type=float, default=1.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    for path, label in (
        (args.base_static, "base static"),
        (args.driving_topography, "driving topography"),
        (args.egm2008_grid, "EGM2008 grid"),
    ):
        if not path.is_file():
            raise SystemExit(f"missing {label}: {path}")
    try:
        if args.extended_dem.is_file() and Path(f"{args.extended_dem}.ready").is_file():
            extended = {
                "path": str(args.extended_dem.resolve()),
                "sha256": sha256(args.extended_dem),
                "reused": True,
            }
        else:
            extended = prepare_extended_dem(
                args.base_static,
                args.driving_topography,
                args.extended_dem,
                args.search_distance_km,
                args.driving_variable,
                args.driving_latitude_variable,
                args.driving_longitude_variable,
                args.max_edge_mismatch_m,
            )
        if args.geometry.is_file() and Path(f"{args.geometry}.ready").is_file():
            geometry = {
                "path": str(args.geometry.resolve()),
                "sha256": sha256(args.geometry),
                "reused": True,
            }
        else:
            geometry = compute_geometry(args.extended_dem, args.geometry, args.egm2008_grid)
        final_static = merge_static(args.base_static, args.geometry, args.output_static)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    report = {
        "schema": "icon-hicar-terrain-radiation-geometry-v1",
        "base_static": {"path": str(args.base_static.resolve()), "sha256": sha256(args.base_static)},
        "driving_topography": {
            "path": str(args.driving_topography.resolve()),
            "sha256": sha256(args.driving_topography),
        },
        "egm2008_grid": {"path": str(args.egm2008_grid.resolve()), "sha256": sha256(args.egm2008_grid)},
        "extended_dem": extended,
        "geometry": geometry,
        "output_static": final_static,
    }
    atomic_json(args.report, report)
    publish_ready(args.report)
    print(json.dumps(report["output_static"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
