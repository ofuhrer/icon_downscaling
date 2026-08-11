"""Valid-time ICON land-state transformation for HICAR initial conditions."""

from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
import os
from pathlib import Path
import re
import tempfile

import netCDF4
import numpy as np
from scipy.spatial import cKDTree

from .products import PRODUCT_VERSION, sha256
from .remap import RBFWeights, grid_fingerprint


ICON_TERRA_SOIL_TABLE = "ICON TERRA sfc_terra_data soil types 1..10"
ICON_TERRA_FIELD_CAPACITY = np.array(
    (1.0e-10, 1.0e-10, 0.196, 0.260, 0.340, 0.370, 0.463, 0.763, 1.0e-10, 1.0e-10)
)
ICON_TERRA_WILTING_POINT = np.array((0.0, 0.0, 0.042, 0.100, 0.110, 0.185, 0.257, 0.265, 0.0, 0.0))
ICON_TERRA_POROSITY = np.array(
    (1.0e-10, 1.0e-10, 0.364, 0.445, 0.455, 0.475, 0.507, 0.863, 1.0e-10, 1.0e-10)
)
ICON_T_SO_DEPTHS_M = np.array((0.0, 0.005, 0.02, 0.06, 0.18, 0.54, 1.62, 4.86))
ICON_W_SO_BOUNDS_M = np.array((0.0, 0.01, 0.03, 0.09, 0.27, 0.81, 2.43, 7.29, 21.87))
HICAR_SOIL_BOUNDS_M = np.array((0.0, 0.1, 0.3, 0.7, 1.5))
WATER_DENSITY_KG_M3 = 1000.0
SOIL_WATER_METHODS = ("smi", "relative_saturation", "absolute_w_so")
WATER_SNOW_POLICIES = ("zero", "preserve")
TEMPERATURE_HEIGHT_METHODS = ("int2lm_climatological", "none")


@dataclass(frozen=True)
class SurfaceDiagnostics:
    valid_time: str
    soil_water_method: str
    same_surface_fallback_count: int
    global_finite_fallback_count: int
    cross_surface_in_stencil_fallback_count: int
    minimum_soil_vwc: float
    maximum_soil_vwc: float
    dry_clip_count: int
    saturation_clip_count: int
    glacier_cell_count: int
    water_snow_zeroed_cell_count: int
    maximum_fallback_distance_km: float
    fallback_distance_p99_km: float
    snow_temperature_source: str
    snow_temperature_lower_bound_count: int
    snow_temperature_upper_bound_count: int


def parse_noahmp_stas_hydraulics(path: Path) -> dict[str, np.ndarray]:
    """Read the exact STAS limits used by the selected HICAR executable."""
    text = path.read_text(encoding="utf-8")
    match = re.search(r"&noahmp_soil_stas_parameters\b(.*?)(?:\n\s*/)", text, re.DOTALL)
    if match is None:
        raise ValueError(f"missing noahmp_soil_stas_parameters in {path}")
    section = match.group(1)
    result: dict[str, np.ndarray] = {}
    for name in ("DRYSMC", "MAXSMC", "REFSMC", "WLTSMC"):
        item = re.search(rf"^\s*{name}\s*=\s*(.*)$", section, re.MULTILINE)
        if item is None:
            raise ValueError(f"missing {name} in Noah-MP STAS table {path}")
        values = np.array(
            [float(value.strip()) for value in item.group(1).split(",") if value.strip()],
            dtype=np.float64,
        )
        if values.size != 19:
            raise ValueError(f"Noah-MP STAS {name} has {values.size} values instead of 19")
        result[name] = values
    return result


def icon_soil_water_to_relative_saturation(
    mass_kg_m2: np.ndarray,
    soil_type: np.ndarray,
    source_bounds_m: np.ndarray = ICON_W_SO_BOUNDS_M,
) -> np.ndarray:
    """Derive the volumetric saturation fraction, theta/source porosity."""
    mass = np.asarray(mass_kg_m2, dtype=np.float64)
    bounds = np.asarray(source_bounds_m, dtype=np.float64)
    if mass.shape[0] + 1 != bounds.size or np.any(np.diff(bounds) <= 0.0):
        raise ValueError("W_SO layers disagree with strictly increasing source bounds")
    soil = _integer_soil_type(soil_type, 10, "ICON SOILTYP")
    if soil.shape != mass.shape[1:]:
        raise ValueError("ICON SOILTYP shape disagrees with W_SO")
    thickness = np.diff(bounds).reshape((-1,) + (1,) * (mass.ndim - 1))
    vwc = mass / (WATER_DENSITY_KG_M3 * thickness)
    active = (soil >= 3) & (soil <= 8)
    result = np.full_like(vwc, np.nan)
    np.divide(
        vwc,
        ICON_TERRA_POROSITY[soil - 1][np.newaxis, ...],
        out=result,
        where=active[np.newaxis, ...],
    )
    return np.clip(result, 0.0, 1.0)


def _integer_soil_type(values: np.ndarray, maximum: int, label: str) -> np.ndarray:
    values = np.asarray(values)
    if not np.isfinite(values).all():
        raise ValueError(f"{label} contains non-finite values")
    integer = values.astype(np.int64)
    if not np.allclose(values, integer):
        raise ValueError(f"{label} contains non-integer values")
    if np.min(integer) < 1 or np.max(integer) > maximum:
        raise ValueError(f"{label} lies outside 1..{maximum}")
    return integer


