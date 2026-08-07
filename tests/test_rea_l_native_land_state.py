from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "case_studies" / "swiss_200m" / "validation"
SCRIPT = VALIDATION / "remap_rea_l_native_land_state.py"
sys.path.insert(0, str(VALIDATION))
SPEC = importlib.util.spec_from_file_location("native_land_state", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

PACKAGE_SCRIPT = VALIDATION / "package_rea_l_native_surface.py"
PACKAGE_SPEC = importlib.util.spec_from_file_location("native_surface_package", PACKAGE_SCRIPT)
PACKAGE_MODULE = importlib.util.module_from_spec(PACKAGE_SPEC)
assert PACKAGE_SPEC.loader is not None
PACKAGE_SPEC.loader.exec_module(PACKAGE_MODULE)


def test_chunk_slices_cover_rows_exactly_once():
    result = MODULE.chunk_slices(7, 3)
    assert [(item.start, item.stop) for item in result] == [(0, 3), (3, 6), (6, 7)]


class FakeTargetGrid:
    def __init__(self, definition):
        self.definition = definition
        self.spec = {
            "type": "unstructured_ll",
            "uid": f"target-{len(definition['latitudes'])}",
        }


def test_chunked_regrid_preserves_field_and_target_order(capsys):
    calls = []
    grids = []

    def fake_grid_factory(definition):
        grid = FakeTargetGrid(definition)
        grids.append(grid)
        return grid

    def fake_regrid(values, *, in_grid, out_grid, interpolation, backend):
        assert values.dtype == np.float64
        calls.append(
            (
                len(out_grid.definition["latitudes"]),
                interpolation,
                backend,
                in_grid,
                out_grid.spec,
            )
        )
        return np.full(len(out_grid.definition["latitudes"]), np.mean(values)), {"uid": "test"}

    latitude = np.arange(12.0).reshape(4, 3)
    longitude = latitude + 100.0
    target_chunks = MODULE.build_target_chunks(latitude, longitude, 2, fake_grid_factory)
    result = MODULE.chunked_regrid(
        np.array([[1, 1], [2, 2]], dtype=np.int16),
        {"grid": "ICON-CH1_C"},
        latitude,
        longitude,
        "linear",
        fake_regrid,
        target_chunks,
        "T_SO",
    )
    assert result.shape == (2, 4, 3)
    np.testing.assert_allclose(result[0], 1.0)
    np.testing.assert_allclose(result[1], 2.0)
    assert [call[0] for call in calls] == [6, 6, 6, 6]
    assert all(call[1:3] == ("linear", "mir") for call in calls)
    assert all(grid.definition["type"] == "unstructured" for grid in grids)
    assert all("latitudes" not in call[4] for call in calls)
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [item["event"] for item in events].count("target_chunk_ready") == 2
    assert [item["event"] for item in events].count("mir_regrid_start") == 4
    assert [item["event"] for item in events].count("mir_regrid_complete") == 4
    assert all(
        item.get("label") == "T_SO" for item in events if item["event"].startswith("mir_regrid")
    )


def test_runtime_caches_include_eckit_geometry(tmp_path, monkeypatch):
    monkeypatch.delenv("MIR_CACHE_PATH", raising=False)
    monkeypatch.delenv("ECKIT_GEO_CACHE_PATH", raising=False)
    root = tmp_path / "cache"
    geometry = MODULE.configure_runtime_caches(root)
    assert geometry == root / "eckit_geo"
    assert geometry.is_dir()
    assert os.environ["MIR_CACHE_PATH"] == str(root.resolve())
    assert os.environ["ECKIT_GEO_CACHE_PATH"] == str(geometry.resolve())


def test_supported_value_normalization_excludes_missing_source_weight():
    weighted = np.array([[0.5, 0.0, 0.8]])
    support = np.array([[0.25, 0.0, 1.0]])
    result = MODULE.normalized_supported_values(weighted, support)
    np.testing.assert_allclose(result[0, [0, 2]], [2.0, 0.8])
    assert np.isnan(result[0, 1])


def test_chunked_supported_regrid_excludes_masked_source_cells():
    def fake_regrid(values, *, in_grid, out_grid, interpolation, backend):
        assert in_grid == {"grid": "native"}
        assert interpolation == "linear"
        assert backend == "mir"
        return np.array([np.mean(values)])

    latitude = np.array([[46.0]])
    longitude = np.array([[8.0]])
    chunks = [(slice(0, 1), FakeTargetGrid({"latitudes": [46.0]}), "target-1")]
    result, support = MODULE.chunked_regrid_with_normalized_support(
        np.array([[10.0, 999.0]]),
        {"grid": "native"},
        latitude,
        longitude,
        "linear",
        fake_regrid,
        chunks,
        "W_SO",
        source_mask=np.array([True, False]),
    )
    np.testing.assert_allclose(support, [[[0.5]]])
    np.testing.assert_allclose(result, [[[10.0]]])


class FakeField:
    def __init__(self, metadata):
        self.values = metadata

    def metadata(self, key):
        if key not in self.values:
            raise KeyError(key)
        return self.values[key]


def grib_field(
    name: str,
    units: str,
    *,
    level_type: str = "surface",
    validity_time: int = 0,
    step_type: str = "instant",
):
    return FakeField(
        {
            "shortName": name,
            "units": units,
            "validityDate": 20200115,
            "validityTime": validity_time,
            "stepType": step_type,
            "step": 0,
            "stepRange": "0",
            "typeOfLevel": level_type,
        }
    )


def valid_native_surface_inventory():
    surface = [
        grib_field("SKT", "K"),
        grib_field("W_SNOW", "kg m**-2"),
        grib_field("RHO_SNOW", "kg m-3"),
    ]
    t_so = [grib_field("T_SO", "K", level_type="depthBelowLandLayer") for _ in range(8)]
    w_so = [
        grib_field("W_SO", "kg m-2", level_type="depthBelowLandLayer") for _ in range(8)
    ]
    return surface, t_so, w_so


def test_native_surface_inventory_enforces_time_units_levels_and_message_count():
    surface, t_so, w_so = valid_native_surface_inventory()
    selected, contract = PACKAGE_MODULE.validate_grib_inventory(
        surface, t_so, w_so, "2020-01-15T00:00:00+00:00"
    )
    assert set(selected) == {"SKT", "W_SNOW", "RHO_SNOW"}
    assert contract["T_SO"]["message_count"] == 8

    with pytest.raises(ValueError, match="exactly one SKT"):
        PACKAGE_MODULE.validate_grib_inventory(
            [*surface, surface[0]], t_so, w_so, "2020-01-15T00:00:00Z"
        )
    stale = [grib_field("SKT", "K", validity_time=100), *surface[1:]]
    with pytest.raises(ValueError, match="differs from requested"):
        PACKAGE_MODULE.validate_grib_inventory(
            stale, t_so, w_so, "2020-01-15T00:00:00Z"
        )
    bad_units = [surface[0], grib_field("W_SNOW", "m"), surface[2]]
    with pytest.raises(ValueError, match="units"):
        PACKAGE_MODULE.validate_grib_inventory(
            bad_units, t_so, w_so, "2020-01-15T00:00:00Z"
        )
    accumulated = [surface[0], grib_field("W_SNOW", "kg m-2", step_type="accum"), surface[2]]
    with pytest.raises(ValueError, match="instantaneous"):
        PACKAGE_MODULE.validate_grib_inventory(
            accumulated, t_so, w_so, "2020-01-15T00:00:00Z"
        )


def test_native_surface_extpar_inventory_enforces_shape_and_physical_ranges():
    values = np.array([0.1, 0.2])
    assert PACKAGE_MODULE.validate_extpar_inventory(
        values, values, np.array([3.0, 9.0]), np.array([0.0, 1.0]), values
    ) == 2
    with pytest.raises(ValueError, match="inconsistent sizes"):
        PACKAGE_MODULE.validate_extpar_inventory(
            values, values[:1], np.array([3.0, 9.0]), np.array([0.0, 1.0]), values
        )
    with pytest.raises(ValueError, match="SOILTYP"):
        PACKAGE_MODULE.validate_extpar_inventory(
            values, values, np.array([3.5, 9.0]), np.array([0.0, 1.0]), values
        )
    with pytest.raises(ValueError, match="FR_LAND"):
        PACKAGE_MODULE.validate_extpar_inventory(
            values, values, np.array([3.0, 9.0]), np.array([-0.1, 1.0]), values
        )


def test_native_surface_value_inventory_rejects_invalid_snow_density():
    surface = {
        "SKT": np.array([280.0, 281.0]),
        "W_SNOW": np.array([0.0, 10.0]),
        "RHO_SNOW": np.array([0.0, 200.0]),
    }
    PACKAGE_MODULE.validate_native_surface_values(
        np.full((2, 2), 278.0), np.full((2, 2), 10.0), surface, np.array([500.0, 600.0])
    )
    surface["RHO_SNOW"][1] = 0.0
    with pytest.raises(ValueError, match="RHO_SNOW"):
        PACKAGE_MODULE.validate_native_surface_values(
            np.full((2, 2), 278.0),
            np.full((2, 2), 10.0),
            surface,
            np.array([500.0, 600.0]),
        )


def test_fixed_surface_depth_uses_layer_bottom_when_present():
    field = FakeField(
        {
            "scaledValueOfFirstFixedSurface": 27,
            "scaleFactorOfFirstFixedSurface": 2,
            "scaledValueOfSecondFixedSurface": 81,
            "scaleFactorOfSecondFixedSurface": 2,
        }
    )
    assert MODULE.fixed_surface_depth(field) == 0.81


def test_normalized_uuid_matches_grib_and_extpar_spellings():
    assert MODULE.normalized_uuid("17643da2-5749-59b6-44d2-54a3cd6e2bc0") == (
        "17643da2574959b644d254a3cd6e2bc0"
    )
