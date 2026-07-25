from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "validation"
    / "build_rea_l_land_initialization.py"
)
SPEC = importlib.util.spec_from_file_location("land_initialization", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_soil_temperature_is_interpolated_at_hicar_midpoints():
    depths = MODULE.REA_L_T_SO_DEPTHS_M
    source = (270.0 + 2.0 * depths)[:, None, None]
    result = MODULE.remap_soil_temperature(source)
    expected = 270.0 + 2.0 * MODULE.HICAR_SOIL_MIDPOINTS_M
    np.testing.assert_allclose(result[:, 0, 0], expected)


def test_layer_water_remap_conserves_represented_column():
    bounds = MODULE.REA_L_W_SO_BOUNDS_M
    uniform_vwc = 0.25
    source_mass = (
        uniform_vwc * MODULE.WATER_DENSITY_KG_M3 * np.diff(bounds)
    )[:, None, None]
    target_vwc, target_mass = MODULE.remap_layer_integrated_soil_water(source_mass)
    np.testing.assert_allclose(target_vwc[:, 0, 0], uniform_vwc)
    np.testing.assert_allclose(
        np.sum(target_mass[:, 0, 0]),
        uniform_vwc
        * MODULE.WATER_DENSITY_KG_M3
        * MODULE.HICAR_SOIL_BOUNDS_M[-1],
    )


def test_layer_water_remap_uses_exact_partial_overlaps():
    source_mass = np.zeros((8, 1, 1))
    source_mass[4, 0, 0] = 54.0
    target_vwc, target_mass = MODULE.remap_layer_integrated_soil_water(source_mass)
    # Source layer 0.27--0.81 m has uniform 0.1 VWC. The target overlaps are
    # 0.03 m in layer 0.1--0.3, 0.4 m in 0.3--0.7, and 0.11 m in 0.7--1.5.
    np.testing.assert_allclose(target_mass[:, 0, 0], [0.0, 3.0, 40.0, 11.0])
    np.testing.assert_allclose(target_vwc[:, 0, 0], [0.0, 0.015, 0.1, 0.01375])


def test_snow_height_fails_for_positive_swe_without_density():
    with pytest.raises(ValueError, match="RHO_SNOW"):
        MODULE.derive_snow_height(np.array([[100.0]]), np.array([[np.nan]]))


def test_snow_height_optional_fallback_is_explicit():
    result = MODULE.derive_snow_height(
        np.array([[100.0, 0.0]]),
        np.array([[np.nan, np.nan]]),
        fallback_density_kg_m3=250.0,
    )
    np.testing.assert_allclose(result, [[0.4, 0.0]])


def test_bilinear_regrid_handles_descending_latitude():
    latitude = np.array([2.0, 1.0, 0.0])
    longitude = np.array([0.0, 1.0, 2.0])
    lon_2d, lat_2d = np.meshgrid(longitude, latitude)
    values = 2.0 * lat_2d + 3.0 * lon_2d
    target_latitude = np.array([[0.5, 1.5]])
    target_longitude = np.array([[0.5, 1.5]])
    result = MODULE.bilinear_regrid(
        values, latitude, longitude, target_latitude, target_longitude
    )
    np.testing.assert_allclose(result, 2.0 * target_latitude + 3.0 * target_longitude)