def icon_soil_water_to_smi(
    mass_kg_m2: np.ndarray,
    soil_type: np.ndarray,
    source_bounds_m: np.ndarray = ICON_W_SO_BOUNDS_M,
) -> np.ndarray:
    """Derive TERRA SMI on the native grid before horizontal remapping."""
    mass = np.asarray(mass_kg_m2, dtype=np.float64)
    bounds = np.asarray(source_bounds_m, dtype=np.float64)
    if mass.shape[0] + 1 != bounds.size or np.any(np.diff(bounds) <= 0.0):
        raise ValueError("W_SO layers disagree with strictly increasing source bounds")
    soil = _integer_soil_type(soil_type, 10, "ICON SOILTYP")
    if soil.shape != mass.shape[1:]:
        raise ValueError("ICON SOILTYP shape disagrees with W_SO")
    thickness = np.diff(bounds).reshape((-1,) + (1,) * (mass.ndim - 1))
    vwc = mass / (WATER_DENSITY_KG_M3 * thickness)
    capacity = ICON_TERRA_FIELD_CAPACITY[soil - 1]
    wilting = ICON_TERRA_WILTING_POINT[soil - 1]
    active = (soil >= 3) & (soil <= 8)
    result = np.full_like(vwc, np.nan)
    np.divide(
        vwc - wilting[np.newaxis, ...],
        (capacity - wilting)[np.newaxis, ...],
        out=result,
        where=active[np.newaxis, ...],
    )
    # This matches int2lm's l_smi policy: zero is the lower bound, while
    # values above one remain meaningful because field capacity is not pore
    # saturation.
    return np.maximum(result, 0.0)


def remap_layer_mean(
    values: np.ndarray,
    source_bounds_m: np.ndarray = ICON_W_SO_BOUNDS_M,
    target_bounds_m: np.ndarray = HICAR_SOIL_BOUNDS_M,
) -> np.ndarray:
    """Overlap-average layer means without imposing a column-conservation gate."""
    values = np.asarray(values, dtype=np.float64)
    source = np.asarray(source_bounds_m, dtype=np.float64)
    target = np.asarray(target_bounds_m, dtype=np.float64)
    if values.shape[0] + 1 != source.size:
        raise ValueError("layer count disagrees with source bounds")
    if np.any(np.diff(source) <= 0.0) or np.any(np.diff(target) <= 0.0):
        raise ValueError("soil-layer bounds must be strictly increasing")
    result = np.zeros((target.size - 1,) + values.shape[1:], dtype=np.float64)
    support = np.zeros_like(result)
    for target_index, (top, bottom) in enumerate(zip(target[:-1], target[1:])):
        for source_index, (source_top, source_bottom) in enumerate(zip(source[:-1], source[1:])):
            overlap = max(0.0, min(bottom, source_bottom) - max(top, source_top))
            if overlap:
                finite = np.isfinite(values[source_index])
                result[target_index] += np.where(finite, values[source_index] * overlap, 0.0)
                support[target_index] += finite * overlap
    return np.divide(result, support, out=np.full_like(result, np.nan), where=support > 0.0)


def remap_soil_temperature(
    values: np.ndarray,
    source_depths_m: np.ndarray = ICON_T_SO_DEPTHS_M,
    target_bounds_m: np.ndarray = HICAR_SOIL_BOUNDS_M,
) -> np.ndarray:
    """Interpolate point soil temperatures to HICAR layer midpoints."""
    values = np.asarray(values, dtype=np.float64)
    source_depths = np.asarray(source_depths_m, dtype=np.float64)
    target_depths = 0.5 * (target_bounds_m[:-1] + target_bounds_m[1:])
    if values.shape[0] != source_depths.size or np.any(np.diff(source_depths) <= 0.0):
        raise ValueError("T_SO layers disagree with strictly increasing source depths")
    flat = values.reshape(values.shape[0], -1)
    result = np.empty((target_depths.size, flat.shape[1]), dtype=np.float64)
    for column in range(flat.shape[1]):
        result[:, column] = np.interp(
            target_depths,
            source_depths,
            flat[:, column],
            left=flat[0, column],
            right=flat[-1, column],
        )
    return result.reshape((target_depths.size,) + values.shape[1:])


def noahmp_smi_to_vwc(
    smi: np.ndarray,
    target_soil_type: np.ndarray,
    hydraulics: dict[str, np.ndarray],
) -> np.ndarray:
    """Reconstruct water using the exact target class and Noah-MP STAS table."""
    smi = np.asarray(smi, dtype=np.float64)
    soil = _integer_soil_type(target_soil_type, 19, "target Noah-MP soil type")
    if soil.ndim == 2:
        soil = np.broadcast_to(soil, smi.shape)
    if soil.shape != smi.shape:
        raise ValueError("target soil type must be 2-D or match SMI layers")
    index = soil - 1
    result = hydraulics["WLTSMC"][index] + smi * (
        hydraulics["REFSMC"][index] - hydraulics["WLTSMC"][index]
    )
    return np.clip(result, hydraulics["DRYSMC"][index], hydraulics["MAXSMC"][index])


def noahmp_relative_saturation_to_vwc(
    relative_saturation: np.ndarray,
    target_soil_type: np.ndarray,
    hydraulics: dict[str, np.ndarray],
) -> np.ndarray:
    """Reconstruct target VWC from the fraction of target pore volume."""
    relative = np.asarray(relative_saturation, dtype=np.float64)
    soil = _integer_soil_type(target_soil_type, 19, "target Noah-MP soil type")
    if soil.ndim == 2:
        soil = np.broadcast_to(soil, relative.shape)
    if soil.shape != relative.shape:
        raise ValueError("target soil type must be 2-D or match relative-saturation layers")
    index = soil - 1
    result = np.clip(relative, 0.0, 1.0) * hydraulics["MAXSMC"][index]
    return np.clip(result, hydraulics["DRYSMC"][index], hydraulics["MAXSMC"][index])


def _bounded_absolute_vwc(
    vwc: np.ndarray,
    target_soil_type: np.ndarray,
    hydraulics: dict[str, np.ndarray],
) -> np.ndarray:
    soil = _integer_soil_type(target_soil_type, 19, "target Noah-MP soil type")
    index = np.broadcast_to(soil, vwc.shape) - 1
    return np.clip(vwc, hydraulics["DRYSMC"][index], hydraulics["MAXSMC"][index])


