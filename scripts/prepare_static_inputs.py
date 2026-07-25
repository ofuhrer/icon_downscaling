#!/usr/bin/env python3
"""Create a small HICAR static/domain NetCDF file."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request
import xml.etree.ElementTree as ET

import netCDF4
import numpy as np
from pyproj import CRS, Transformer
from scipy.interpolate import LinearNDInterpolator, RegularGridInterpolator
from scipy.spatial import cKDTree


USGS_WATER_CATEGORY = 16
DEFAULT_CACHE_SUBDIR = "icon_hicar/cache/hicar_static_public"

COPERNICUS_DEM_URL = (
    "https://copernicus-dem-30m.s3.amazonaws.com/"
    "Copernicus_DSM_COG_10_{lat_tag}_00_{lon_tag}_00_DEM/"
    "Copernicus_DSM_COG_10_{lat_tag}_00_{lon_tag}_00_DEM.tif"
)
ESA_WORLDCOVER_URL = (
    "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"
    "ESA_WorldCover_10m_2021_v200_{lat_tag}{lon_tag}_Map.tif"
)
SOILGRIDS_VRT_URL = "https://files.isric.org/soilgrids/latest/data/{prop}/{prop}_{depth}_mean.vrt"
SOILGRIDS_DEPTHS = ("0-5cm", "5-15cm", "15-30cm", "30-60cm")

WORLDCOVER_TO_USGS = {
    10: 15,  # tree cover -> mixed forest
    20: 8,   # shrubland
    30: 7,   # grassland/herbaceous
    40: 2,   # cropland
    50: 1,   # built-up
    60: 19,  # bare/sparse vegetation
    70: 24,  # snow and ice
    80: 16,  # permanent water bodies
    90: 17,  # herbaceous wetland
    95: 17,  # mangroves -> wetland fallback for USGS
    100: 19, # moss/lichen -> sparse vegetation fallback
}

SOIL_TYPE_DEFAULT_VWC = {
    1: 0.12,   # sand
    2: 0.14,   # loamy sand
    3: 0.18,   # sandy loam
    4: 0.32,   # silt loam
    5: 0.32,   # silt
    6: 0.28,   # loam
    7: 0.27,   # sandy clay loam
    8: 0.36,   # silty clay loam
    9: 0.34,   # clay loam
    10: 0.32,  # sandy clay
    11: 0.38,  # silty clay
    12: 0.40,  # clay
    13: 0.50,  # organic
}


def read_source_var(path: Path, name: str) -> tuple[np.ndarray, tuple[str, ...]]:
    with netCDF4.Dataset(path) as ds:
        if name not in ds.variables:
            available = ", ".join(sorted(ds.variables))
            raise SystemExit(f"{path}: variable {name!r} not found; available: {available}")
        var = ds.variables[name]
        data = np.ma.asarray(var[:]).astype(np.float64).filled(np.nan)
        dims = tuple(var.dimensions)
    return np.squeeze(data), dims


def source_latlon(path: Path, lat_name: str, lon_name: str) -> tuple[np.ndarray, np.ndarray]:
    lat, _ = read_source_var(path, lat_name)
    lon, _ = read_source_var(path, lon_name)
    lat = np.squeeze(lat)
    lon = np.squeeze(lon)
    lon = ((lon + 180.0) % 360.0) - 180.0
    return lat, lon


def _rectilinear_coords_from_2d(src_lat: np.ndarray, src_lon: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    if src_lat.ndim != 2 or src_lon.ndim != 2 or src_lat.shape != src_lon.shape:
        return None
    lat_col = src_lat[:, 0]
    lon_row = src_lon[0, :]
    if np.allclose(src_lat, lat_col[:, np.newaxis], equal_nan=True) and np.allclose(src_lon, lon_row[np.newaxis, :], equal_nan=True):
        return lat_col, lon_row
    return None


def interp_to_domain(
    src_path: Path,
    src_var_name: str,
    src_lat_name: str,
    src_lon_name: str,
    dst_lat: np.ndarray,
    dst_lon: np.ndarray,
    method: str,
) -> np.ndarray:
    src_data, _ = read_source_var(src_path, src_var_name)
    src_lat, src_lon = source_latlon(src_path, src_lat_name, src_lon_name)

    if src_data.ndim != 2:
        raise SystemExit(f"{src_path}: {src_var_name!r} must be 2D after squeezing, got {src_data.shape}")

    rectilinear = (src_lat, src_lon) if src_lat.ndim == 1 and src_lon.ndim == 1 else _rectilinear_coords_from_2d(src_lat, src_lon)
    if rectilinear is not None:
        data = src_data
        lat_1d, lon_1d = rectilinear
        if lat_1d[0] > lat_1d[-1]:
            lat_1d = lat_1d[::-1]
            data = data[::-1, :]
        if lon_1d[0] > lon_1d[-1]:
            lon_1d = lon_1d[::-1]
            data = data[:, ::-1]

        interpolator = RegularGridInterpolator(
            (lat_1d, lon_1d),
            data,
            method="nearest" if method == "nearest" else "linear",
            bounds_error=False,
            fill_value=np.nan,
        )
        return interpolator(np.column_stack([dst_lat.ravel(), dst_lon.ravel()])).reshape(dst_lat.shape)

    if src_lat.ndim == 2 and src_lon.ndim == 2:
        if src_lat.shape != src_lon.shape or src_lat.shape != src_data.shape:
            raise SystemExit(
                f"{src_path}: curvilinear source shapes differ: "
                f"lat={src_lat.shape}, lon={src_lon.shape}, data={src_data.shape}"
            )
        points = np.column_stack([src_lat.ravel(), src_lon.ravel()])
        values = src_data.ravel()
        valid = np.isfinite(points).all(axis=1) & np.isfinite(values)
        if not np.any(valid):
            raise SystemExit(f"{src_path}: no finite points found for {src_var_name!r}")
        dst_points = np.column_stack([dst_lat.ravel(), dst_lon.ravel()])
        if method == "nearest":
            tree = cKDTree(points[valid])
            _, idx = tree.query(dst_points, k=1)
            return values[valid][idx].reshape(dst_lat.shape)
        interpolator = LinearNDInterpolator(points[valid], values[valid], fill_value=np.nan)
        return interpolator(dst_points).reshape(dst_lat.shape)

    raise SystemExit(f"{src_path}: source lat/lon must both be 1D or both be 2D")


def boundary_blend_weight(x: np.ndarray, y: np.ndarray, width_m: float, shape: str) -> np.ndarray:
    if width_m <= 0.0:
        raise SystemExit("--topo-blend-width-km must be positive when --boundary-topo-source is used")
    xx, yy = np.meshgrid(x, y)
    distance = np.minimum.reduce([xx - x.min(), x.max() - xx, yy - y.min(), y.max() - yy])
    t = np.clip(distance / width_m, 0.0, 1.0)
    if shape == "linear":
        weight = t
    elif shape == "cosine":
        weight = 0.5 - 0.5 * np.cos(np.pi * t)
    elif shape == "smootherstep":
        weight = t * t * t * (t * (t * 6.0 - 15.0) + 10.0)
    else:
        raise SystemExit(f"unsupported topography blend shape: {shape}")
    return weight.astype(np.float32)


def blend_topography(
    topo_highres: np.ndarray,
    topo_driving: np.ndarray,
    blend_weight: np.ndarray,
) -> np.ndarray:
    if topo_highres.shape != topo_driving.shape or topo_highres.shape != blend_weight.shape:
        raise SystemExit(
            "topography blend shape mismatch: "
            f"highres={topo_highres.shape}, driving={topo_driving.shape}, weight={blend_weight.shape}"
        )
    return ((1.0 - blend_weight) * topo_driving + blend_weight * topo_highres).astype(np.float32)


def make_grid(center_lat: float, center_lon: float, width_km: float, height_km: float, dx_m: float):
    nx = int(round(width_km * 1000.0 / dx_m)) + 1
    ny = int(round(height_km * 1000.0 / dx_m)) + 1
    if nx < 3 or ny < 3:
        raise SystemExit("domain must contain at least 3 x 3 points")

    x = (np.arange(nx, dtype=np.float64) - (nx - 1) / 2.0) * dx_m
    y = (np.arange(ny, dtype=np.float64) - (ny - 1) / 2.0) * dx_m
    xx, yy = np.meshgrid(x, y)

    local_crs = CRS.from_proj4(
        f"+proj=aeqd +lat_0={center_lat} +lon_0={center_lon} "
        "+x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs"
    )
    to_geo = Transformer.from_crs(local_crs, "EPSG:4326", always_xy=True)
    lon, lat = to_geo.transform(xx, yy)
    return x, y, lat.astype(np.float32), lon.astype(np.float32), local_crs


def default_cache_dir() -> Path:
    scratch = os.environ.get("SCRATCH")
    if scratch:
        return Path(scratch) / DEFAULT_CACHE_SUBDIR
    return Path.cwd() / ".cache" / "hicar_static_public"


def require_cmd(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise SystemExit(f"required command not found: {name}; install GDAL command-line tools")
    return path


def run_cmd(cmd: list[str]) -> None:
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"command failed with exit {exc.returncode}: {' '.join(cmd)}") from exc


def lat_tag_1deg(lat: int) -> str:
    return f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}"


def lon_tag_1deg(lon: int) -> str:
    return f"{'E' if lon >= 0 else 'W'}{abs(lon):03d}"


def worldcover_tile_origin(value: float) -> int:
    return int(math.floor(value / 3.0) * 3)


def needed_1deg_tiles(lat: np.ndarray, lon: np.ndarray, border_deg: float = 0.05) -> list[tuple[int, int]]:
    lat_min = math.floor(float(np.nanmin(lat)) - border_deg)
    lat_max = math.floor(float(np.nanmax(lat)) + border_deg)
    lon_min = math.floor(float(np.nanmin(lon)) - border_deg)
    lon_max = math.floor(float(np.nanmax(lon)) + border_deg)
    return [(ilat, ilon) for ilat in range(lat_min, lat_max + 1) for ilon in range(lon_min, lon_max + 1)]


def needed_worldcover_tiles(lat: np.ndarray, lon: np.ndarray, border_deg: float = 0.05) -> list[tuple[int, int]]:
    lat_min = worldcover_tile_origin(float(np.nanmin(lat)) - border_deg)
    lat_max = worldcover_tile_origin(float(np.nanmax(lat)) + border_deg)
    lon_min = worldcover_tile_origin(float(np.nanmin(lon)) - border_deg)
    lon_max = worldcover_tile_origin(float(np.nanmax(lon)) + border_deg)
    return [(ilat, ilon) for ilat in range(lat_min, lat_max + 1, 3) for ilon in range(lon_min, lon_max + 1, 3)]


def cached_download(url: str, target: Path, offline: bool = False) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size > 0:
        return target
    if offline:
        raise SystemExit(f"cache miss in --offline mode: {target}")
    tmp = target.with_suffix(target.suffix + ".tmp")
    print(f"download {url} -> {target}")
    try:
        urllib.request.urlretrieve(url, tmp)
    except Exception as exc:
        if tmp.exists():
            tmp.unlink()
        raise SystemExit(f"failed to download {url}: {exc}") from exc
    tmp.replace(target)
    return target


def copernicus_dem_tiles(lat: np.ndarray, lon: np.ndarray, cache_dir: Path, offline: bool) -> list[Path]:
    out = []
    for ilat, ilon in needed_1deg_tiles(lat, lon):
        lat_tag = lat_tag_1deg(ilat)
        lon_tag = lon_tag_1deg(ilon)
        url = COPERNICUS_DEM_URL.format(lat_tag=lat_tag, lon_tag=lon_tag)
        out.append(cached_download(url, cache_dir / "copernicus_dem_glo30" / f"{lat_tag}_{lon_tag}.tif", offline))
    return out


def worldcover_tiles(lat: np.ndarray, lon: np.ndarray, cache_dir: Path, offline: bool) -> list[Path]:
    out = []
    for ilat, ilon in needed_worldcover_tiles(lat, lon):
        lat_tag = lat_tag_1deg(ilat)
        lon_tag = lon_tag_1deg(ilon)
        url = ESA_WORLDCOVER_URL.format(lat_tag=lat_tag, lon_tag=lon_tag)
        out.append(cached_download(url, cache_dir / "esa_worldcover_2021_v200" / f"{lat_tag}_{lon_tag}.tif", offline))
    return out


def raster_to_xyz_array(path: Path, nx: int, ny: int) -> np.ndarray:
    with tempfile.NamedTemporaryFile(suffix=".xyz") as tmp:
        run_cmd(["gdal_translate", "-q", "-of", "XYZ", str(path), tmp.name])
        values = np.loadtxt(tmp.name, usecols=2)
    if values.size != nx * ny:
        raise SystemExit(f"{path}: expected {nx * ny} raster cells, got {values.size}")
    return values.reshape(ny, nx)[::-1, :]


def warp_to_domain(
    sources: list[str | Path],
    out_tif: Path,
    local_crs: CRS,
    x: np.ndarray,
    y: np.ndarray,
    dx_m: float,
    resampling: str,
    source_crs_override: CRS | None = None,
) -> np.ndarray:
    out_tif.parent.mkdir(parents=True, exist_ok=True)
    if out_tif.exists() and out_tif.stat().st_size > 0:
        if shutil.which("gdal_translate"):
            return raster_to_xyz_array(out_tif, x.size, y.size)
        try:
            import rasterio
            with rasterio.open(out_tif) as ds:
                return ds.read(1)[::-1, :]
        except ImportError:
            pass

    xmin = float(x.min() - dx_m / 2.0)
    xmax = float(x.max() + dx_m / 2.0)
    ymin = float(y.min() - dx_m / 2.0)
    ymax = float(y.max() + dx_m / 2.0)
    # Prefer the site GDAL CLI.  A rasterio fallback keeps the public-static
    # workflow usable on Balfrin, where the Python wheel carries GDAL but the
    # gdalwarp/gdal_translate command-line tools are not installed.
    if not shutil.which("gdalwarp"):
        try:
            import rasterio
            from rasterio.enums import Resampling
            from rasterio.transform import from_origin
            from rasterio.warp import reproject
        except ImportError as exc:
            raise SystemExit("required command not found: gdalwarp; install GDAL tools or Python rasterio") from exc
        transform = from_origin(xmin, ymax, dx_m, dx_m)
        destination = np.full((y.size, x.size), np.nan, dtype=np.float32)
        method = Resampling.nearest if resampling == "near" else Resampling.bilinear
        for source in sources:
            source_path = str(source).replace("/vsicurl/", "")
            with rasterio.open(source_path) as ds:
                candidate = np.full(destination.shape, np.nan, dtype=np.float32)
                reproject(
                    source=rasterio.band(ds, 1), destination=candidate,
                    src_transform=ds.transform, src_crs=(source_crs_override.to_wkt() if source_crs_override else ds.crs),
                    src_nodata=ds.nodata, dst_transform=transform,
                    dst_crs=local_crs.to_wkt(), dst_nodata=np.nan,
                    resampling=method,
                )
                destination[np.isnan(destination) & np.isfinite(candidate)] = candidate[np.isnan(destination) & np.isfinite(candidate)]
        if np.isnan(destination).any():
            raise SystemExit(f"raster source does not fully cover requested domain: {sources}")
        profile = {"driver":"GTiff", "height":y.size, "width":x.size, "count":1, "dtype":"float32", "crs":local_crs.to_wkt(), "transform":transform, "compress":"deflate"}
        with rasterio.open(out_tif, "w", **profile) as ds:
            ds.write(destination, 1)
        return destination[::-1, :]
    require_cmd("gdal_translate")
    cmd = [
        "gdalwarp",
        "-q",
        "-overwrite",
        "-t_srs",
        local_crs.to_wkt(),
        "-te",
        str(xmin),
        str(ymin),
        str(xmax),
        str(ymax),
        "-tr",
        str(dx_m),
        str(dx_m),
        "-r",
        resampling,
        "-of",
        "GTiff",
        "-co",
        "COMPRESS=DEFLATE",
    ]
    cmd.extend(str(src) for src in sources)
    cmd.append(str(out_tif))
    run_cmd(cmd)
    return raster_to_xyz_array(out_tif, x.size, y.size)


def domain_cache_key(lat: np.ndarray, lon: np.ndarray, x: np.ndarray, y: np.ndarray, dx_m: float) -> str:
    text = (
        f"lat={float(np.nanmin(lat)):.6f}:{float(np.nanmax(lat)):.6f};"
        f"lon={float(np.nanmin(lon)):.6f}:{float(np.nanmax(lon)):.6f};"
        f"nx={x.size};ny={y.size};dx={dx_m:.3f}"
    )
    return hashlib.sha1(text.encode("ascii")).hexdigest()[:12]


def reclass_worldcover_to_usgs(worldcover: np.ndarray) -> np.ndarray:
    landuse = np.full(worldcover.shape, 7, dtype=np.int16)
    rounded = np.rint(worldcover).astype(np.int16)
    for wc_value, usgs_value in WORLDCOVER_TO_USGS.items():
        landuse[rounded == wc_value] = usgs_value
    return landuse


def classify_usda_soil(sand_pct: np.ndarray, silt_pct: np.ndarray, clay_pct: np.ndarray) -> np.ndarray:
    soil = np.full(sand_pct.shape, 6, dtype=np.int16)  # loam fallback
    sand = sand_pct
    silt = silt_pct
    clay = clay_pct

    soil[(sand >= 85) & ((silt + 1.5 * clay) < 15)] = 1
    soil[(sand >= 70) & (sand < 90) & ((silt + 1.5 * clay) >= 15) & ((silt + 2 * clay) < 30)] = 2
    soil[((clay >= 7) & (clay < 20) & (sand > 52) & ((silt + 2 * clay) >= 30)) | ((clay < 7) & (silt < 50) & ((silt + 2 * clay) >= 30))] = 3
    soil[((silt >= 50) & (clay >= 12) & (clay < 27)) | ((silt >= 50) & (silt < 80) & (clay < 12))] = 4
    soil[(silt >= 80) & (clay < 12)] = 5
    soil[(clay >= 7) & (clay < 27) & (silt >= 28) & (silt < 50) & (sand <= 52)] = 6
    soil[(clay >= 20) & (clay < 35) & (silt < 28) & (sand > 45)] = 7
    soil[(clay >= 27) & (clay < 40) & (sand <= 20)] = 8
    soil[(clay >= 27) & (clay < 40) & (sand > 20) & (sand <= 45)] = 9
    soil[(clay >= 35) & (sand > 45)] = 10
    soil[(clay >= 40) & (silt >= 40)] = 11
    soil[(clay >= 40) & (sand <= 45) & (silt < 40)] = 12
    return soil


def texture_default_vwc(soil_type: np.ndarray) -> np.ndarray:
    out = np.full(soil_type.shape, 0.28, dtype=np.float32)
    for soil_id, vwc in SOIL_TYPE_DEFAULT_VWC.items():
        out[soil_type == soil_id] = vwc
    return out


def soilgrids_subset(
    prop: str,
    depth: str,
    cache_dir: Path,
    local_crs: CRS,
    x: np.ndarray,
    y: np.ndarray,
    dx_m: float,
    offline: bool,
    key: str,
) -> np.ndarray:
    url = SOILGRIDS_VRT_URL.format(prop=prop, depth=depth)
    out_tif = cache_dir / "warped_subsets" / f"soilgrids_{prop}_{depth}_{key}.tif"
    if offline and not out_tif.exists():
        raise SystemExit(f"cache miss in --offline mode: {out_tif}")
    if out_tif.exists():
        return warp_to_domain([], out_tif, local_crs, x, y, dx_m, "bilinear")

    # SoilGrids publishes global VRT mosaics whose relative source paths are
    # not resolved by Rasterio's HTTP driver on Balfrin.  Download only the
    # VRT tiles intersecting this domain, then reproject the local COGs.
    vrt_path = cached_download(url, cache_dir / "soilgrids_vrt" / f"{prop}_{depth}.vrt", offline)
    try:
        root = ET.parse(vrt_path).getroot()
        soil_crs = CRS.from_wkt(root.findtext("SRS"))
        gt = [float(v.strip()) for v in root.findtext("GeoTransform").split(",")]
    except Exception as exc:
        raise SystemExit(f"could not parse SoilGrids VRT {vrt_path}: {exc}") from exc

    to_soil = Transformer.from_crs(local_crs, soil_crs, always_xy=True)
    xx, yy = np.meshgrid([x.min(), x.max()], [y.min(), y.max()])
    sx, sy = to_soil.transform(xx.ravel(), yy.ravel())
    px = [(value - gt[0]) / gt[1] for value in sx]
    py = [(value - gt[3]) / gt[5] for value in sy]
    xmin, xmax = min(px), max(px)
    ymin, ymax = min(py), max(py)

    selected_groups: set[str] = set()
    complex_sources = root.findall(".//ComplexSource")
    for complex_source in complex_sources:
        source_name = complex_source.findtext("SourceFilename")
        dst = complex_source.find("DstRect")
        if not source_name or dst is None:
            continue
        dx0 = float(dst.attrib["xOff"])
        dy0 = float(dst.attrib["yOff"])
        dx1 = dx0 + float(dst.attrib["xSize"])
        dy1 = dy0 + float(dst.attrib["ySize"])
        if dx1 <= xmin or dx0 >= xmax or dy1 <= ymin or dy0 >= ymax:
            continue
        selected_groups.add(source_name.removeprefix("./").split("/")[-2])
    if not selected_groups:
        raise SystemExit(f"SoilGrids VRT has no tiles covering requested domain for {prop}/{depth}")

    # A VRT tile group is a 4x4 COG block.  Fetch the whole selected group:
    # individual tile georeferencing near interrupted-projection seams is not
    # sufficient for Rasterio to fill a target grid reliably.
    sources: list[Path] = []
    vrt_parent = url.rsplit("/", 1)[0]
    for complex_source in complex_sources:
        source_name = complex_source.findtext("SourceFilename")
        if not source_name:
            continue
        relative = source_name.removeprefix("./")
        if relative.split("/")[-2] not in selected_groups:
            continue
        source_url = f"{vrt_parent}/{relative}"
        sources.append(cached_download(source_url, cache_dir / "soilgrids_tiles" / relative, offline))
    # Sample directly in the SoilGrids projected coordinates.  Rasterio/GDAL
    # cannot safely reproject these COGs on Balfrin because their per-tile CRS
    # metadata has an anonymous datum while the VRT has WGS84.  Direct affine
    # indexing keeps the authoritative VRT projection and avoids extrapolation.
    try:
        import rasterio
    except ImportError as exc:
        raise SystemExit("Python rasterio is required for SoilGrids tile sampling") from exc
    xx, yy = np.meshgrid(x, y)
    sx, sy = to_soil.transform(xx, yy)
    destination = np.full(xx.shape, np.nan, dtype=np.float32)
    for source in sources:
        with rasterio.open(source) as ds:
            bounds = ds.bounds
            mask = (
                np.isnan(destination)
                & (sx >= bounds.left) & (sx < bounds.right)
                & (sy > bounds.bottom) & (sy <= bounds.top)
            )
            if not np.any(mask):
                continue
            cols, rows = (~ds.transform) * (sx[mask], sy[mask])
            cols = np.floor(cols).astype(np.int64)
            rows = np.floor(rows).astype(np.int64)
            valid = (rows >= 0) & (rows < ds.height) & (cols >= 0) & (cols < ds.width)
            values = np.full(rows.shape, np.nan, dtype=np.float32)
            data = ds.read(1)
            values[valid] = data[rows[valid], cols[valid]]
            if ds.nodata is not None:
                values[values == ds.nodata] = np.nan
            destination[mask] = values
    if np.isnan(destination).any():
        # SoilGrids has small no-data seams between COG blocks.  Fill only
        # those cells from the nearest valid SoilGrids texture value; do not
        # introduce a synthetic uniform soil category.
        valid = np.isfinite(destination)
        if not np.any(valid):
            raise SystemExit(f"SoilGrids tiles contain no valid values over requested domain: {sources}")
        from scipy.ndimage import distance_transform_edt
        _, nearest = distance_transform_edt(~valid, return_indices=True)
        destination[~valid] = destination[tuple(index[~valid] for index in nearest)]
    return destination


def build_public_static(
    lat: np.ndarray,
    lon: np.ndarray,
    x: np.ndarray,
    y: np.ndarray,
    local_crs: CRS,
    dx_m: float,
    cache_dir: Path,
    offline: bool,
    skip_soilgrids: bool,
    include_land_surface: bool,
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, np.ndarray | None, list[str]]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    sources = []
    key = domain_cache_key(lat, lon, x, y, dx_m)

    dem_paths = copernicus_dem_tiles(lat, lon, cache_dir, offline)
    topo = warp_to_domain(dem_paths, cache_dir / "warped_subsets" / f"copernicus_dem_{key}.tif", local_crs, x, y, dx_m, "bilinear")
    sources.append("Copernicus DEM GLO-30 Public")

    wc_paths = worldcover_tiles(lat, lon, cache_dir, offline)
    wc = warp_to_domain(wc_paths, cache_dir / "warped_subsets" / f"esa_worldcover_2021_{key}.tif", local_crs, x, y, dx_m, "near")
    landuse = reclass_worldcover_to_usgs(wc)
    sources.append("ESA WorldCover 2021 v200 reclassified to USGS")

    if not include_land_surface:
        soil_type = None
        soil_vwc_base = None
    elif skip_soilgrids:
        soil_type = np.full(lat.shape, 8, dtype=np.int16)  # silty clay loam pragmatic fallback
        soil_vwc_base = texture_default_vwc(soil_type)
        sources.append("constant silty-clay-loam soil fallback")
    else:
        clay = soilgrids_subset("clay", "0-5cm", cache_dir, local_crs, x, y, dx_m, offline, key) / 10.0
        sand = soilgrids_subset("sand", "0-5cm", cache_dir, local_crs, x, y, dx_m, offline, key) / 10.0
        silt = soilgrids_subset("silt", "0-5cm", cache_dir, local_crs, x, y, dx_m, offline, key) / 10.0
        soil_type = classify_usda_soil(sand, silt, clay)
        soil_vwc_base = texture_default_vwc(soil_type)
        sources.append("SoilGrids 250 m 0-5 cm mean texture")

    return topo, landuse, soil_type, soil_vwc_base, sources


def put_2d(ds, name: str, data: np.ndarray, units: str, long_name: str, dtype="f4"):
    var = ds.createVariable(name, dtype, ("y", "x"), zlib=True)
    var[:, :] = data
    var.units = units
    var.long_name = long_name
    if name in {"lat", "lon"}:
        var.standard_name = "latitude" if name == "lat" else "longitude"
        var.units = "degrees_north" if name == "lat" else "degrees_east"
    else:
        var.coordinates = "lon lat"
    var.grid_mapping = "azimuthal_equidistant"
    return var


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--center-lat", type=float, default=46.75)
    parser.add_argument("--center-lon", type=float, default=8.15)
    parser.add_argument("--width-km", type=float, default=20.0)
    parser.add_argument("--height-km", type=float, default=20.0)
    parser.add_argument("--dx-m", type=float, default=250.0)
    parser.add_argument(
        "--public-sources",
        action="store_true",
        help="Use public Copernicus DEM, ESA WorldCover, and SoilGrids inputs with cache reuse.",
    )
    parser.add_argument(
        "--static-field-set",
        choices=("core", "land-surface"),
        default="land-surface",
        help=(
            "Fields to write. 'core' writes only coordinates/topography/land mask/land use; "
            "'land-surface' also writes pragmatic soil and surface initialization fields."
        ),
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=default_cache_dir(),
        help="Cache for downloaded/warped public datasets [SCRATCH/icon_hicar/cache/hicar_static_public or .cache].",
    )
    parser.add_argument("--offline", action="store_true", help="Use only already cached public-source files.")
    parser.add_argument(
        "--skip-soilgrids",
        action="store_true",
        help="Skip SoilGrids and use a constant silty-clay-loam soil fallback.",
    )
    parser.add_argument("--topo-source", type=Path)
    parser.add_argument("--topo-var", default="topo")
    parser.add_argument("--source-lat-var", default="lat")
    parser.add_argument("--source-lon-var", default="lon")
    parser.add_argument("--source-interp", choices=("nearest", "linear"), default="linear")
    parser.add_argument(
        "--boundary-topo-source",
        type=Path,
        help="Prepared ICON/HICAR forcing NetCDF containing driving-model topography for boundary blending.",
    )
    parser.add_argument("--boundary-topo-var", default="HSURF")
    parser.add_argument("--boundary-topo-lat-var", default="lat_1")
    parser.add_argument("--boundary-topo-lon-var", default="lon_1")
    parser.add_argument("--boundary-topo-interp", choices=("nearest", "linear"), default="linear")
    parser.add_argument(
        "--topo-blend-width-km",
        type=float,
        default=10.0,
        help="Width of the lateral boundary zone that transitions from driving topography to high-resolution topography.",
    )
    parser.add_argument(
        "--topo-blend-shape",
        choices=("cosine", "linear", "smootherstep"),
        default="cosine",
        help="Shape of the high-resolution topography weight across the boundary blend zone.",
    )
    parser.add_argument(
        "--write-topo-blend-diagnostics",
        action="store_true",
        help="Write topo_highres, topo_driving, and topo_blend_weight diagnostic fields.",
    )
    parser.add_argument("--placeholder-topo-m", type=float, default=1500.0)
    parser.add_argument("--allow-placeholder-static", action="store_true")
    parser.add_argument("--landuse-source", type=Path)
    parser.add_argument("--landuse-var", default="landuse")
    parser.add_argument("--lu-categories", default="USGS")
    parser.add_argument("--landuse-category", type=int, default=7)
    parser.add_argument("--soil-type", type=int, default=3)
    parser.add_argument("--soil-vwc", type=float, default=0.4)
    parser.add_argument("--surface-temp-k", type=float, default=280.0)
    parser.add_argument("--soil-layers", type=int, default=4)
    args = parser.parse_args()

    if args.dx_m <= 0 or args.width_km <= 0 or args.height_km <= 0:
        raise SystemExit("--dx-m, --width-km, and --height-km must be positive")
    include_land_surface = args.static_field_set == "land-surface"

    if include_land_surface and args.soil_layers < 1:
        raise SystemExit("--soil-layers must be positive")
    if args.public_sources and (args.topo_source or args.landuse_source):
        raise SystemExit("--public-sources cannot be combined with --topo-source or --landuse-source")
    if args.topo_source is None and not args.public_sources and not args.allow_placeholder_static:
        raise SystemExit("provide --topo-source or set --allow-placeholder-static for a non-scientific placeholder domain")
    if args.write_topo_blend_diagnostics and not args.boundary_topo_source:
        raise SystemExit("--write-topo-blend-diagnostics requires --boundary-topo-source")
    if args.boundary_topo_source and not args.boundary_topo_source.exists():
        raise SystemExit(f"missing boundary topography source: {args.boundary_topo_source}")
    if args.boundary_topo_source and args.topo_blend_width_km <= 0:
        raise SystemExit("--topo-blend-width-km must be positive when --boundary-topo-source is used")

    x, y, lat, lon, local_crs = make_grid(args.center_lat, args.center_lon, args.width_km, args.height_km, args.dx_m)

    public_sources_used: list[str] = []
    topo_highres = None
    topo_driving = None
    topo_blend_weight = None
    if args.public_sources:
        topo, landuse, soil_type, soil_vwc_base, public_sources_used = build_public_static(
            lat=lat,
            lon=lon,
            x=x,
            y=y,
            local_crs=local_crs,
            dx_m=args.dx_m,
            cache_dir=args.cache_dir,
            offline=args.offline,
            skip_soilgrids=args.skip_soilgrids,
            include_land_surface=include_land_surface,
        )
    elif args.topo_source:
        topo = interp_to_domain(
            args.topo_source,
            args.topo_var,
            args.source_lat_var,
            args.source_lon_var,
            lat,
            lon,
            args.source_interp,
        )
        if np.isnan(topo).any():
            raise SystemExit("topography source does not fully cover requested domain")
    else:
        topo = np.full(lat.shape, args.placeholder_topo_m, dtype=np.float32)

    if args.boundary_topo_source:
        topo_highres = topo.astype(np.float32)
        topo_driving = interp_to_domain(
            args.boundary_topo_source,
            args.boundary_topo_var,
            args.boundary_topo_lat_var,
            args.boundary_topo_lon_var,
            lat,
            lon,
            args.boundary_topo_interp,
        ).astype(np.float32)
        if np.isnan(topo_driving).any():
            raise SystemExit("boundary topography source does not fully cover requested domain")
        topo_blend_weight = boundary_blend_weight(x, y, args.topo_blend_width_km * 1000.0, args.topo_blend_shape)
        topo = blend_topography(topo_highres, topo_driving, topo_blend_weight)

    if args.public_sources:
        pass
    elif args.landuse_source:
        landuse = interp_to_domain(
            args.landuse_source,
            args.landuse_var,
            args.source_lat_var,
            args.source_lon_var,
            lat,
            lon,
            "nearest",
        )
        if np.isnan(landuse).any():
            raise SystemExit("landuse source does not fully cover requested domain")
        landuse = np.rint(landuse).astype(np.int16)
    else:
        landuse = np.full(lat.shape, args.landuse_category, dtype=np.int16)

    landmask = np.where(landuse == USGS_WATER_CATEGORY, 0, 1).astype(np.int16)
    if include_land_surface and not args.public_sources:
        soil_type = np.full(lat.shape, args.soil_type, dtype=np.int16)
        soil_vwc_base = np.full(lat.shape, args.soil_vwc, dtype=np.float32)
    if include_land_surface:
        if soil_type is None or soil_vwc_base is None:
            raise SystemExit("internal error: land-surface field set requested but soil fields were not prepared")
        surface_temp = np.full(lat.shape, args.surface_temp_k, dtype=np.float32)
        soil_t = np.repeat(surface_temp[np.newaxis, :, :], args.soil_layers, axis=0)
        soil_vwc = np.repeat(soil_vwc_base[np.newaxis, :, :], args.soil_layers, axis=0).astype(np.float32)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    ready_file = Path(str(args.output) + ".ready")
    ready_file.unlink(missing_ok=True)
    with netCDF4.Dataset(args.output, "w", format="NETCDF4") as ds:
        ds.createDimension("x", x.size)
        ds.createDimension("y", y.size)
        if include_land_surface:
            ds.createDimension("soil_layer", args.soil_layers)

        x_var = ds.createVariable("x", "f4", ("x",))
        x_var[:] = x
        x_var.standard_name = "projection_x_coordinate"
        x_var.units = "m"
        x_var.long_name = "local x coordinate"
        x_var.axis = "X"

        y_var = ds.createVariable("y", "f4", ("y",))
        y_var[:] = y
        y_var.standard_name = "projection_y_coordinate"
        y_var.units = "m"
        y_var.long_name = "local y coordinate"
        y_var.axis = "Y"

        crs_var = ds.createVariable("azimuthal_equidistant", "i4")
        crs_var.grid_mapping_name = "azimuthal_equidistant"
        crs_var.latitude_of_projection_origin = args.center_lat
        crs_var.longitude_of_projection_origin = args.center_lon
        crs_var.false_easting = 0.0
        crs_var.false_northing = 0.0
        crs_var.semi_major_axis = 6378137.0
        crs_var.inverse_flattening = 298.257223563
        crs_var.crs_wkt = local_crs.to_wkt()

        if include_land_surface:
            soil_var = ds.createVariable("soil_layer", "i4", ("soil_layer",))
            soil_var[:] = np.arange(1, args.soil_layers + 1)
            soil_var.long_name = "soil layer index"

        put_2d(ds, "lat", lat, "degrees_north", "latitude")
        put_2d(ds, "lon", lon, "degrees_east", "longitude")
        put_2d(ds, "topo", topo.astype(np.float32), "m", "terrain height")
        if args.write_topo_blend_diagnostics:
            put_2d(ds, "topo_highres", topo_highres, "m", "unblended high-resolution terrain height")
            put_2d(ds, "topo_driving", topo_driving, "m", "driving-model terrain height interpolated to HICAR grid")
            put_2d(ds, "topo_blend_weight", topo_blend_weight, "1", "high-resolution topography blend weight")
        put_2d(ds, "landmask", landmask, "1", "land mask", dtype="i2")
        put_2d(ds, "landuse", landuse, "1", f"{args.lu_categories} land-use category", dtype="i2")
        if include_land_surface:
            put_2d(ds, "soil_type", soil_type, "1", "soil type category", dtype="i2")
            put_2d(ds, "surface_temperature", surface_temp, "K", "initial surface temperature")
            put_2d(ds, "soil_deep_temperature", surface_temp, "K", "initial deep soil temperature")

            # Python writes C-order NetCDF dimensions. HICAR's Fortran NetCDF API
            # sees these reversed, so soil_layer,y,x is read as x,y,soil_layer.
            soil_t_var = ds.createVariable("soil_temperature", "f4", ("soil_layer", "y", "x"), zlib=True)
            soil_t_var[:, :, :] = soil_t
            soil_t_var.units = "K"
            soil_t_var.long_name = "initial soil temperature"
            soil_t_var.coordinates = "lon lat"
            soil_t_var.grid_mapping = "azimuthal_equidistant"

            soil_vwc_var = ds.createVariable("soil_vwc", "f4", ("soil_layer", "y", "x"), zlib=True)
            soil_vwc_var[:, :, :] = soil_vwc
            soil_vwc_var.units = "m3 m-3"
            soil_vwc_var.long_name = "initial volumetric soil water content"
            soil_vwc_var.coordinates = "lon lat"
            soil_vwc_var.grid_mapping = "azimuthal_equidistant"

        ds.Conventions = "CF-1.8"
        ds.title = "HICAR static domain"
        ds.history = f"{dt.datetime.utcnow().isoformat(timespec='seconds')}Z: created by prepare_static_inputs.py"
        ds.hicar_dx_m = args.dx_m
        ds.hicar_center_lat = args.center_lat
        ds.hicar_center_lon = args.center_lon
        ds.hicar_projection = local_crs.to_proj4()
        ds.hicar_lu_categories = args.lu_categories
        ds.hicar_static_field_set = args.static_field_set
        if args.public_sources:
            ds.hicar_static_quality = "public_source_pragmatic"
            ds.public_sources = "; ".join(public_sources_used)
            ds.public_cache_dir = str(args.cache_dir)
            if include_land_surface:
                ds.soil_initialization = (
                    "soil_type from SoilGrids texture with texture-dependent default volumetric water content"
                    if not args.skip_soilgrids
                    else "constant silty-clay-loam soil fallback with texture-dependent default volumetric water content"
                )
            else:
                ds.soil_surface_initialization = "not written; leave HICAR optional soil/surface initialization namelist variables blank to use model defaults"
        else:
            ds.hicar_static_quality = "placeholder" if args.topo_source is None or args.landuse_source is None else "source_interpolated"
            if not include_land_surface:
                ds.soil_surface_initialization = "not written; leave HICAR optional soil/surface initialization namelist variables blank to use model defaults"
        if args.topo_source:
            ds.topography_source = str(args.topo_source)
        if args.boundary_topo_source:
            ds.boundary_topography_source = str(args.boundary_topo_source)
            ds.boundary_topography_variable = args.boundary_topo_var
            ds.boundary_topography_lat_variable = args.boundary_topo_lat_var
            ds.boundary_topography_lon_variable = args.boundary_topo_lon_var
            ds.boundary_topography_interpolation = args.boundary_topo_interp
            ds.topography_blend_width_km = args.topo_blend_width_km
            ds.topography_blend_shape = args.topo_blend_shape
            ds.topography_blend_formula = "topo=(1-w)*topo_driving+w*topo_highres"
            ds.topography_blend_note = "topo is blended near lateral boundaries; land-use and soil fields remain from the static sources"
        if args.landuse_source:
            ds.landuse_source = str(args.landuse_source)

    ready_file.touch()
    print(args.output)
    print(ready_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
