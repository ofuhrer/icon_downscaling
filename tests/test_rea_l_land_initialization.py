from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "case_studies" / "swiss_200m" / "validation" / "build_rea_l_land_initialization.py"
SPEC = importlib.util.spec_from_file_location("land_initialization", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)
NOAHMP_TABLE = ROOT / "HICAR" / "run" / "NoahmpTable.TBL"


def test_soil_temperature_is_interpolated_at_hicar_midpoints():
    depths = MODULE.REA_L_T_SO_DEPTHS_M
    source = (270.0 + 2.0 * depths)[:, None, None]
    result = MODULE.remap_soil_temperature(source)
    expected = 270.0 + 2.0 * MODULE.HICAR_SOIL_MIDPOINTS_M
    np.testing.assert_allclose(result[:, 0, 0], expected)


def test_layer_water_remap_conserves_represented_column():
    bounds = MODULE.REA_L_W_SO_BOUNDS_M
    uniform_vwc = 0.25
    source_mass = (uniform_vwc * MODULE.WATER_DENSITY_KG_M3 * np.diff(bounds))[:, None, None]
    target_vwc, target_mass = MODULE.remap_layer_integrated_soil_water(source_mass)
    np.testing.assert_allclose(target_vwc[:, 0, 0], uniform_vwc)
    np.testing.assert_allclose(
        np.sum(target_mass[:, 0, 0]),
        uniform_vwc * MODULE.WATER_DENSITY_KG_M3 * MODULE.HICAR_SOIL_BOUNDS_M[-1],
    )


def test_layer_water_remap_uses_exact_partial_overlaps():
    source_mass = np.zeros((8, 1, 1))
    source_mass[4, 0, 0] = 54.0
    target_vwc, target_mass = MODULE.remap_layer_integrated_soil_water(source_mass)
    # Source layer 0.27--0.81 m has uniform 0.1 VWC. The target overlaps are
    # 0.03 m in layer 0.1--0.3, 0.4 m in 0.3--0.7, and 0.11 m in 0.7--1.5.
    np.testing.assert_allclose(target_mass[:, 0, 0], [0.0, 3.0, 40.0, 11.0])
    np.testing.assert_allclose(target_vwc[:, 0, 0], [0.0, 0.015, 0.1, 0.01375])


def test_icon_soil_water_to_smi_uses_terra_soil_class_hydraulics():
    bounds = MODULE.REA_L_W_SO_BOUNDS_M
    thickness = np.diff(bounds)
    soil_type = np.array([[3, 7]])  # sand and clay
    wilting = MODULE.ICON_TERRA_WILTING_POINT[soil_type - 1]
    field_capacity = MODULE.ICON_TERRA_FIELD_CAPACITY[soil_type - 1]
    expected_smi = 0.4
    vwc = wilting + expected_smi * (field_capacity - wilting)
    mass = vwc[np.newaxis, ...] * (
        MODULE.WATER_DENSITY_KG_M3 * thickness[:, np.newaxis, np.newaxis]
    )
    result = MODULE.icon_soil_water_to_smi(mass, soil_type)
    np.testing.assert_allclose(result, expected_smi)


def test_icon_soil_water_to_smi_masks_sea_and_keeps_rock_dry():
    thickness = np.diff(MODULE.REA_L_W_SO_BOUNDS_M)
    mass = np.zeros((8, 1, 2))
    mass[:, 0, :] = 0.2 * MODULE.WATER_DENSITY_KG_M3 * thickness[:, None]
    result = MODULE.icon_soil_water_to_smi(mass, np.array([[2, 9]]))
    np.testing.assert_allclose(result[:, 0, 0], 0.0)
    assert np.all(np.isnan(result[:, 0, 1]))


def test_smi_vertical_remap_overlap_averages_without_clipping():
    source = np.arange(8.0)[:, None, None]
    result = MODULE.remap_layer_mean(source)
    # 0--0.1 m: 0.01*0 + 0.02*1 + 0.06*2 + 0.01*3.
    np.testing.assert_allclose(result[0, 0, 0], 1.7)
    assert result[-1, 0, 0] > 1.0


def test_noahmp_smi_to_vwc_uses_production_stas_table():
    hydraulics = MODULE.parse_noahmp_stas_hydraulics(NOAHMP_TABLE)
    soil_type = np.array([[1, 12]])  # sand and clay
    smi = np.stack([np.zeros_like(soil_type), np.ones_like(soil_type)])
    result = MODULE.noahmp_smi_to_vwc(smi, soil_type, hydraulics)
    np.testing.assert_allclose(result[0], hydraulics["WLTSMC"][soil_type - 1])
    np.testing.assert_allclose(result[1], hydraulics["REFSMC"][soil_type - 1])


def test_noahmp_smi_to_vwc_clips_only_at_physical_dryness_and_saturation():
    hydraulics = MODULE.parse_noahmp_stas_hydraulics(NOAHMP_TABLE)
    soil_type = np.array([[6]])
    result = MODULE.noahmp_smi_to_vwc(np.array([[[-10.0]], [[10.0]]]), soil_type, hydraulics)
    np.testing.assert_allclose(
        result[:, 0, 0],
        [
            hydraulics["DRYSMC"][5],
            hydraulics["MAXSMC"][5],
        ],
    )


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
    result = MODULE.bilinear_regrid(values, latitude, longitude, target_latitude, target_longitude)
    np.testing.assert_allclose(result, 2.0 * target_latitude + 3.0 * target_longitude)


def test_same_curvilinear_grid_bypasses_rectilinear_interpolation():
    latitude = np.array([[46.0, 46.01], [46.02, 46.03]])
    longitude = np.array([[7.0, 7.02], [7.01, 7.03]])
    assert MODULE.coordinate_grid_shape(latitude, longitude) == (2, 2)
    assert MODULE.same_coordinate_grid(latitude, longitude, latitude.copy(), longitude.copy())
    with pytest.raises(ValueError, match="rectilinear"):
        MODULE.regular_grid_axes(latitude, longitude)


def test_nearest_regrid_preserves_categories():
    result = MODULE.nearest_regrid(
        np.array([[3, 4], [5, 6]]),
        np.array([0.0, 1.0]),
        np.array([0.0, 1.0]),
        np.array([[0.1, 0.9]]),
        np.array([[0.9, 0.1]]),
    )
    np.testing.assert_array_equal(result, [[4, 5]])


def test_surface_temperature_height_correction_uses_stated_lapse_rate():
    result = MODULE.elevation_correct_temperature(
        np.array([[280.0]]),
        np.array([[500.0]]),
        np.array([[1500.0]]),
        -0.0065,
    )
    np.testing.assert_allclose(result, [[273.5]])