def _supported_remap(
    weights: RBFWeights,
    source: np.ndarray,
    source_land: np.ndarray,
    target_land: np.ndarray,
    *,
    source_lat: np.ndarray | None = None,
    source_lon: np.ndarray | None = None,
    target_lat: np.ndarray | None = None,
    target_lon: np.ndarray | None = None,
    required_target: np.ndarray | None = None,
    monotone: bool = True,
    nonnegative_weights: bool = False,
    allow_cross_surface_in_stencil: bool = False,
    cross_surface_fallback_counts: list[int] | None = None,
    fallback_distances_km: list[float] | None = None,
    global_fallback_masks: list[np.ndarray] | None = None,
    global_fallback_distance_fields_km: list[np.ndarray] | None = None,
) -> tuple[np.ndarray, int, int]:
    """Apply finite same-surface RBF support, optionally recording global fallback.

    Each optional provenance list receives one target-shaped field per leading
    source field. Distances are NaN wherever the global fallback was not used.
    The established three-value return contract is unchanged.
    """
    source = np.asarray(source, dtype=np.float64)
    flat = source.reshape((-1, source.shape[-1]))
    output = np.empty((flat.shape[0], *weights.target_shape), dtype=np.float64)
    fallback_count = 0
    global_fallback_count = 0
    target_flat = np.asarray(target_land, dtype=bool).ravel()
    required_flat = (
        np.ones_like(target_flat)
        if required_target is None
        else np.asarray(required_target, dtype=bool).ravel()
    )
    if required_flat.shape != target_flat.shape:
        raise ValueError("required target support disagrees with interpolation target")
    source_land = np.asarray(source_land, dtype=bool).ravel()
    for field_index, field in enumerate(flat):
        field_global_fallback = np.zeros_like(target_flat, dtype=bool)
        field_global_distance_km = np.full(target_flat.shape, np.nan, dtype=np.float64)
        donors = np.take(field, weights.donor_index, axis=-1)
        donor_land = source_land[weights.donor_index]
        eligible = np.isfinite(donors) & (donor_land == target_flat[:, None])
        base_weight = (
            np.maximum(weights.weight, 0.0) if nonnegative_weights else weights.weight
        )
        local_weight = np.where(eligible, base_weight, 0.0)
        total = np.sum(local_weight, axis=1)
        fallback = np.abs(total) <= 1.0e-14
        local_weight[~fallback] /= total[~fallback, None]
        missing_required = np.zeros_like(fallback)
        if np.any(fallback):
            # A fine-grid lake/coast classification can have no matching
            # coarse-grid surface inside the compact stencil. Match WPS/int2lm
            # fallback behavior by using the nearest finite in-stencil donor,
            # and expose every such exception in the product diagnostics.
            alternatives = np.isfinite(donors[fallback]) & (
                donor_land[fallback] == target_flat[fallback, None]
            )
            has_alternative = np.any(alternatives, axis=1)
            first = np.argmax(alternatives, axis=1)
            local_weight[fallback] = 0.0
            fallback_rows = np.flatnonzero(fallback)
            available_rows = fallback_rows[has_alternative]
            available_first = first[has_alternative]
            local_weight[available_rows, available_first] = 1.0
            eligible[available_rows, available_first] = True
            if fallback_distances_km is not None and available_rows.size and all(
                value is not None for value in (source_lat, source_lon, target_lat, target_lon)
            ):
                chosen_source = weights.donor_index[available_rows, available_first]
                source_xyz = _unit_sphere_points(
                    np.asarray(source_lat).ravel()[chosen_source],
                    np.asarray(source_lon).ravel()[chosen_source],
                )
                target_xyz = _unit_sphere_points(
                    np.asarray(target_lat).ravel()[available_rows],
                    np.asarray(target_lon).ravel()[available_rows],
                )
                chord = np.linalg.norm(source_xyz - target_xyz, axis=1)
                fallback_distances_km.extend(
                    (2.0 * 6371.0 * np.arcsin(np.clip(chord / 2.0, 0.0, 1.0))).tolist()
                )
            unresolved = ~has_alternative
            if allow_cross_surface_in_stencil and np.any(unresolved):
                unresolved_rows = fallback_rows[unresolved]
                cross_alternatives = np.isfinite(donors[unresolved_rows])
                cross_has = np.any(cross_alternatives, axis=1)
                cross_first = np.argmax(cross_alternatives, axis=1)
                cross_rows = unresolved_rows[cross_has]
                cross_choice = cross_first[cross_has]
                local_weight[cross_rows, cross_choice] = 1.0
                eligible[cross_rows, cross_choice] = True
                if cross_surface_fallback_counts is not None:
                    cross_surface_fallback_counts.append(int(cross_rows.size))
                if fallback_distances_km is not None and cross_rows.size and all(
                    value is not None
                    for value in (source_lat, source_lon, target_lat, target_lon)
                ):
                    chosen_source = weights.donor_index[cross_rows, cross_choice]
                    source_xyz = _unit_sphere_points(
                        np.asarray(source_lat).ravel()[chosen_source],
                        np.asarray(source_lon).ravel()[chosen_source],
                    )
                    target_xyz = _unit_sphere_points(
                        np.asarray(target_lat).ravel()[cross_rows],
                        np.asarray(target_lon).ravel()[cross_rows],
                    )
                    chord = np.linalg.norm(source_xyz - target_xyz, axis=1)
                    fallback_distances_km.extend(
                        (
                            2.0
                            * 6371.0
                            * np.arcsin(np.clip(chord / 2.0, 0.0, 1.0))
                        ).tolist()
                    )
                unresolved_positions = np.flatnonzero(unresolved)
                unresolved[unresolved_positions[cross_has]] = False
            missing_required[fallback_rows[unresolved]] = required_flat[
                fallback_rows[unresolved]
            ]
            fallback_count += int(np.sum(fallback & required_flat))
        remapped = np.sum(np.where(eligible, donors, 0.0) * local_weight, axis=1)
        if monotone:
            bounded_donors = np.where(eligible, donors, np.nan)
            has_bound = np.any(np.isfinite(bounded_donors), axis=1)
            lower = np.min(np.where(np.isfinite(bounded_donors), bounded_donors, np.inf), axis=1)
            upper = np.max(np.where(np.isfinite(bounded_donors), bounded_donors, -np.inf), axis=1)
            remapped[has_bound] = np.clip(remapped[has_bound], lower[has_bound], upper[has_bound])
        if np.any(missing_required):
            if any(value is None for value in (source_lat, source_lon, target_lat, target_lon)):
                raise ValueError("global finite fallback requires source and target coordinates")
            for surface_value in (False, True):
                target_missing = missing_required & (target_flat == surface_value)
                if not np.any(target_missing):
                    continue
                finite_source = np.isfinite(field) & (source_land == surface_value)
                if not np.any(finite_source):
                    raise ValueError(
                        "required target point has no finite source value on the same surface"
                    )
                finite_indices = np.flatnonzero(finite_source)
                source_xyz = _unit_sphere_points(
                    np.asarray(source_lat).ravel()[finite_source],
                    np.asarray(source_lon).ravel()[finite_source],
                )
                tree = cKDTree(source_xyz)
                target_xyz = _unit_sphere_points(
                    np.asarray(target_lat).ravel()[target_missing],
                    np.asarray(target_lon).ravel()[target_missing],
                )
                chord, nearest = tree.query(target_xyz, k=1)
                remapped[target_missing] = field[finite_indices[nearest]]
                global_fallback_count += int(np.sum(target_missing))
                distance_km = (
                    2.0
                    * 6371.0
                    * np.arcsin(np.clip(np.asarray(chord) / 2.0, 0.0, 1.0))
                ).ravel()
                field_global_fallback[target_missing] = True
                field_global_distance_km[target_missing] = distance_km
                if fallback_distances_km is not None:
                    fallback_distances_km.extend(distance_km.tolist())
        output[field_index] = remapped.reshape(weights.target_shape)
        if global_fallback_masks is not None:
            global_fallback_masks.append(
                field_global_fallback.reshape(weights.target_shape).copy()
            )
        if global_fallback_distance_fields_km is not None:
            global_fallback_distance_fields_km.append(
                field_global_distance_km.reshape(weights.target_shape).copy()
            )
    return (
        output.reshape((*source.shape[:-1], *weights.target_shape)),
        fallback_count,
        global_fallback_count,
    )


