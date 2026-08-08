"""Scientific plausibility diagnostics for alternative soil-water transfers."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Mapping

import netCDF4
import numpy as np

from .products import sha256
from .surface import (
    HICAR_SOIL_BOUNDS_M,
    _layer_cell,
    icon_soil_water_to_relative_saturation,
    icon_soil_water_to_smi,
    parse_noahmp_stas_hydraulics,
)


def _quantiles(values: np.ndarray) -> dict[str, float]:
    finite = np.asarray(values, dtype=np.float64)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        return {name: float("nan") for name in ("minimum", "p01", "p50", "p99", "maximum")}
    result = np.quantile(finite, (0.0, 0.01, 0.5, 0.99, 1.0))
    return dict(zip(("minimum", "p01", "p50", "p99", "maximum"), map(float, result)))


def _layer_quantiles(values: np.ndarray, land: np.ndarray) -> list[dict[str, float]]:
    return [_quantiles(layer[land]) for layer in np.asarray(values)]


def _class_layer_quantiles(
    values: np.ndarray, soil_type: np.ndarray, land: np.ndarray
) -> dict[str, list[dict[str, float]]]:
    result: dict[str, list[dict[str, float]]] = {}
    for soil_class in np.unique(soil_type[land]).astype(int):
        mask = land & (soil_type == soil_class)
        result[str(soil_class)] = [_quantiles(layer[mask]) for layer in values]
    return result


def _neighbor_jumps(
    values: np.ndarray, soil_type: np.ndarray, land: np.ndarray
) -> dict[str, dict[str, float]]:
    """Compare horizontal jumps across and within target soil-class boundaries."""
    across: list[np.ndarray] = []
    within: list[np.ndarray] = []
    for axis in (-2, -1):
        left = [slice(None)] * values.ndim
        right = [slice(None)] * values.ndim
        left[axis] = slice(None, -1)
        right[axis] = slice(1, None)
        delta = np.abs(values[tuple(left)] - values[tuple(right)])

        # Explicit slices are clearer here than broadcasting index machinery.
        if axis == -2:
            pair_land = land[:-1, :] & land[1:, :]
            pair_boundary = soil_type[:-1, :] != soil_type[1:, :]
        else:
            pair_land = land[:, :-1] & land[:, 1:]
            pair_boundary = soil_type[:, :-1] != soil_type[:, 1:]
        across.append(delta[:, pair_land & pair_boundary])
        within.append(delta[:, pair_land & ~pair_boundary])
    across_values = np.concatenate([item.ravel() for item in across])
    within_values = np.concatenate([item.ravel() for item in within])
    return {
        "across_soil_class": _quantiles(across_values),
        "within_soil_class": _quantiles(within_values),
    }


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".partial", dir=path.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_surface_case(
    source_path: Path,
    static_path: Path,
    products: Mapping[str, Path],
    *,
    noahmp_table: Path,
    report_path: Path | None = None,
) -> dict:
    """Validate one valid time without pretending plausibility selects a transfer policy."""
    if set(products) != {"smi", "relative_saturation", "absolute_w_so"}:
        raise ValueError("products must provide smi, relative_saturation and absolute_w_so")

    hydraulics = parse_noahmp_stas_hydraulics(noahmp_table)
    with netCDF4.Dataset(static_path) as static:
        land = np.asarray(static["landmask"][:], dtype=np.float64) >= 0.5
        landuse = np.asarray(static["landuse"][:], dtype=np.int64)
        glacier = land & (landuse == 24)
        active_soil = land & ~glacier
        if "soil_type_layer" in static.variables:
            target_soil = np.rint(np.asarray(static["soil_type_layer"][:])).astype(np.int64)
        else:
            target_soil = np.broadcast_to(
                np.rint(np.asarray(static["soil_type"][:])).astype(np.int64),
                (HICAR_SOIL_BOUNDS_M.size - 1, *land.shape),
            )
    target_land_3d = np.broadcast_to(land, target_soil.shape)
    target_active_soil_3d = np.broadcast_to(active_soil, target_soil.shape)
    if target_soil.shape != (HICAR_SOIL_BOUNDS_M.size - 1, *land.shape):
        raise ValueError("target soil classes do not match the four HICAR soil layers")
    if np.any((target_soil[target_land_3d] < 1) | (target_soil[target_land_3d] > 19)):
        raise ValueError("target land contains a Noah-MP soil class outside 1..19")
    target_index = target_soil - 1

    with netCDF4.Dataset(source_path) as source:
        source_w_so = _layer_cell(source, "W_SO")
        source_soil = _layer_cell(source, "SOILTYP")
        source_bounds = (
            np.asarray(source["w_so_bounds"][:], dtype=np.float64)
            if "w_so_bounds" in source.variables
            else None
        )
        if source_bounds is None:
            source_smi = icon_soil_water_to_smi(source_w_so, source_soil)
            source_relative = icon_soil_water_to_relative_saturation(source_w_so, source_soil)
        else:
            source_smi = icon_soil_water_to_smi(source_w_so, source_soil, source_bounds)
            source_relative = icon_soil_water_to_relative_saturation(
                source_w_so, source_soil, source_bounds
            )

    hard_failures: list[str] = []
    warnings: list[str] = []
    method_payload: dict[str, dict] = {}
    valid_times: set[str] = set()
    arrays: dict[str, np.ndarray] = {}
    common_state: dict[str, tuple[np.ndarray, ...]] = {}

    static_digest = sha256(static_path)
    source_digest = sha256(source_path)
    for method, path in products.items():
        with netCDF4.Dataset(path) as product:
            declared = str(getattr(product, "soil_water_method", ""))
            if declared != method:
                hard_failures.append(f"{method}: product declares soil_water_method={declared!r}")
            if str(getattr(product, "static_sha256", "")) != static_digest:
                hard_failures.append(f"{method}: static hash does not match validation target")
            if str(getattr(product, "source_surface_sha256", "")) != source_digest:
                hard_failures.append(f"{method}: source hash does not match validation source")
            valid_times.add(str(getattr(product, "valid_time", "")))
            vwc = np.asarray(product["soil_vwc"][:], dtype=np.float64)
            transfer = np.asarray(product["soil_water_transfer_index"][:], dtype=np.float64)
            temperature = np.asarray(product["soil_temperature"][:], dtype=np.float64)
            skin = np.asarray(product["skin_temperature"][:], dtype=np.float64)
            swe = np.asarray(product["snow_water_equivalent"][:], dtype=np.float64)
            density = np.asarray(product["snow_density"][:], dtype=np.float64)
            depth = np.asarray(product["snow_depth"][:], dtype=np.float64)
            source_topography = np.asarray(
                product["source_topography_on_target"][:], dtype=np.float64
            )
            terrain_difference = np.asarray(
                product["terrain_height_difference"][:], dtype=np.float64
            )
            dry_clips = int(getattr(product, "soil_water_dry_clip_count", -1))
            saturation_clips = int(getattr(product, "soil_water_saturation_clip_count", -1))
            same_surface_fallbacks = int(getattr(product, "same_surface_fallback_count", -1))
            global_fallbacks = int(getattr(product, "global_finite_fallback_count", -1))
            cross_surface_fallbacks = int(
                getattr(product, "cross_surface_in_stencil_fallback_count", -1)
            )
            water_snow_policy = str(getattr(product, "water_snow_policy", "unknown"))
            temperature_height_method = str(
                getattr(product, "temperature_height_method", "unknown")
            )
            maximum_fallback_distance_km = float(
                getattr(product, "maximum_fallback_distance_km", float("nan"))
            )
            fallback_distance_p99_km = float(
                getattr(product, "fallback_distance_p99_km", float("nan"))
            )
            static_epoch_back_extrapolation = str(
                getattr(product, "static_epoch_back_extrapolation", "unknown")
            )
            static_landuse_epoch_valid_from = str(
                getattr(product, "static_landuse_epoch_valid_from", "")
            )

        expected_shape = target_index.shape
        if vwc.shape != expected_shape or transfer.shape != expected_shape:
            hard_failures.append(
                f"{method}: soil arrays have shape {vwc.shape}, expected {expected_shape}"
            )
            continue
        arrays[method] = vwc
        common_state[method] = (temperature, skin, swe, density, depth)
        dry = hydraulics["DRYSMC"][target_index]
        maximum = hydraulics["MAXSMC"][target_index]
        if not np.isfinite(vwc[target_active_soil_3d]).all():
            hard_failures.append(f"{method}: non-finite active-soil VWC")
        if np.any(vwc[target_active_soil_3d] < dry[target_active_soil_3d] - 1.0e-10):
            hard_failures.append(f"{method}: VWC below target DRYSMC")
        if np.any(vwc[target_active_soil_3d] > maximum[target_active_soil_3d] + 1.0e-10):
            hard_failures.append(f"{method}: VWC above target MAXSMC")
        if not np.isfinite(transfer[target_active_soil_3d]).all():
            hard_failures.append(f"{method}: non-finite transfer index on active target soil")
        if np.any(vwc[:, glacier] != 0.0):
            hard_failures.append(f"{method}: glacier columns contain porous-soil water")
        if not np.isfinite(temperature).all() or np.any(
            (temperature < 180.0) | (temperature > 340.0)
        ):
            hard_failures.append(
                f"{method}: full-grid soil temperature is non-finite or outside 180..340 K"
            )
        if not np.isfinite(skin).all() or np.any((skin < 180.0) | (skin > 350.0)):
            hard_failures.append(f"{method}: skin temperature is non-finite or outside 180..350 K")
        if (
            np.any(~np.isfinite(swe))
            or np.any(swe < 0.0)
            or np.any(~np.isfinite(depth))
            or np.any(depth < 0.0)
        ):
            hard_failures.append(f"{method}: snow water/depth is non-finite or negative")
        snow = swe > 1.0e-9
        if np.any(snow & (~np.isfinite(density) | (density <= 0.0))):
            hard_failures.append(f"{method}: positive SWE has invalid density")
        if np.any(snow & (density > 917.0)):
            hard_failures.append(f"{method}: snow density exceeds pure-ice density")
        if np.any(swe > 10_000.0) or np.any(depth > 20.0):
            hard_failures.append(f"{method}: snow storage exceeds conservative physical limits")
        if water_snow_policy == "zero" and np.any(swe[~land] > 0.0):
            hard_failures.append(f"{method}: zero water-snow policy left snow off land")
        consistent_snow = snow & np.isfinite(density) & (density > 0.0)
        if np.any(consistent_snow) and not np.allclose(
            depth[consistent_snow],
            swe[consistent_snow] / density[consistent_snow],
            rtol=1.0e-8,
            atol=1.0e-10,
        ):
            hard_failures.append(f"{method}: snow depth is inconsistent with SWE/density")

        cell_layers = int(np.sum(target_active_soil_3d))
        clip_rate = (dry_clips + saturation_clips) / cell_layers
        if clip_rate > 0.05:
            warnings.append(f"{method}: {clip_rate:.1%} of target land layers hit hydraulic bounds")
        if not np.isfinite(maximum_fallback_distance_km):
            hard_failures.append(f"{method}: fallback distance diagnostics are missing")
        elif maximum_fallback_distance_km > 20.0:
            hard_failures.append(f"{method}: fallback donor exceeds 20 km")
        elif maximum_fallback_distance_km > 5.0:
            warnings.append(f"{method}: fallback donor exceeds 5 km")
        if static_epoch_back_extrapolation == "explicit_research_override":
            warnings.append(
                f"{method}: research validation back-extrapolates static landuse epoch "
                f"{static_landuse_epoch_valid_from}"
            )
        elif static_epoch_back_extrapolation not in {"none", "unknown"}:
            hard_failures.append(
                f"{method}: unknown static_epoch_back_extrapolation policy "
                f"{static_epoch_back_extrapolation!r}"
            )
        skin_soil_jump = np.abs(temperature[0] - skin)
        if np.any(skin_soil_jump[land] > 50.0):
            hard_failures.append(f"{method}: top-soil/skin temperature jump exceeds 50 K")
        elif np.quantile(skin_soil_jump[land], 0.99) > 15.0:
            warnings.append(f"{method}: p99 top-soil/skin temperature jump exceeds 15 K")
        finite_terrain_diagnostics = np.isfinite(source_topography).all() and np.isfinite(
            terrain_difference
        ).all()
        missing_terrain_diagnostics = np.isnan(source_topography).all() and np.isnan(
            terrain_difference
        ).all()
        if temperature_height_method == "int2lm_climatological":
            if not finite_terrain_diagnostics:
                hard_failures.append(
                    f"{method}: height-corrected temperatures require finite terrain diagnostics"
                )
        elif temperature_height_method == "none":
            if not (finite_terrain_diagnostics or missing_terrain_diagnostics):
                hard_failures.append(
                    f"{method}: no-correction terrain diagnostics are only partly defined"
                )
        else:
            hard_failures.append(
                f"{method}: unknown temperature_height_method={temperature_height_method!r}"
            )
        target_smi = np.divide(
            vwc - hydraulics["WLTSMC"][target_index],
            hydraulics["REFSMC"][target_index] - hydraulics["WLTSMC"][target_index],
        )
        target_relative = np.divide(vwc, hydraulics["MAXSMC"][target_index])
        thickness = np.diff(HICAR_SOIL_BOUNDS_M)[:, None, None]
        column_water = np.sum(vwc * thickness * 1000.0, axis=0)
        method_payload[method] = {
            "soil_vwc_by_layer": _layer_quantiles(vwc, active_soil),
            "target_smi_by_layer": _layer_quantiles(target_smi, active_soil),
            "target_relative_saturation_by_layer": _layer_quantiles(
                target_relative, active_soil
            ),
            "transfer_index_by_layer": _layer_quantiles(transfer, active_soil),
            "soil_vwc_by_target_class": {
                str(layer + 1): _class_layer_quantiles(
                    vwc[layer : layer + 1], target_soil[layer], active_soil
                )
                for layer in range(vwc.shape[0])
            },
            "soil_vwc_neighbor_jumps": {
                str(layer + 1): _neighbor_jumps(
                    vwc[layer : layer + 1], target_soil[layer], active_soil
                )
                for layer in range(vwc.shape[0])
            },
            "transfer_index_neighbor_jumps": {
                str(layer + 1): _neighbor_jumps(
                    transfer[layer : layer + 1], target_soil[layer], active_soil
                )
                for layer in range(transfer.shape[0])
            },
            "column_water_kg_m2": _quantiles(column_water[active_soil]),
            "soil_temperature_k": _quantiles(temperature[:, land]),
            "skin_temperature_k": _quantiles(skin),
            "snow_water_equivalent_kg_m2": _quantiles(swe),
            "snow_depth_m": _quantiles(depth),
            "dry_clip_count": dry_clips,
            "saturation_clip_count": saturation_clips,
            "hydraulic_clip_rate": clip_rate,
            "same_surface_fallback_count": same_surface_fallbacks,
            "global_finite_fallback_count": global_fallbacks,
            "cross_surface_in_stencil_fallback_count": cross_surface_fallbacks,
            "maximum_fallback_distance_km": maximum_fallback_distance_km,
            "fallback_distance_p99_km": fallback_distance_p99_km,
            "glacier_cell_count": int(np.sum(glacier)),
            "offland_snow_cell_count": int(np.sum((~land) & (swe > 1.0e-9))),
            "water_snow_policy": water_snow_policy,
            "temperature_height_method": temperature_height_method,
            "static_epoch_back_extrapolation": static_epoch_back_extrapolation,
            "static_landuse_epoch_valid_from": static_landuse_epoch_valid_from,
            "terrain_height_difference_m": _quantiles(terrain_difference),
            "top_soil_minus_skin_absolute_k": _quantiles(skin_soil_jump[land]),
            "product": str(path.resolve()),
            "product_sha256": sha256(path),
        }

    if len(valid_times) != 1 or "" in valid_times:
        hard_failures.append(
            f"products do not share one non-empty valid_time: {sorted(valid_times)}"
        )

    if common_state:
        reference_method = sorted(common_state)[0]
        reference = common_state[reference_method]
        for method, state in common_state.items():
            if method == reference_method:
                continue
            for label, left, right in zip(
                ("soil_temperature", "skin_temperature", "SWE", "snow_density", "snow_depth"),
                reference,
                state,
            ):
                if not np.allclose(left, right, equal_nan=True):
                    hard_failures.append(
                        f"{method}: {label} differs from {reference_method}; only soil-water "
                        "transfer should vary across method controls"
                    )

    pairwise: dict[str, dict[str, float]] = {}
    names = sorted(arrays)
    for left_index, left in enumerate(names):
        for right in names[left_index + 1 :]:
            delta = arrays[left] - arrays[right]
            selected = delta[target_active_soil_3d]
            pairwise[f"{left}_minus_{right}"] = {
                "mean_m3_m3": float(np.mean(selected)),
                "rmse_m3_m3": float(np.sqrt(np.mean(selected**2))),
                "absolute_difference_p95_m3_m3": float(np.quantile(np.abs(selected), 0.95)),
                "maximum_absolute_difference_m3_m3": float(np.max(np.abs(selected))),
            }

    payload = {
        "schema": "hicarprep-surface-validation-v1",
        "status": "PASS_INPUT_PLAUSIBILITY" if not hard_failures else "FAIL_INPUT_PLAUSIBILITY",
        "policy_decision": "NOT_DETERMINED_BY_PLAUSIBILITY_TESTS",
        "assessment_scope": "NUMERICAL_AND_RANGE_PLAUSIBILITY_ONLY",
        "valid_time": next(iter(valid_times)) if len(valid_times) == 1 else None,
        "source": str(source_path.resolve()),
        "source_sha256": source_digest,
        "static": str(static_path.resolve()),
        "static_sha256": static_digest,
        "native_icon_indices": {
            "smi_by_layer": [_quantiles(layer) for layer in source_smi],
            "relative_saturation_by_layer": [_quantiles(layer) for layer in source_relative],
        },
        "methods": method_payload,
        "pairwise_vwc": pairwise,
        "hard_failures": hard_failures,
        "warnings": warnings,
        "interpretation_limit": (
            "Passing establishes numerical and physical-range plausibility only. Selection between "
            "SMI and relative saturation requires model-response and/or observation-based evidence."
        ),
    }
    if report_path is not None:
        _write_json_atomic(report_path, payload)
    return payload
