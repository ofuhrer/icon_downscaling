from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "case_studies/swiss_200m/validation/package_rea_l_native_surface.py"
SPEC = importlib.util.spec_from_file_location("native_surface_package", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class FakeField:
    def __init__(self, metadata): self.values = metadata
    def metadata(self, key): return self.values[key]


def field(name, units, level_type="surface", validity_time=0, step_type="instant"):
    return FakeField({
        "shortName": name, "units": units, "validityDate": 20200115,
        "validityTime": validity_time, "stepType": step_type, "step": 0,
        "stepRange": "0", "typeOfLevel": level_type,
    })


def inventory():
    surface = [
        field("SKT", "K"),
        field("W_SNOW", "kg m**-2"),
        field("RHO_SNOW", "kg m-3"),
        field("T_SNOW", "K"),
    ]
    soil_t = [field("T_SO", "K", "depthBelowLandLayer") for _ in range(8)]
    soil_w = [field("W_SO", "kg m-2", "depthBelowLandLayer") for _ in range(8)]
    return surface, soil_t, soil_w


def test_native_surface_inventory_is_exact() -> None:
    surface, soil_t, soil_w = inventory()
    selected, details = MODULE.validate_grib_inventory(
        surface, soil_t, soil_w, "2020-01-15T00:00:00Z"
    )
    assert set(selected) == {"SKT", "W_SNOW", "RHO_SNOW", "T_SNOW"}
    assert details["T_SO"]["message_count"] == 8
    with pytest.raises(ValueError, match="exactly one SKT"):
        MODULE.validate_grib_inventory([*surface, surface[0]], soil_t, soil_w, "2020-01-15T00:00:00Z")
    with pytest.raises(ValueError, match="instantaneous"):
        bad = [
            surface[0],
            field("W_SNOW", "kg m-2", step_type="accum"),
            surface[2],
            surface[3],
        ]
        MODULE.validate_grib_inventory(bad, soil_t, soil_w, "2020-01-15T00:00:00Z")


def test_extpar_and_snow_ranges_are_checked() -> None:
    values = np.array([0.1, 0.2])
    assert MODULE.validate_extpar_inventory(
        values, values, np.array([3.0, 9.0]), np.array([0.0, 1.0]), values
    ) == 2
    with pytest.raises(ValueError, match="FR_LAND"):
        MODULE.validate_extpar_inventory(
            values, values, np.array([3.0, 9.0]), np.array([-0.1, 1.0]), values
        )
    surface = {
        "SKT": np.array([280.0, 281.0]),
        "W_SNOW": np.array([0.0, 10.0]),
        "RHO_SNOW": np.array([0.0, 200.0]),
        "T_SNOW": np.array([np.nan, 270.0]),
    }
    MODULE.validate_native_surface_values(
        np.full((2, 2), 278.0), np.full((2, 2), 10.0), surface, np.array([500.0, 600.0])
    )
    surface["RHO_SNOW"][1] = 0.0
    with pytest.raises(ValueError, match="RHO_SNOW"):
        MODULE.validate_native_surface_values(
            np.full((2, 2), 278.0), np.full((2, 2), 10.0), surface, np.array([500.0, 600.0])
        )


def test_grid_identity_does_not_require_earthkit_geography_facade() -> None:
    value = FakeField({
        "uuidOfHGrid": "grid-uuid",
        "gridType": "unstructured_grid",
        "gridDefinitionTemplateNumber": 101,
        "numberOfDataPoints": 42,
        "numberOfValues": 42,
        "md5GridSection": "grid-md5",
    })
    assert MODULE.grid_spec(value) == {
        "uuidOfHGrid": "grid-uuid",
        "gridType": "unstructured_grid",
        "gridDefinitionTemplateNumber": 101,
        "numberOfDataPoints": 42,
        "numberOfValues": 42,
        "md5GridSection": "grid-md5",
    }
