from __future__ import annotations

from pathlib import Path

import netCDF4
import numpy as np
import pytest

from preprocessing.hicarprep.products import sha256
from preprocessing.hicarprep.remap import RBFWeights, grid_fingerprint
from preprocessing.hicarprep.sst import build_target_sst_product, load_target_sst


def inputs(root: Path) -> tuple[Path, Path, RBFWeights, np.ndarray, np.ndarray, np.ndarray]:
    static = root / "static.nc"
    source = root / "skt.grib"
    source.write_bytes(b"synthetic-skt")
    lat = np.array([[46.0, 46.0], [46.1, 46.1]])
    lon = np.array([[7.0, 7.1], [7.0, 7.1]])
    land = np.array([[True, False], [True, False]])
    with netCDF4.Dataset(static, "w") as dataset:
        dataset.createDimension("y", 2)
        dataset.createDimension("x", 2)
        dataset.createVariable("lat", "f8", ("y", "x"))[:] = lat
        dataset.createVariable("lon", "f8", ("y", "x"))[:] = lon
        dataset.createVariable("landmask", "i1", ("y", "x"))[:] = land
    donors = np.array([[0, 1], [1, 0], [2, 3], [3, 2]])
    weight = np.array([[0.9, 0.1], [0.9, 0.1], [0.9, 0.1], [0.9, 0.1]])
    weights = RBFWeights(
        donor_index=donors,
        weight=weight,
        target_shape=(2, 2),
        source_fingerprint=grid_fingerprint(lat.ravel(), lon.ravel()),
        target_fingerprint=grid_fingerprint(lat, lon),
    )
    return static, source, weights, lat, lon, land


def test_target_sst_uses_same_surface_water_and_exact_contract(tmp_path: Path) -> None:
    static, source, weights, lat, lon, land = inputs(tmp_path)
    output = tmp_path / "target_sst.nc"
    source_skt = np.array([290.0, 280.0, 292.0, 282.0])
    diagnostics = build_target_sst_product(
        output,
        source_skt=source_skt,
        source_lat=lat.ravel(),
        source_lon=lon.ravel(),
        source_land=land.ravel(),
        static_path=static,
        weights=weights,
        valid_time="2020-02-10T01:00:00Z",
        source_path=source,
    )
    actual = load_target_sst(
        output,
        static_path=static,
        valid_time="2020-02-10T01:00:00Z",
        target_lat=lat,
        target_lon=lon,
        target_land=land,
    )
    np.testing.assert_allclose(actual[~land], [280.0, 282.0])
    assert diagnostics["water_cell_count"] == 2
    with netCDF4.Dataset(output) as dataset:
        assert dataset.static_sha256 == sha256(static)
        assert dataset.source_variable == "SKT"


def test_target_sst_rejects_time_and_mask_mismatch(tmp_path: Path) -> None:
    static, source, weights, lat, lon, land = inputs(tmp_path)
    output = tmp_path / "target_sst.nc"
    build_target_sst_product(
        output,
        source_skt=np.array([290.0, 280.0, 292.0, 282.0]),
        source_lat=lat.ravel(),
        source_lon=lon.ravel(),
        source_land=land.ravel(),
        static_path=static,
        weights=weights,
        valid_time="2020-02-10T01:00:00Z",
        source_path=source,
    )
    with pytest.raises(ValueError, match="valid_time"):
        load_target_sst(
            output,
            static_path=static,
            valid_time="2020-02-10T02:00:00Z",
            target_lat=lat,
            target_lon=lon,
            target_land=land,
        )
    with netCDF4.Dataset(output, "a") as dataset:
        dataset["water_mask"][:] = 0
    with pytest.raises(ValueError, match="water mask"):
        load_target_sst(
            output,
            static_path=static,
            valid_time="2020-02-10T01:00:00Z",
            target_lat=lat,
            target_lon=lon,
            target_land=land,
        )
