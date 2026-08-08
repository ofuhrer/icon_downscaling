from __future__ import annotations

import importlib.util
from pathlib import Path

import netCDF4
import numpy as np


SCRIPT = Path(__file__).parents[1] / "scripts" / "crop_static_domain.py"
SPEC = importlib.util.spec_from_file_location("crop_static_domain", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_boundary_weight_has_zero_edge_and_unit_interior():
    weight = MODULE.boundary_weight(9, 11, 2.0)
    assert np.all(weight[0, :] == 0.0)
    assert np.all(weight[:, 0] == 0.0)
    assert weight[4, 5] == 1.0
    assert np.isclose(weight[1, 5], 0.5)


def test_crop_rebuilds_topography_blend(tmp_path):
    source = tmp_path / "source.nc"
    output = tmp_path / "crop.nc"
    with netCDF4.Dataset(source, "w") as ds:
        ds.createDimension("y", 8)
        ds.createDimension("x", 10)
        ds.hicar_dx_m = 1000.0
        ds.createVariable("x", "f4", ("x",))[:] = np.arange(10) * 1000
        ds.createVariable("y", "f4", ("y",))[:] = np.arange(8) * 1000
        for name, value in (
            ("topo_highres", 100.0),
            ("topo_driving", 20.0),
            ("topo_blend_weight", 1.0),
            ("topo", 100.0),
        ):
            ds.createVariable(name, "f4", ("y", "x"))[:] = value

    MODULE.crop_static(
        source,
        output,
        x_start=2,
        x_stop=9,
        y_start=1,
        y_stop=7,
        blend_width_km=2.0,
    )
    with netCDF4.Dataset(output) as ds:
        assert ds.dimensions["x"].size == 7
        assert ds.dimensions["y"].size == 6
        weight = ds["topo_blend_weight"][:]
        topo = ds["topo"][:]
        assert np.all(weight[0, :] == 0.0)
        assert topo[0, 3] == 20.0
        assert topo[2, 3] == 100.0
