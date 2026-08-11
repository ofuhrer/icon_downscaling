from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import netCDF4
import pytest


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "case_studies"
    / "swiss_200m"
    / "scripts"
    / "prepare_terrain_radiation_geometry.py"
)
SPEC = importlib.util.spec_from_file_location("terrain_radiation_geometry", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_extension_covers_complete_search_cell() -> None:
    assert MODULE.extension_cells(20.0, 200.0) == 101
    source = np.array([-200.0, 0.0, 200.0])
    extended = MODULE.extended_axis(source, 101, 200.0)
    assert extended.size == 205
    assert extended[101] == source[0]
    assert extended[-102] == source[-1]
    assert source[0] - extended[0] == 20_200.0


def test_spacing_rejects_irregular_grid() -> None:
    assert MODULE.spacing(np.array([0.0, 200.0, 400.0]), "x") == 200.0
    with pytest.raises(ValueError, match="regularly spaced"):
        MODULE.spacing(np.array([0.0, 200.0, 401.0]), "x")


def test_boundary_values_do_not_duplicate_corners() -> None:
    values = np.arange(12).reshape(3, 4)
    boundary = MODULE.boundary_values(values)
    assert boundary.size == 10
    assert set(boundary.tolist()) == {0, 1, 2, 3, 4, 7, 8, 9, 10, 11}


def test_geometry_contract_ranges(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MODULE, "EXPECTED_TARGET_SHAPE", (2, 3))
    hlm = np.full((90, 2, 3), 90.0, dtype=np.float32)
    svf = np.ones((2, 3), dtype=np.float32)
    slope = np.zeros((2, 3), dtype=np.float32)
    aspect = np.zeros((2, 3), dtype=np.float32)
    ranges = MODULE.validate_arrays(hlm, svf, slope, aspect)
    assert ranges["hlm"] == [90.0, 90.0]
    svf[0, 0] = 1.01
    with pytest.raises(ValueError, match="svf range"):
        MODULE.validate_arrays(hlm, svf, slope, aspect)


def test_selected_azimuth_convention() -> None:
    np.testing.assert_array_equal(MODULE.AZIMUTH_DEGREES, np.arange(0.0, 360.0, 4.0))


def test_extended_dem_contains_exact_target_and_real_outer_band(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MODULE, "EXPECTED_TARGET_SHAPE", (3, 4))
    base = tmp_path / "base.nc"
    driving_source = tmp_path / "driving.nc"
    output = tmp_path / "extended.nc"
    with netCDF4.Dataset(base, "w") as dataset:
        dataset.createDimension("x", 4)
        dataset.createDimension("y", 3)
        dataset.createVariable("x", "f4", ("x",))[:] = [0, 200, 400, 600]
        dataset.createVariable("y", "f4", ("y",))[:] = [0, 200, 400]
        dataset.createVariable("lat", "f4", ("y", "x"))[:] = 46.8
        dataset.createVariable("lon", "f4", ("y", "x"))[:] = 8.2
        topo = np.zeros((3, 4), dtype=np.float32)
        topo[1, 1:3] = 100.0
        dataset.createVariable("topo", "f4", ("y", "x"))[:] = topo
        dataset.hicar_projection = "+proj=aeqd +lat_0=46.8 +lon_0=8.2 +datum=WGS84 +units=m"
    with netCDF4.Dataset(driving_source, "w"):
        pass

    def fake_driving(*args, **kwargs):  # noqa: ANN002, ANN003
        latitude = args[1]
        return np.zeros(latitude.shape, dtype=np.float32)

    monkeypatch.setattr(MODULE, "interpolate_driving_topography", fake_driving)
    report = MODULE.prepare_extended_dem(
        base,
        driving_source,
        output,
        0.2,
        "HSURF",
        "lat",
        "lon",
        0.01,
    )
    assert report["actual_extension_km"] == 0.4
    with netCDF4.Dataset(output) as dataset:
        assert dataset.variables["topo"].shape == (7, 8)
        np.testing.assert_array_equal(dataset.variables["topo"][2:5, 2:6], topo)
        assert np.count_nonzero(dataset.variables["topo"][:]) == 2
    assert Path(f"{output}.ready").is_file()
