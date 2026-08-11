"""Valid-time water-temperature remapping for HICAR regular forcing."""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
import tempfile

import netCDF4
import numpy as np

from .products import sha256
from .remap import RBFWeights, grid_fingerprint
from .surface import _supported_remap


def _normalized_time(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def build_target_sst_product(
    path: Path,
    *,
    source_skt: np.ndarray,
    source_lat: np.ndarray,
    source_lon: np.ndarray,
    source_land: np.ndarray,
    static_path: Path,
    weights: RBFWeights,
    valid_time: str,
    source_path: Path,
) -> dict[str, float | int | str]:
    """Remap one REA-L skin-temperature state with water-only support."""
    source_skt = np.asarray(source_skt, dtype=np.float64).ravel()
    source_lat = np.asarray(source_lat, dtype=np.float64).ravel()
    source_lon = np.asarray(source_lon, dtype=np.float64).ravel()
    source_land = np.asarray(source_land, dtype=bool).ravel()
    if not (
        source_skt.shape == source_lat.shape == source_lon.shape == source_land.shape
    ):
        raise ValueError("REA-L SKT, coordinates and surface mask must share one cell axis")
    if weights.source_fingerprint != grid_fingerprint(source_lat, source_lon):
        raise ValueError("SST weights do not belong to the REA-L source grid")
    if not np.isfinite(source_skt).all() or np.any(
        (source_skt < 180.0) | (source_skt > 350.0)
    ):
        raise ValueError("REA-L SKT lies outside the conservative 180..350 K range")

    with netCDF4.Dataset(static_path) as static:
        target_lat = np.asarray(static["lat"][:], dtype=np.float64)
        target_lon = np.asarray(static["lon"][:], dtype=np.float64)
        target_land = np.asarray(static["landmask"][:], dtype=np.float64) >= 0.5
    if weights.target_fingerprint != grid_fingerprint(target_lat, target_lon):
        raise ValueError("SST weights do not belong to the HICAR target grid")
    if not np.any(~target_land):
        raise ValueError("HICAR target grid has no water cells for SST forcing")

    baseline = weights.apply(source_skt, monotone=True)
    fallback_distances_km: list[float] = []
    global_fallback_masks: list[np.ndarray] = []
    global_fallback_distance_fields_km: list[np.ndarray] = []
    supported, local_fallbacks, global_fallbacks = _supported_remap(
        weights,
        source_skt,
        source_land,
        target_land,
        source_lat=source_lat,
        source_lon=source_lon,
        target_lat=target_lat,
        target_lon=target_lon,
        required_target=~target_land,
        monotone=True,
        fallback_distances_km=fallback_distances_km,
        global_fallback_masks=global_fallback_masks,
        global_fallback_distance_fields_km=global_fallback_distance_fields_km,
    )
    if len(global_fallback_masks) != 1 or len(global_fallback_distance_fields_km) != 1:
        raise RuntimeError("scalar SST remap did not produce one fallback provenance field")
    global_fallback_mask = global_fallback_masks[0]
    global_fallback_distance_km = global_fallback_distance_fields_km[0]
    if int(np.count_nonzero(global_fallback_mask)) != global_fallbacks:
        raise RuntimeError("SST global fallback count disagrees with its target mask")
    if np.any(global_fallback_mask & target_land):
        raise RuntimeError("SST global fallback unexpectedly modified a target land cell")
    sst = np.where(target_land, baseline, supported)
    if not np.isfinite(sst).all() or np.any(
        (sst[~target_land] < 180.0) | (sst[~target_land] > 350.0)
    ):
        raise ValueError("remapped water temperature is non-finite or implausible")

    when = _normalized_time(valid_time)
    diagnostics: dict[str, float | int | str] = {
        "valid_time": when.isoformat().replace("+00:00", "Z"),
        "water_cell_count": int(np.sum(~target_land)),
        "water_local_fallback_count": int(local_fallbacks),
        "water_global_fallback_count": int(global_fallbacks),
        "maximum_fallback_distance_km": (
            float(max(fallback_distances_km)) if fallback_distances_km else 0.0
        ),
        "maximum_global_fallback_distance_km": (
            float(np.nanmax(global_fallback_distance_km))
            if global_fallbacks
            else 0.0
        ),
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".partial", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with netCDF4.Dataset(temporary, "w") as dataset:
            ny, nx = target_lat.shape
            dataset.createDimension("y", ny)
            dataset.createDimension("x", nx)
            dataset.createVariable("lat", "f8", ("y", "x"))[:] = target_lat
            dataset.createVariable("lon", "f8", ("y", "x"))[:] = target_lon
            variable = dataset.createVariable("SST", "f4", ("y", "x"), zlib=True)
            variable[:] = sst
            variable.units = "K"
            variable.hicar_support = "water cells; land values are finite placeholders"
            dataset.createVariable("water_mask", "i1", ("y", "x"), zlib=True)[:] = (
                ~target_land
            )
            fallback_mask_variable = dataset.createVariable(
                "global_fallback_mask", "i1", ("y", "x"), zlib=True
            )
            fallback_mask_variable[:] = global_fallback_mask
            fallback_mask_variable.long_name = (
                "target cells filled from nearest finite same-surface source outside "
                "the compact RBF stencil"
            )
            fallback_distance_variable = dataset.createVariable(
                "global_fallback_distance_km", "f8", ("y", "x"), zlib=True
            )
            fallback_distance_variable[:] = global_fallback_distance_km
            fallback_distance_variable.units = "km"
            fallback_distance_variable.long_name = (
                "great-circle distance to global same-surface fallback source; "
                "NaN where no global fallback was used"
            )
            dataset.product_type = "hicarprep_target_water_temperature"
            dataset.valid_time = diagnostics["valid_time"]
            dataset.static_sha256 = sha256(static_path)
            dataset.target_grid_fingerprint = grid_fingerprint(target_lat, target_lon)
            dataset.source_path = str(source_path)
            dataset.source_sha256 = sha256(source_path)
            dataset.source_variable = "SKT"
            dataset.remap_policy = "same-surface water support; RBF baseline on land"
            for name, value in diagnostics.items():
                if name != "valid_time":
                    dataset.setncattr(name, value)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return diagnostics


def load_target_sst(
    path: Path,
    *,
    static_path: Path,
    valid_time: str,
    target_lat: np.ndarray,
    target_lon: np.ndarray,
    target_land: np.ndarray,
    static_digest: str | None = None,
) -> np.ndarray:
    """Validate and load an exact-grid, exact-time target SST product."""
    expected_time = _normalized_time(valid_time)
    target_lat = np.asarray(target_lat, dtype=np.float64)
    target_lon = np.asarray(target_lon, dtype=np.float64)
    target_land = np.asarray(target_land, dtype=bool)
    with netCDF4.Dataset(path) as dataset:
        if str(getattr(dataset, "product_type", "")) != (
            "hicarprep_target_water_temperature"
        ):
            raise ValueError("SST input is not a hicarprep target-water-temperature product")
        actual_time = str(getattr(dataset, "valid_time", ""))
        if not actual_time or _normalized_time(actual_time) != expected_time:
            raise ValueError("SST valid_time does not match the atmospheric state")
        expected_static_digest = static_digest or sha256(static_path)
        if str(getattr(dataset, "static_sha256", "")) != expected_static_digest:
            raise ValueError("SST input does not belong to the supplied runtime domain")
        fingerprint = grid_fingerprint(target_lat, target_lon)
        if str(getattr(dataset, "target_grid_fingerprint", "")) != fingerprint:
            raise ValueError("SST target-grid fingerprint does not match the runtime domain")
        if tuple(dataset["SST"].dimensions) != ("y", "x"):
            raise ValueError("SST must be a two-dimensional target-grid field")
        sst = np.asarray(np.ma.asarray(dataset["SST"][:]).filled(np.nan), dtype=np.float64)
        water_mask = np.asarray(dataset["water_mask"][:], dtype=bool)
        if sst.shape != target_lat.shape or water_mask.shape != target_lat.shape:
            raise ValueError("SST input shape does not match the runtime domain")
        if not np.array_equal(dataset["lat"][:], target_lat) or not np.array_equal(
            dataset["lon"][:], target_lon
        ):
            raise ValueError("SST coordinates differ from the runtime domain")
        if not np.array_equal(water_mask, ~target_land):
            raise ValueError("SST water mask differs from the runtime domain")
        units = str(getattr(dataset["SST"], "units", "")).strip().lower()
        if units not in {"k", "kelvin"}:
            raise ValueError("SST units must be kelvin")
    if not np.isfinite(sst).all() or np.any(
        (sst[~target_land] < 180.0) | (sst[~target_land] > 350.0)
    ):
        raise ValueError("SST is non-finite or outside 180..350 K on water")
    return sst
