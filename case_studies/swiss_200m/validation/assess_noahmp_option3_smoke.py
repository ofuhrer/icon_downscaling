#!/usr/bin/env python3
"""Assess whether a GPU smoke restart contains active Noah-MP option-3 results."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import netCDF4
import numpy as np


GUARD_CELLS = 3
SNOW_FREE_LIMIT_M = 1.0e-6
VEGETATION_FRACTION_LIMIT = 0.05
ACTIVE_CONDUCTANCE_LIMIT = 1.0e-4
GAS_CONSTANT_DRY_AIR = 287.04
HEAT_CAPACITY_DRY_AIR = 1004.64

REQUIRED_ATTRIBUTES = {
    "physics.lsm": "noahmp",
    "lsm.nmp_opt_sfc": "3",
    "sfc.iz0tlnd": "1",
}

SURFACE_RANGES = {
    "coeff_momentum_drag": (0.0, 1.0),
    "coeff_heat_exchange": (0.0, 1.0),
    "ch_veg": (0.0, 1.0),
    "ch_veg_2m": (0.0, 1.0),
    "ch_bare": (0.0, 1.0),
    "ch_bare_2m": (0.0, 1.0),
    "temperature_2m_veg": (180.0, 340.0),
    "temperature_2m_bare": (180.0, 340.0),
    "mixing_ratio_2m_veg": (0.0, 0.1),
    "mixing_ratio_2m_bare": (0.0, 0.1),
    "hpbl": (0.0, 20_000.0),
}

BRANCH_FIELDS = {
    "vegetated": (
        "ch_veg",
        "ch_veg_2m",
        "temperature_2m_veg",
        "mixing_ratio_2m_veg",
    ),
    "bare": (
        "ch_bare",
        "ch_bare_2m",
        "temperature_2m_bare",
        "mixing_ratio_2m_bare",
    ),
}

IDENTITY_FIELDS = (
    "canopy_temperature",
    "ground_temperature_bare",
    "sensible_heat_veg",
    "sensible_heat_canopy",
    "sensible_heat_bare",
)


class AssessmentError(ValueError):
    """The restart does not provide credible evidence of option-3 execution."""


def _values(variable: netCDF4.Variable) -> np.ndarray:
    return np.ma.asarray(variable[:]).filled(np.nan).astype(np.float64, copy=False)


def _without_last_time(variable: netCDF4.Variable) -> tuple[np.ndarray, list[str]]:
    values = _values(variable)
    dimensions = list(variable.dimensions)
    if "time" in dimensions:
        axis = dimensions.index("time")
        values = np.take(values, -1, axis=axis)
        dimensions.pop(axis)
    return values, dimensions


def _surface_field(
    dataset: netCDF4.Dataset,
    name: str,
    horizontal_dimensions: tuple[str, str] | None = None,
) -> tuple[np.ndarray, tuple[str, str]]:
    if name not in dataset.variables:
        raise AssessmentError(f"missing required variable: {name}")
    values, dimensions = _without_last_time(dataset[name])
    if values.ndim != 2:
        raise AssessmentError(
            f"{name}: expected a 2-D surface field after selecting the last time, "
            f"got dimensions {dimensions}"
        )
    field_dimensions = tuple(dimensions)
    if horizontal_dimensions is None:
        return values, field_dimensions
    if set(field_dimensions) != set(horizontal_dimensions):
        raise AssessmentError(
            f"{name}: horizontal dimensions {field_dimensions} do not match "
            f"{horizontal_dimensions}"
        )
    if field_dimensions != horizontal_dimensions:
        values = np.transpose(
            values, tuple(field_dimensions.index(name) for name in horizontal_dimensions)
        )
    return values, horizontal_dimensions


def _level_field(
    dataset: netCDF4.Dataset,
    name: str,
    level: int,
    horizontal_dimensions: tuple[str, str],
) -> np.ndarray:
    if name not in dataset.variables:
        raise AssessmentError(f"missing required variable: {name}")
    values, dimensions = _without_last_time(dataset[name])
    vertical_dimensions = [name for name in dimensions if name not in horizontal_dimensions]
    if len(vertical_dimensions) != 1 or values.ndim != 3:
        raise AssessmentError(
            f"{name}: expected one vertical and two horizontal dimensions, got {dimensions}"
        )
    vertical_axis = dimensions.index(vertical_dimensions[0])
    if values.shape[vertical_axis] <= level:
        raise AssessmentError(f"{name}: vertical level {level} is unavailable")
    values = np.take(values, level, axis=vertical_axis)
    dimensions.pop(vertical_axis)
    if set(dimensions) != set(horizontal_dimensions):
        raise AssessmentError(
            f"{name}: horizontal dimensions {tuple(dimensions)} do not match "
            f"{horizontal_dimensions}"
        )
    if tuple(dimensions) != horizontal_dimensions:
        values = np.transpose(
            values, tuple(dimensions.index(name) for name in horizontal_dimensions)
        )
    return values


def _core(values: np.ndarray) -> np.ndarray:
    if values.ndim != 2 or min(values.shape) <= 2 * GUARD_CELLS:
        raise AssessmentError(
            f"horizontal field {values.shape} is too small for a {GUARD_CELLS}-cell guard"
        )
    return values[GUARD_CELLS:-GUARD_CELLS, GUARD_CELLS:-GUARD_CELLS]


def _attribute_text(dataset: netCDF4.Dataset, name: str) -> str:
    value = getattr(dataset, name, "")
    if isinstance(value, bytes):
        value = value.decode()
    return str(value).strip()


def _require_sample_count(name: str, mask: np.ndarray, minimum_samples: int) -> int:
    count = int(np.count_nonzero(mask))
    if count < minimum_samples:
        raise AssessmentError(
            f"{name}: only {count} applicable cells; require at least {minimum_samples}"
        )
    return count


def _require_range_and_variation(
    name: str,
    values: np.ndarray,
    mask: np.ndarray,
    lower: float,
    upper: float,
) -> dict[str, float]:
    selected = values[mask]
    if selected.size == 0:
        raise AssessmentError(f"{name}: no applicable cells")
    if not np.isfinite(selected).all():
        raise AssessmentError(
            f"{name}: {int(selected.size - np.isfinite(selected).sum())} non-finite values"
        )
    minimum = float(selected.min())
    maximum = float(selected.max())
    if minimum <= lower or maximum > upper:
        raise AssessmentError(
            f"{name}: range [{minimum:.7g}, {maximum:.7g}] outside ({lower}, {upper}]"
        )
    spread = maximum - minimum
    variation_limit = max(1.0e-12, 1.0e-7 * max(abs(minimum), abs(maximum)))
    if spread <= variation_limit:
        raise AssessmentError(
            f"{name}: field is numerically constant over applicable cells "
            f"(range [{minimum:.7g}, {maximum:.7g}])"
        )
    return {"min": minimum, "max": maximum, "spread": spread}


def _identity_summary(
    name: str,
    actual: np.ndarray,
    expected: np.ndarray,
    mask: np.ndarray,
    tolerance: float,
    minimum_samples: int,
) -> dict[str, float | int]:
    count = _require_sample_count(name, mask, minimum_samples)
    residual = np.abs(actual[mask] - expected[mask])
    if not np.isfinite(residual).all():
        raise AssessmentError(f"{name}: identity residual contains non-finite values")
    maximum = float(residual.max())
    percentile_99 = float(np.percentile(residual, 99.0))
    if maximum > tolerance:
        raise AssessmentError(
            f"{name}: maximum residual {maximum:.7g} K exceeds {tolerance:.7g} K "
            f"over {count} cells"
        )
    return {
        "samples": count,
        "max_abs_residual_K": maximum,
        "p99_abs_residual_K": percentile_99,
        "tolerance_K": tolerance,
    }


def assess_option3_smoke(
    restart_path: Path,
    static_path: Path,
    *,
    identity_tolerance: float = 1.0e-3,
    minimum_samples: int = 100,
) -> dict[str, Any]:
    """Return a compact option-3 acceptance summary or raise ``AssessmentError``."""
    if identity_tolerance <= 0.0 or minimum_samples <= 0:
        raise AssessmentError("identity tolerance and minimum sample count must be positive")

    with netCDF4.Dataset(static_path) as static, netCDF4.Dataset(restart_path) as restart:
        mismatches = {
            name: {"actual": _attribute_text(restart, name), "expected": expected}
            for name, expected in REQUIRED_ATTRIBUTES.items()
            if _attribute_text(restart, name) != expected
        }
        if mismatches:
            raise AssessmentError(
                "restart option-3 attribute mismatch: " + json.dumps(mismatches, sort_keys=True)
            )

        land_mask, horizontal_dimensions = _surface_field(static, "landmask")
        vegetation_type, _ = _surface_field(static, "landuse", horizontal_dimensions)
        try:
            ice_category = int(_attribute_text(restart, "lsm.ice_category"))
        except ValueError as error:
            raise AssessmentError("restart is missing a valid lsm.ice_category") from error

        surface: dict[str, np.ndarray] = {}
        for name in (*SURFACE_RANGES, "vegetation_fraction_out", "snow_height", *IDENTITY_FIELDS):
            surface[name], _ = _surface_field(restart, name, horizontal_dimensions)

        land_mask = _core(land_mask)
        vegetation_type = _core(vegetation_type)
        surface = {name: _core(values) for name, values in surface.items()}

        shape = land_mask.shape
        if any(values.shape != shape for values in (vegetation_type, *surface.values())):
            raise AssessmentError("restart and static horizontal shapes differ after guard removal")

        snow_height = surface["snow_height"]
        vegetation_fraction = surface["vegetation_fraction_out"]
        mask_inputs_finite = (
            np.isfinite(land_mask)
            & np.isfinite(vegetation_type)
            & np.isfinite(snow_height)
            & np.isfinite(vegetation_fraction)
        )
        if not mask_inputs_finite.all():
            raise AssessmentError("land/glacier/snow/vegetation mask inputs contain non-finite values")
        if np.any((land_mask < 0.0) | (land_mask > 1.0)):
            raise AssessmentError("static landmask is outside [0, 1]")
        if np.any((vegetation_fraction < 0.0) | (vegetation_fraction > 1.0)):
            raise AssessmentError("vegetation_fraction_out is outside [0, 1]")

        eligible = (
            (land_mask >= 0.5)
            & (vegetation_type != ice_category)
            & (snow_height <= SNOW_FREE_LIMIT_M)
        )
        eligible_count = _require_sample_count("eligible snow-free land", eligible, minimum_samples)
        branch_masks = {
            "vegetated": eligible & (vegetation_fraction > VEGETATION_FRACTION_LIMIT),
            "bare": eligible & ((1.0 - vegetation_fraction) > VEGETATION_FRACTION_LIMIT),
        }
        branch_counts = {
            name: _require_sample_count(name, mask, minimum_samples)
            for name, mask in branch_masks.items()
        }

        ranges: dict[str, dict[str, float]] = {}
        aggregate_fields = ("coeff_momentum_drag", "coeff_heat_exchange", "hpbl")
        for name in aggregate_fields:
            ranges[name] = _require_range_and_variation(
                name, surface[name], eligible, *SURFACE_RANGES[name]
            )
        for branch, names in BRANCH_FIELDS.items():
            for name in names:
                ranges[name] = _require_range_and_variation(
                    name, surface[name], branch_masks[branch], *SURFACE_RANGES[name]
                )

        temperature = _core(_level_field(restart, "temperature", 0, horizontal_dimensions))
        mixing_ratio = _core(_level_field(restart, "qv", 0, horizontal_dimensions))
        pressure_lower = _core(_level_field(restart, "pressure", 0, horizontal_dimensions))
        pressure_upper = _core(_level_field(restart, "pressure", 1, horizontal_dimensions))
        atmospheric = (temperature, mixing_ratio, pressure_lower, pressure_upper)
        if any(values.shape != shape for values in atmospheric):
            raise AssessmentError("atmospheric and surface restart shapes differ")
        if not all(np.isfinite(values[eligible]).all() for values in atmospheric):
            raise AssessmentError("option-3 atmospheric inputs contain non-finite values")

        pressure = 0.5 * (pressure_lower + pressure_upper)
        specific_humidity = mixing_ratio / (1.0 + mixing_ratio)
        vapor_pressure = specific_humidity * pressure / (
            0.622 + 0.378 * specific_humidity
        )
        density = (pressure - 0.378 * vapor_pressure) / (
            GAS_CONSTANT_DRY_AIR * temperature
        )
        if np.any((density[eligible] <= 0.0) | (density[eligible] > 2.0)):
            raise AssessmentError("reconstructed Noah-MP reference density is outside (0, 2] kg m-3")

        with np.errstate(divide="ignore", invalid="ignore"):
            expected_bare = surface["ground_temperature_bare"] - surface[
                "sensible_heat_bare"
            ] / (density * HEAT_CAPACITY_DRY_AIR * surface["ch_bare_2m"])
            expected_vegetated = surface["canopy_temperature"] - (
                surface["sensible_heat_veg"] + surface["sensible_heat_canopy"]
            ) / (density * HEAT_CAPACITY_DRY_AIR * surface["ch_veg_2m"])

        active_masks = {
            "bare": branch_masks["bare"]
            & (surface["ch_bare_2m"] >= ACTIVE_CONDUCTANCE_LIMIT),
            "vegetated": branch_masks["vegetated"]
            & (surface["ch_veg_2m"] >= ACTIVE_CONDUCTANCE_LIMIT),
        }
        identities = {
            "bare": _identity_summary(
                "bare option-3 T2 identity",
                surface["temperature_2m_bare"],
                expected_bare,
                active_masks["bare"],
                identity_tolerance,
                minimum_samples,
            ),
            "vegetated": _identity_summary(
                "vegetated option-3 T2 identity",
                surface["temperature_2m_veg"],
                expected_vegetated,
                active_masks["vegetated"],
                identity_tolerance,
                minimum_samples,
            ),
        }

    return {
        "status": "accepted",
        "restart": str(restart_path),
        "static": str(static_path),
        "guard_cells_removed": GUARD_CELLS,
        "ice_category_excluded": ice_category,
        "eligible_snow_free_land_cells": eligible_count,
        "applicable_cells": branch_counts,
        "ranges": ranges,
        "temperature_identities": identities,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restart", type=Path, required=True)
    parser.add_argument("--static", type=Path, required=True)
    parser.add_argument("--identity-tolerance", type=float, default=1.0e-3)
    parser.add_argument("--minimum-samples", type=int, default=100)
    args = parser.parse_args()

    try:
        summary = assess_option3_smoke(
            args.restart,
            args.static,
            identity_tolerance=args.identity_tolerance,
            minimum_samples=args.minimum_samples,
        )
    except (AssessmentError, OSError) as error:
        raise SystemExit(str(error)) from error
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