def _unit_sphere_points(latitude: np.ndarray, longitude: np.ndarray) -> np.ndarray:
    latitude = np.deg2rad(np.asarray(latitude, dtype=np.float64))
    longitude = np.deg2rad(np.asarray(longitude, dtype=np.float64))
    return np.column_stack(
        (
            np.cos(latitude) * np.cos(longitude),
            np.cos(latitude) * np.sin(longitude),
            np.sin(latitude),
        )
    )


def _layer_cell(dataset: netCDF4.Dataset, name: str) -> np.ndarray:
    if name not in dataset.variables:
        raise KeyError(f"surface source lacks required field {name}")
    variable = dataset[name]
    values = np.asarray(np.ma.asarray(variable[:]).filled(np.nan), dtype=np.float64)
    dimensions = list(variable.dimensions)
    if "time" in dimensions:
        axis = dimensions.index("time")
        if values.shape[axis] != 1:
            raise ValueError(f"{name}: surface source must contain one valid time")
        values = np.take(values, 0, axis=axis)
        dimensions.pop(axis)
    if "cell" not in dimensions:
        raise ValueError(f"{name}: canonical surface field requires a cell dimension")
    cell_axis = dimensions.index("cell")
    if values.ndim == 1:
        return np.moveaxis(values, cell_axis, -1)
    if values.ndim != 2:
        raise ValueError(f"{name}: expected layer/cell or cell")
    return np.moveaxis(values, cell_axis, -1)


