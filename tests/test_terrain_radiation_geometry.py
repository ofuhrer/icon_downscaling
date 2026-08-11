from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import netCDF4
from pyproj import CRS, Transformer
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


def test_horayzon_float32_radian_azimuth_roundoff_is_accepted() -> None:
    stored = np.deg2rad(MODULE.AZIMUTH_DEGREES).astype(np.float32)
    converted = np.mod(np.rad2deg(stored), 360.0)
    assert np.max(np.abs(converted - MODULE.AZIMUTH_DEGREES)) > 1.0e-5
    MODULE.validate_horayzon_azimuths(stored)
    with pytest.raises(ValueError, match="unexpected azimuths"):
        MODULE.validate_horayzon_azimuths(stored + np.float32(np.deg2rad(0.01)))


def test_pinned_horayzon_v121_padded_slope_contract() -> None:
    calls = {}

    class FakeTransform:
        @staticmethod
        def rotation_matrix_glob2loc(north, normal):
            calls["direction_shapes"] = (north.shape, normal.shape)
            return np.zeros((4, 5, 3, 3), dtype=np.float32)

    class FakeTopoParam:
        @staticmethod
        def slope_plane_meth(x, y, z, *, rot_mat, output_rot):
            calls["slope_shapes"] = (x.shape, y.shape, z.shape, rot_mat.shape)
            calls["output_rot"] = output_rot
            result = np.full((4, 5, 3), np.nan, dtype=np.float32)
            result[1:-1, 1:-1, 2] = 1.0
            return result

    class FakeHorayzon:
        transform = FakeTransform()
        topo_param = FakeTopoParam()

    padded = np.zeros((4, 5), dtype=np.float32)
    target_vectors = np.zeros((2, 3, 3), dtype=np.float32)
    tilted = MODULE.tilted_surface_normals_v121(
        FakeHorayzon(), padded, padded, padded, target_vectors, target_vectors
    )
    assert calls["direction_shapes"] == ((2, 3, 3), (2, 3, 3))
    assert calls["slope_shapes"] == ((4, 5), (4, 5), (4, 5), (4, 5, 3, 3))
    assert calls["output_rot"] is True
    assert tilted.shape == (2, 3, 3)
    np.testing.assert_array_equal(tilted[..., 2], 1.0)


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
        xx, yy = np.meshgrid([0, 200, 400, 600], [0, 200, 400])
        to_geo = Transformer.from_crs(
            CRS.from_proj4("+proj=aeqd +lat_0=46.8 +lon_0=8.2 +datum=WGS84 +units=m"),
            "EPSG:4326",
            always_xy=True,
        )
        lon, lat = to_geo.transform(xx, yy)
        dataset.createVariable("lat", "f4", ("y", "x"))[:] = lat
        dataset.createVariable("lon", "f4", ("y", "x"))[:] = lon
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
    reused = MODULE.validate_extended_dem_reuse(
        output, base, driving_source, 0.2, 0.01
    )
    assert reused["validated_against_current_sources"] is True


def test_pinned_horayzon_v121_file_api_and_axes(tmp_path: Path) -> None:
    calls = {}

    class FakeHorizon:
        @staticmethod
        def horizon_gridded(
            vertices,
            dem_ny,
            dem_nx,
            vec_norm,
            vec_north,
            y_start,
            x_start,
            *,
            file_out,
            x_axis_val,
            y_axis_val,
            x_axis_name,
            y_axis_name,
            units,
            dist_search,
            azim_num,
        ):
            calls.update(
                dem_shape=(dem_ny, dem_nx),
                offsets=(y_start, x_start),
                x=x_axis_val.copy(),
                y=y_axis_val.copy(),
                names=(x_axis_name, y_axis_name, units),
                search=dist_search,
                azim_num=azim_num,
            )
            with netCDF4.Dataset(file_out, "w") as dataset:
                dataset.createDimension("y", vec_norm.shape[0])
                dataset.createDimension("x", vec_norm.shape[1])
                dataset.createDimension("azim", azim_num)
                dataset.createVariable("x", "f4", ("x",))[:] = x_axis_val
                dataset.createVariable("y", "f4", ("y",))[:] = y_axis_val
                dataset.createVariable("azim", "f4", ("azim",))[:] = np.deg2rad(
                    MODULE.AZIMUTH_DEGREES
                )
                horizon = dataset.createVariable("horizon", "f4", ("y", "x", "azim"))
                horizon[:] = np.arange(vec_norm.shape[0], dtype=np.float32)[:, None, None]

    class FakeHorayzon:
        horizon = FakeHorizon()

    normals = np.zeros((2, 3, 3), dtype=np.float32)
    horizon, azimuth = MODULE.run_horayzon_v121(
        FakeHorayzon(),
        np.zeros(60, dtype=np.float32),
        (4, 5),
        normals,
        normals,
        1,
        1,
        np.array([0.0, 200.0, 400.0]),
        np.array([200.0, 0.0]),
        20.0,
        tmp_path,
    )
    assert calls["dem_shape"] == (4, 5)
    assert calls["offsets"] == (1, 1)
    np.testing.assert_array_equal(calls["x"], [0.0, 200.0, 400.0])
    np.testing.assert_array_equal(calls["y"], [200.0, 0.0])
    assert calls["names"] == ("x", "y", "m")
    assert calls["search"] == 20.0
    assert calls["azim_num"] == 90
    assert horizon.shape == (2, 3, 90)
    np.testing.assert_array_equal(horizon[:, 0, 0], [0.0, 1.0])
    np.testing.assert_allclose(np.rad2deg(azimuth), MODULE.AZIMUTH_DEGREES, atol=2.0e-5)
