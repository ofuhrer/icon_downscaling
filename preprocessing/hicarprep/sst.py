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


SST_POLICY_VERSION = "sst-local-baseline-v1"
SST_REMAP_POLICY = (
    "compact same-surface water support; exact-valid-time monotone all-surface "
    "RBF baseline for unsupported water"
)


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
    """Remap REA-L skin temperature with compact water support and a local baseline."""
    source_skt = np.asarray(source_skt, dtype=np.float64).ravel()
    source_lat = np.asarray(source_lat, dtype=np.float64).ravel()
    source_lon = np.asarray(source_lon, dtype=np.float64).ravel()
    source_land = np.asarray(source_land, dtype=bool).ravel()
    if not (source_skt.shape == source_lat.shape == source_lon.shape == source_land.shape):
        raise ValueError("REA-L SKT, coordinates and surface mask must share one cell axis")
    if weights.source_fingerprint != grid_fingerprint(source_lat, source_lon):
        raise ValueError("SST weights do not belong to the REA-L source grid")
    if not np.isfinite(source_skt).all() or np.any((source_skt < 180.0) | (source_skt > 350.0)):
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
    unsupported_water_masks: list[np.ndarray] = []
    nearest_candidate_distance_fields_km: list[np.ndarray] = []
    supported, all_fallbacks, unsupported_count = _supported_remap(
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
        global_fallback_masks=unsupported_water_masks,
        global_fallback_distance_fields_km=nearest_candidate_distance_fields_km,
    )
    if len(unsupported_water_masks) != 1 or len(nearest_candidate_distance_fields_km) != 1:
        raise RuntimeError("scalar SST remap did not produce one support-provenance field")
    unsupported_water_mask = unsupported_water_masks[0]
    nearest_candidate_distance_km = nearest_candidate_distance_fields_km[0]
    if int(np.count_nonzero(unsupported_water_mask)) != unsupported_count:
        raise RuntimeError("SST unsupported-water count disagrees with its target mask")
    if np.any(unsupported_water_mask & target_land):
        raise RuntimeError("SST unsupported-water mask unexpectedly includes target land")
    # The same-surface routine computes the nearest remote water value to expose
    # candidate distance, but that value is scientifically inappropriate for a
    # fine Alpine lake absent from the coarse source mask.  Preserve the target
    # water classification and use the exact-valid-time local RBF baseline.
    supported[unsupported_water_mask] = baseline[unsupported_water_mask]
    sst = np.where(target_land, baseline, supported)
    if not np.isfinite(sst).all() or np.any(
        (sst[~target_land] < 180.0) | (sst[~target_land] > 350.0)
    ):
        raise ValueError("remapped water temperature is non-finite or implausible")

    when = _normalized_time(valid_time)
    diagnostics: dict[str, float | int | str] = {
        "valid_time": when.isoformat().replace("+00:00", "Z"),
        "water_cell_count": int(np.sum(~target_land)),
        "water_compact_fallback_count": int(all_fallbacks - unsupported_count),
        "water_unsupported_count": int(unsupported_count),
        "maximum_nearest_same_surface_candidate_distance_km": (
            float(np.nanmax(nearest_candidate_distance_km)) if unsupported_count else 0.0
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
            dataset.createVariable("water_mask", "i1", ("y", "x"), zlib=True)[:] = ~target_land
            unsupported_mask_variable = dataset.createVariable(
                "unsupported_water_mask", "i1", ("y", "x"), zlib=True
            )
            unsupported_mask_variable[:] = unsupported_water_mask
            unsupported_mask_variable.long_name = (
                "target water cells lacking finite same-surface support in the compact "
                "RBF stencil and filled from the local all-surface RBF baseline"
            )
            candidate_distance_variable = dataset.createVariable(
                "nearest_same_surface_candidate_distance_km",
                "f8",
                ("y", "x"),
                zlib=True,
            )
            candidate_distance_variable[:] = nearest_candidate_distance_km
            candidate_distance_variable.units = "km"
            candidate_distance_variable.long_name = (
                "great-circle distance to nearest finite same-surface source candidate; "
                "diagnostic only because the candidate value was not used"
            )
            dataset.product_type = "hicarprep_target_water_temperature"
            dataset.sst_policy_version = SST_POLICY_VERSION
            dataset.valid_time = diagnostics["valid_time"]
            dataset.static_sha256 = sha256(static_path)
            dataset.target_grid_fingerprint = grid_fingerprint(target_lat, target_lon)
            dataset.source_path = str(source_path)
            dataset.source_sha256 = sha256(source_path)
            dataset.source_variable = "SKT"
            dataset.remap_policy = SST_REMAP_POLICY
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
        if str(getattr(dataset, "product_type", "")) != ("hicarprep_target_water_temperature"):
            raise ValueError("SST input is not a hicarprep target-water-temperature product")
        if (
            str(getattr(dataset, "sst_policy_version", "")) != SST_POLICY_VERSION
            or str(getattr(dataset, "remap_policy", "")) != SST_REMAP_POLICY
        ):
            raise ValueError("SST input lacks the selected local-baseline remapping contract")
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