def _iso_utc(value: str) -> str:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def prepare_surface_state(
    source_path: Path,
    static_path: Path,
    output_path: Path,
    *,
    weights: RBFWeights,
    noahmp_table: Path,
    soil_water_method: str = "smi",
    water_snow_policy: str = "zero",
    glacier_landuse_category: int = 24,
    external_path: Path | None = None,
    allow_static_epoch_back_extrapolation: bool = False,
    allow_external_epoch_back_extrapolation: bool = False,
    temperature_height_method: str = "int2lm_climatological",
    climatological_lapse_rate_k_m: float = 0.007,
    valid_time: str | None = None,
) -> SurfaceDiagnostics:
    """Build a target-grid, valid-time soil/snow/skin initial-state product."""
    if soil_water_method not in SOIL_WATER_METHODS:
        raise ValueError(f"soil_water_method must be one of {SOIL_WATER_METHODS}")
    if water_snow_policy not in WATER_SNOW_POLICIES:
        raise ValueError(f"water_snow_policy must be one of {WATER_SNOW_POLICIES}")
    if temperature_height_method not in TEMPERATURE_HEIGHT_METHODS:
        raise ValueError(
            f"temperature_height_method must be one of {TEMPERATURE_HEIGHT_METHODS}"
        )
    if climatological_lapse_rate_k_m < 0.0:
        raise ValueError("climatological_lapse_rate_k_m must be non-negative")
    if external_path is not None and allow_static_epoch_back_extrapolation:
        raise ValueError(
            "static epoch back-extrapolation override is incompatible with an external product"
        )
    if external_path is None and allow_external_epoch_back_extrapolation:
        raise ValueError(
            "external epoch back-extrapolation override requires an external product"
        )
    hydraulics = parse_noahmp_stas_hydraulics(noahmp_table)
    with netCDF4.Dataset(source_path) as source:
        source_time = str(getattr(source, "valid_time", ""))
    chosen_time = valid_time or source_time
    if not chosen_time:
        raise ValueError("surface state requires an explicit valid_time")
    chosen_time = _iso_utc(chosen_time)
    if source_time and _iso_utc(source_time) != chosen_time:
        raise ValueError("requested valid_time disagrees with the ICON surface state")
    static_epoch_back_extrapolated = False
    static_landuse_epoch_valid_from = ""
    external_epoch_back_extrapolated = False
    external_epoch_valid_from = ""
    with netCDF4.Dataset(static_path) as static:
        target_lat = np.asarray(static["lat"][:], dtype=np.float64)
        target_lon = np.asarray(static["lon"][:], dtype=np.float64)
        target_topography = np.asarray(static["topo"][:], dtype=np.float64)
        target_land = np.asarray(static["landmask"][:], dtype=np.float64) >= 0.5
        if external_path is not None:
            from .external import evaluate_external_fields

            when = dt.datetime.fromisoformat(chosen_time.replace("Z", "+00:00"))
            with netCDF4.Dataset(external_path) as external_dataset:
                epochs = np.asarray(external_dataset["epoch_time"][:], dtype=np.float64)
                if epochs.size:
                    first = dt.datetime.fromtimestamp(float(epochs[0]), tz=dt.timezone.utc)
                    external_epoch_valid_from = first.isoformat().replace("+00:00", "Z")
                    external_epoch_back_extrapolated = when < first
            external = evaluate_external_fields(
                external_path,
                when,
                allow_epoch_back_extrapolation=allow_external_epoch_back_extrapolation,
            )
            if "landuse" not in external:
                raise KeyError("external product lacks landuse required for glacier policy")
            target_landuse = np.asarray(external["landuse"], dtype=np.int64)
        elif "landuse" in static.variables:
            landuse_variable = static["landuse"]
            target_landuse = np.asarray(landuse_variable[:], dtype=np.int64)
            if str(getattr(landuse_variable, "hicar_lifetime", "")) == "epoch":
                static_landuse_epoch_valid_from = str(
                    getattr(landuse_variable, "epoch_valid_from", "")
                )
                if not static_landuse_epoch_valid_from:
                    raise ValueError(
                        "static epoch landuse lacks required epoch_valid_from metadata"
                    )
                epoch_start = _iso_utc(static_landuse_epoch_valid_from)
                static_epoch_back_extrapolated = chosen_time < epoch_start
                if (
                    static_epoch_back_extrapolated
                    and not allow_static_epoch_back_extrapolation
                ):
                    raise ValueError(
                        f"static landuse epoch begins at {epoch_start}, after surface "
                        f"valid time {chosen_time}; use a valid earlier epoch or the explicit "
                        "research back-extrapolation override"
                    )
        else:
            raise KeyError(
                "target geometry lacks landuse; supply the lifetime-partitioned external product"
            )
        target_glacier = target_land & (target_landuse == glacier_landuse_category)
        target_soil_active = target_land & ~target_glacier
        if "soil_type_layer" in static.variables:
            target_soil_type = np.asarray(static["soil_type_layer"][:])
            expected = (HICAR_SOIL_BOUNDS_M.size - 1, *target_land.shape)
            if target_soil_type.shape != expected:
                raise ValueError(
                    f"target soil_type_layer has shape {target_soil_type.shape}, expected {expected}"
                )
            target_soil_type_source = "soil_type_layer"
        else:
            target_soil_type = np.broadcast_to(
                np.asarray(static["soil_type"][:]),
                (HICAR_SOIL_BOUNDS_M.size - 1, *target_land.shape),
            )
            target_soil_type_source = "soil_type_broadcast_to_all_layers"
    if weights.target_fingerprint != grid_fingerprint(target_lat, target_lon):
        raise ValueError("cached weights do not belong to the target static grid")

    with netCDF4.Dataset(source_path) as source:
        from .pipeline import read_coordinate

        source_lat = read_coordinate(source, "clat")
        source_lon = read_coordinate(source, "clon")
        if weights.source_fingerprint != grid_fingerprint(source_lat, source_lon):
            raise ValueError("cached weights do not belong to the ICON surface grid")
        w_so = _layer_cell(source, "W_SO")
        t_so = _layer_cell(source, "T_SO")
        skt = _layer_cell(source, "SKT")
        snow_water = _layer_cell(source, "W_SNOW")
        snow_density = _layer_cell(source, "RHO_SNOW")
        source_snow_temperature = (
            _layer_cell(source, "T_SNOW") if "T_SNOW" in source.variables else None
        )
        source_topography = (
            _layer_cell(source, "HSURF") if "HSURF" in source.variables else None
        )
        source_soil_type = _layer_cell(source, "SOILTYP")
        source_land = (
            _layer_cell(source, "FR_LAND") >= 0.5
            if "FR_LAND" in source.variables
            else (source_soil_type >= 1) & (source_soil_type <= 8)
        )
        source_soil_active = source_land & (source_soil_type >= 3) & (source_soil_type <= 8)
        source_t_depths = (
            np.asarray(source["t_so_depth"][:], dtype=np.float64)
            if "t_so_depth" in source.variables
            else ICON_T_SO_DEPTHS_M
        )
        source_w_bounds = (
            np.asarray(source["w_so_bounds"][:], dtype=np.float64)
            if "w_so_bounds" in source.variables
            else ICON_W_SO_BOUNDS_M
        )

    fallbacks = 0
    global_fallbacks = 0
    cross_surface_fallback_counts: list[int] = []
    fallback_distances_km: list[float] = []
    coordinate_arguments = {
        "source_lat": source_lat,
        "source_lon": source_lon,
        "target_lat": target_lat,
        "target_lon": target_lon,
    }
    mapped_t_so, count, global_count = _supported_remap(
        weights,
        t_so,
        source_land,
        target_land,
        required_target=target_land,
        **coordinate_arguments,
        fallback_distances_km=fallback_distances_km,
    )
    fallbacks += count
    global_fallbacks += global_count
    if source_topography is not None:
        mapped_source_topography, count, global_count = _supported_remap(
            weights,
            source_topography,
            np.ones_like(source_land, dtype=bool),
            np.ones_like(target_land, dtype=bool),
            required_target=np.ones_like(target_land),
            **coordinate_arguments,
            fallback_distances_km=fallback_distances_km,
        )
        fallbacks += count
        global_fallbacks += global_count
    else:
        if temperature_height_method == "int2lm_climatological":
            raise KeyError(
                "int2lm_climatological temperature-height correction requires source HSURF"
            )
        # HSURF is optional only for the explicit no-correction control.  Keep
        # the diagnostic variables in the schema, but mark their absence
        # rather than inventing a source terrain.
        mapped_source_topography = np.full(target_land.shape, np.nan)
    mapped_skt, count, global_count = _supported_remap(
        weights,
        skt,
        source_land,
        target_land,
        required_target=np.ones_like(target_land),
        **coordinate_arguments,
        allow_cross_surface_in_stencil=True,
        cross_surface_fallback_counts=cross_surface_fallback_counts,
        fallback_distances_km=fallback_distances_km,
    )
    fallbacks += count
    global_fallbacks += global_count
    snow_required = target_land if water_snow_policy == "zero" else np.ones_like(target_land)
    mapped_snow_water, count, global_count = _supported_remap(
        weights,
        snow_water,
        source_land,
        target_land,
        required_target=snow_required,
        nonnegative_weights=True,
        **coordinate_arguments,
        fallback_distances_km=fallback_distances_km,
    )
    fallbacks += count
    global_fallbacks += global_count
    source_snow = snow_water > 1.0e-9
    if np.any(source_snow & (~np.isfinite(snow_density) | (snow_density <= 0.0))):
        raise ValueError("positive source W_SNOW requires finite positive RHO_SNOW")
    if source_snow_temperature is not None and np.any(
        source_snow
        & (
            ~np.isfinite(source_snow_temperature)
            | (source_snow_temperature < 180.0)
            | (source_snow_temperature > 300.0)
        )
    ):
        raise ValueError("positive source W_SNOW requires plausible finite T_SNOW")
    # Snow density is undefined in snow-free cells and must not be treated as
    # a sparse scalar whose fallback donor can be tens of kilometres away.
    # Remap the two extensive-per-area state components (water and volume),
    # then diagnose density from their ratio on the target grid.
    source_snow_depth = np.divide(
        snow_water,
        snow_density,
        out=np.zeros_like(snow_water),
        where=source_snow,
    )
    mapped_snow_depth, count, global_count = _supported_remap(
        weights,
        source_snow_depth,
        source_land,
        target_land,
        required_target=snow_required,
        nonnegative_weights=True,
        **coordinate_arguments,
        fallback_distances_km=fallback_distances_km,
    )
    fallbacks += count
    global_fallbacks += global_count

    if soil_water_method == "smi":
        native = icon_soil_water_to_smi(w_so, source_soil_type, source_w_bounds)
        mapped, count, global_count = _supported_remap(
            weights,
            native,
            source_soil_active,
            target_soil_active,
            required_target=target_soil_active,
            **coordinate_arguments,
            fallback_distances_km=fallback_distances_km,
        )
        target_smi = remap_layer_mean(mapped, source_w_bounds, HICAR_SOIL_BOUNDS_M)
        transfer_index = target_smi
        soil_vwc = noahmp_smi_to_vwc(
            target_smi, np.where(target_soil_active, target_soil_type, 1), hydraulics
        )
        fallbacks += count
        global_fallbacks += global_count
    elif soil_water_method == "relative_saturation":
        native = icon_soil_water_to_relative_saturation(w_so, source_soil_type, source_w_bounds)
        mapped, count, global_count = _supported_remap(
            weights,
            native,
            source_soil_active,
            target_soil_active,
            required_target=target_soil_active,
            **coordinate_arguments,
            fallback_distances_km=fallback_distances_km,
        )
        target_relative = remap_layer_mean(mapped, source_w_bounds, HICAR_SOIL_BOUNDS_M)
        transfer_index = target_relative
        soil_vwc = noahmp_relative_saturation_to_vwc(
            target_relative, np.where(target_soil_active, target_soil_type, 1), hydraulics
        )
        fallbacks += count
        global_fallbacks += global_count
    else:
        source_thickness = np.diff(source_w_bounds).reshape((-1, 1))
        native_vwc = w_so / (WATER_DENSITY_KG_M3 * source_thickness)
        mapped, count, global_count = _supported_remap(
            weights,
            native_vwc,
            source_soil_active,
            target_soil_active,
            required_target=target_soil_active,
            **coordinate_arguments,
            fallback_distances_km=fallback_distances_km,
        )
        transfer_index = remap_layer_mean(mapped, source_w_bounds, HICAR_SOIL_BOUNDS_M)
        soil_vwc = transfer_index.copy()
        soil_vwc = _bounded_absolute_vwc(
            soil_vwc, np.where(target_soil_active, target_soil_type, 1), hydraulics
        )
        fallbacks += count
        global_fallbacks += global_count

    lookup_soil = np.where(target_soil_active, target_soil_type, 1).astype(np.int64) - 1
    lookup_soil = np.broadcast_to(lookup_soil, soil_vwc.shape)
    if soil_water_method == "smi":
        raw_target_vwc = hydraulics["WLTSMC"][lookup_soil] + transfer_index * (
            hydraulics["REFSMC"][lookup_soil] - hydraulics["WLTSMC"][lookup_soil]
        )
    elif soil_water_method == "relative_saturation":
        raw_target_vwc = transfer_index * hydraulics["MAXSMC"][lookup_soil]
    else:
        raw_target_vwc = transfer_index
    dry_clip = (raw_target_vwc < hydraulics["DRYSMC"][lookup_soil]) & target_soil_active
    saturation_clip = (
        raw_target_vwc > hydraulics["MAXSMC"][lookup_soil]
    ) & target_soil_active
    # Glacier columns are not porous soil. As in int2lm's non-soil handling,
    # do not import neighbouring mineral-soil moisture into them; the explicit
    # USGS ice category lets Noah-MP select its glacier physics instead.
    soil_vwc[:, ~target_soil_active] = 0.0
    transfer_index[:, ~target_soil_active] = np.nan

    soil_temperature = remap_soil_temperature(mapped_t_so, source_t_depths)
    terrain_height_difference = target_topography - mapped_source_topography
    if temperature_height_method == "int2lm_climatological":
        correction = -climatological_lapse_rate_k_m * terrain_height_difference
        mapped_skt[target_land] += correction[target_land]
        target_depths = 0.5 * (HICAR_SOIL_BOUNDS_M[:-1] + HICAR_SOIL_BOUNDS_M[1:])
        x_zero = 3.0
        c = 3.0 * x_zero
        clipped_depth = np.clip(target_depths, -x_zero, x_zero)
        depth_weight = (
            9.0 * c * c * clipped_depth + 27.0 * clipped_depth**3
        ) / (c**3 + 27.0 * c * clipped_depth**2)
        soil_temperature[:, target_land] += (
            depth_weight[:, None] * correction[target_land][None, :]
        )
    # HICAR reads the full soil-temperature array before the land/water
    # physics split. TERRA soil levels are undefined over source water, so a
    # same-surface soil remap can legitimately have no value there. Populate
    # those inactive columns with the valid-time skin temperature instead of
    # leaving artificial zero-K placeholders in the runtime file.
    soil_temperature[:, ~target_land] = mapped_skt[~target_land][None, :]
    snow_water_equivalent = np.maximum(mapped_snow_water, 0.0)
    snow_depth = np.maximum(mapped_snow_depth, 0.0)
    unresolved_snow = (snow_water_equivalent > 1.0e-9) & (snow_depth <= 1.0e-12)
    if np.any(unresolved_snow):
        raise ValueError("remapped positive SWE has no corresponding snow volume")
    snow_density = np.divide(
        snow_water_equivalent,
        snow_depth,
        out=np.zeros_like(snow_water_equivalent),
        where=snow_water_equivalent > 1.0e-9,
    )
    water_snow_zeroed = (~target_land) & (snow_water_equivalent > 0.0)
    if water_snow_policy == "zero":
        snow_water_equivalent[~target_land] = 0.0
        snow_density[~target_land] = 0.0
        snow_depth[~target_land] = 0.0
    target_snow = snow_water_equivalent > 1.0e-9
    snow_temperature = np.minimum(mapped_skt, 273.15)
    snow_temperature_source = "skin_temperature_capped_at_freezing"
    if source_snow_temperature is not None and np.any(target_snow):
        mapped_snow_temperature, count, global_count = _supported_remap(
            weights,
            source_snow_temperature,
            source_snow,
            target_snow,
            required_target=target_snow,
            **coordinate_arguments,
            fallback_distances_km=fallback_distances_km,
        )
        fallbacks += count
        global_fallbacks += global_count
        snow_temperature[target_snow] = mapped_snow_temperature[target_snow]
        snow_temperature_source = "ICON T_SNOW"
    snow_temperature_upper_bound = np.minimum(mapped_skt, 273.15)
    snow_temperature_lower_bound = np.minimum(
        np.maximum(mapped_skt - 10.0, 180.0), snow_temperature_upper_bound
    )
    snow_temperature_lower_bound_count = int(
        np.sum(target_snow & (snow_temperature < snow_temperature_lower_bound))
    )
    snow_temperature_upper_bound_count = int(
        np.sum(target_snow & (snow_temperature > snow_temperature_upper_bound))
    )
    snow_temperature[target_snow] = np.clip(
        snow_temperature[target_snow],
        snow_temperature_lower_bound[target_snow],
        snow_temperature_upper_bound[target_snow],
    )
    for label, values in {
        "soil_temperature": soil_temperature[:, target_land],
        "soil_vwc": soil_vwc[:, target_soil_active],
        "skin_temperature": mapped_skt,
        "snow_water_equivalent": snow_water_equivalent,
        "snow_depth": snow_depth,
        "snow_temperature": snow_temperature,
    }.items():
        if not np.isfinite(values).all():
            raise ValueError(f"prepared {label} contains non-finite values on its support")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.", suffix=".partial", dir=output_path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with netCDF4.Dataset(temporary, "w") as output:
            ny, nx = target_lat.shape
            output.createDimension("y", ny)
            output.createDimension("x", nx)
            output.createDimension("soil_layer", HICAR_SOIL_BOUNDS_M.size - 1)
            output.createDimension("soil_interface", HICAR_SOIL_BOUNDS_M.size)
            definitions = {
                "soil_temperature": (soil_temperature, ("soil_layer", "y", "x"), "K"),
                "soil_vwc": (soil_vwc, ("soil_layer", "y", "x"), "m3 m-3"),
                "soil_water_transfer_index": (
                    transfer_index,
                    ("soil_layer", "y", "x"),
                    "1" if soil_water_method != "absolute_w_so" else "m3 m-3",
                ),
                "skin_temperature": (mapped_skt, ("y", "x"), "K"),
                "snow_water_equivalent": (snow_water_equivalent, ("y", "x"), "kg m-2"),
                "snow_density": (snow_density, ("y", "x"), "kg m-3"),
                "snow_depth": (snow_depth, ("y", "x"), "m"),
                "snow_temperature_initial": (snow_temperature, ("y", "x"), "K"),
                "source_topography_on_target": (
                    mapped_source_topography,
                    ("y", "x"),
                    "m",
                ),
                "terrain_height_difference": (
                    terrain_height_difference,
                    ("y", "x"),
                    "m",
                ),
            }
            for name, (values, dimensions, units) in definitions.items():
                variable = output.createVariable(name, "f8", dimensions, zlib=True)
                variable[:] = values
                variable.units = units
                variable.hicar_lifetime = "initial_only"
            bounds = output.createVariable("soil_layer_bounds_m", "f8", ("soil_interface",))
            bounds[:] = HICAR_SOIL_BOUNDS_M
            bounds.units = "m"
            output.product_type = "initial_surface_state"
            output.hicarprep_product_version = PRODUCT_VERSION
            output.valid_time = chosen_time
            output.soil_water_method = soil_water_method
            output.soil_water_default = "smi"
            output.soil_water_method_definition = {
                "smi": "(theta-PWP)/(field_capacity-PWP), lower bounded at zero",
                "relative_saturation": "theta/porosity, bounded to [0,1]",
                "absolute_w_so": "direct source theta; diagnostic control across soil classes",
            }[soil_water_method]
            output.soil_water_conservation_required = "false"
            output.glacier_policy = (
                f"landuse={glacier_landuse_category}: no porous-soil water transfer; "
                "soil_vwc=0; retain remapped glacier temperature and snow"
            )
            output.glacier_cell_count = int(np.sum(target_glacier))
            output.water_snow_policy = water_snow_policy
            output.water_snow_zeroed_cell_count = int(np.sum(water_snow_zeroed))
            output.snow_temperature_source = snow_temperature_source
            output.snow_temperature_policy = (
                "same-snow-support remap; target snow bounded to min(max(TSK-10 K,"
                "180 K),upper)..upper with upper=min(TSK,273.15 K); snow-free cells "
                "use min(TSK,273.15 K)"
            )
            output.snow_temperature_lower_bound_count = snow_temperature_lower_bound_count
            output.snow_temperature_upper_bound_count = snow_temperature_upper_bound_count
            output.target_soil_type_source = target_soil_type_source
            output.temperature_height_method = temperature_height_method
            output.climatological_lapse_rate_k_m = climatological_lapse_rate_k_m
            output.temperature_height_reference = (
                "int2lm src_2d_fields climatological -7 K km-1 and depth blendfunc(x,3m); "
                "skin correction applied fully because HICAR receives target-terrain atmosphere"
                if temperature_height_method == "int2lm_climatological"
                else "none"
            )
            output.soil_water_dry_clip_count = int(np.sum(dry_clip))
            output.soil_water_saturation_clip_count = int(np.sum(saturation_clip))
            output.same_surface_normalization = (
                "finite donors matching target land/water support; nearest finite in-stencil "
                "fallback when no matching surface exists"
            )
            output.skin_temperature_cross_surface_policy = (
                "when a fine-grid water cell has no coarse-grid water donor in the compact "
                "stencil, use the nearest finite in-stencil SKT donor; never search for a "
                "distant water analogue"
            )
            output.source_topography_surface_policy = (
                "continuous field remapped without land/water masking"
            )
            output.nonland_soil_temperature_policy = (
                "inactive HICAR soil columns filled with valid-time remapped skin temperature"
            )
            output.static_landuse_epoch_valid_from = static_landuse_epoch_valid_from
            output.static_epoch_back_extrapolation = (
                "explicit_research_override"
                if static_epoch_back_extrapolated
                else "none"
            )
            output.external_epoch_valid_from = external_epoch_valid_from
            output.external_epoch_back_extrapolation = (
                "explicit_research_override"
                if external_epoch_back_extrapolated
                else "none"
            )
            output.same_surface_fallback_count = fallbacks
            output.global_finite_fallback_count = global_fallbacks
            output.cross_surface_in_stencil_fallback_count = int(
                sum(cross_surface_fallback_counts)
            )
            output.maximum_fallback_distance_km = (
                float(np.max(fallback_distances_km)) if fallback_distances_km else 0.0
            )
            output.fallback_distance_p99_km = (
                float(np.quantile(fallback_distances_km, 0.99))
                if fallback_distances_km
                else 0.0
            )
            output.icon_terra_soil_table = ICON_TERRA_SOIL_TABLE
            output.noahmp_table_path = str(noahmp_table)
            output.noahmp_table_sha256 = sha256(noahmp_table)
            output.source_surface_sha256 = sha256(source_path)
            output.static_sha256 = sha256(static_path)
            if external_path is not None:
                output.external_parameters_sha256 = sha256(external_path)
            output.horizontal_operator = weights.method
        os.replace(temporary, output_path)
    finally:
        temporary.unlink(missing_ok=True)

    return SurfaceDiagnostics(
        valid_time=chosen_time,
        soil_water_method=soil_water_method,
        same_surface_fallback_count=fallbacks,
        global_finite_fallback_count=global_fallbacks,
        cross_surface_in_stencil_fallback_count=int(sum(cross_surface_fallback_counts)),
        minimum_soil_vwc=float(np.min(soil_vwc[:, target_soil_active])),
        maximum_soil_vwc=float(np.max(soil_vwc[:, target_soil_active])),
        dry_clip_count=int(np.sum(dry_clip)),
        saturation_clip_count=int(np.sum(saturation_clip)),
        glacier_cell_count=int(np.sum(target_glacier)),
        water_snow_zeroed_cell_count=int(np.sum(water_snow_zeroed)),
        maximum_fallback_distance_km=(
            float(np.max(fallback_distances_km)) if fallback_distances_km else 0.0
        ),
        fallback_distance_p99_km=(
            float(np.quantile(fallback_distances_km, 0.99)) if fallback_distances_km else 0.0
        ),
        snow_temperature_source=snow_temperature_source,
        snow_temperature_lower_bound_count=snow_temperature_lower_bound_count,
        snow_temperature_upper_bound_count=snow_temperature_upper_bound_count,
    )
