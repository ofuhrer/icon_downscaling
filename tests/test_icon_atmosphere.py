from __future__ import annotations

from pathlib import Path

import netCDF4
import numpy as np
import pytest

from preprocessing.hicarprep import icon_atmosphere as atmosphere


class FakeGeography:
    def grid_spec(self):
        return {"type": "unstructured", "uid": "test-grid"}


class FakeField:
    def __init__(self, values: np.ndarray, **metadata):
        self.values = np.asarray(values)
        self._metadata = metadata
        self.geography = FakeGeography()

    def metadata(self, key=None):
        if key is None:
            return dict(self._metadata)
        if key not in self._metadata:
            raise KeyError(key)
        return self._metadata[key]

    def to_numpy(self, *, flatten: bool):
        return self.values.reshape(-1) if flatten else self.values


def fake_field(spec, values, *, level=0, step=1, validity_time=100):
    return FakeField(
        values,
        shortName=spec.name,
        paramId=spec.param_id,
        units={
            "P": "Pa",
            "T": "K",
            "U": "m s-1",
            "V": "m s-1",
            "W": "m s-1",
            "HHL": "m",
            "QV": "kg kg-1",
            "QC": "kg kg-1",
            "HSURF": "m",
            "FR_LAND": "Proportion",
        }[spec.name],
        typeOfLevel=spec.level_type,
        stepType="instant",
        level=level,
        step=step,
        dataDate=20200210,
        dataTime=0,
        validityDate=20200210,
        validityTime=validity_time,
        uuidOfHGrid="test-grid",
    )


def inventories(cells=2):
    dynamic = []
    for spec in atmosphere.FULL_LEVEL_SPECS:
        for level in range(1, 81):
            if spec.name == "P":
                values = np.full(cells, 1_000.0 + level * 1_000.0)
            elif spec.name == "T":
                values = np.full(cells, 250.0 + level * 0.1)
            elif spec.name in {"QV", "QC"}:
                values = np.full(cells, 1.0e-3)
            else:
                values = np.full(cells, 5.0)
            dynamic.append(fake_field(spec, values, level=level))
    w_spec = atmosphere.HALF_LEVEL_SPECS[0]
    for level in range(1, 82):
        dynamic.append(fake_field(w_spec, np.full(cells, 0.1), level=level))

    geometry = []
    hhl_spec = atmosphere.HALF_LEVEL_SPECS[1]
    for level in range(1, 82):
        height = 22_000.0 - (level - 1) * 273.75
        geometry.append(
            fake_field(
                hhl_spec, np.full(cells, height), level=level, step=0, validity_time=0
            )
        )
    for spec in atmosphere.SURFACE_SPECS:
        values = np.full(cells, 100.0 if spec.name == "HSURF" else 1.0)
        geometry.append(fake_field(spec, values, step=0, validity_time=0))
    return dynamic, geometry


def write_extpar(path: Path, cells=2):
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("cell", cells)
        dataset.createVariable("clat", "f8", ("cell",))[:] = [0.8, 0.81]
        dataset.createVariable("clon", "f8", ("cell",))[:] = [0.1, 0.11]
        dataset.uuidOfHGrid = "test-grid"


def test_inventory_rejects_duplicate_level_and_wrong_param_id():
    dynamic, _ = inventories()
    atmosphere.index_inventory(
        dynamic,
        (*atmosphere.FULL_LEVEL_SPECS, atmosphere.HALF_LEVEL_SPECS[0]),
        "2020-02-10T01:00:00Z",
    )
    with pytest.raises(ValueError, match="duplicate P level 1"):
        atmosphere.index_inventory(
            [*dynamic, dynamic[0]],
            (*atmosphere.FULL_LEVEL_SPECS, atmosphere.HALF_LEVEL_SPECS[0]),
            "2020-02-10T01:00:00Z",
        )
    dynamic[0]._metadata["paramId"] = 999
    with pytest.raises(ValueError, match="unexpected atmospheric GRIB message"):
        atmosphere.index_inventory(
            dynamic,
            (*atmosphere.FULL_LEVEL_SPECS, atmosphere.HALF_LEVEL_SPECS[0]),
            "2020-02-10T01:00:00Z",
        )


def test_operational_decode_reverses_levels_and_records_explicit_missing_qi(tmp_path, monkeypatch):
    dynamic, geometry = inventories()
    dynamic_path = tmp_path / "dynamic.grib"
    geometry_path = tmp_path / "geometry.grib"
    extpar_path = tmp_path / "extpar.nc"
    output_path = tmp_path / "atmosphere.nc"
    dynamic_path.write_bytes(b"dynamic")
    geometry_path.write_bytes(b"geometry")
    write_extpar(extpar_path)
    monkeypatch.setattr(
        atmosphere,
        "read_grib_fields",
        lambda path: dynamic if path == dynamic_path else geometry,
    )
    with pytest.raises(ValueError, match="does not archive QI"):
        atmosphere.decode_icon_atmosphere(
            dynamic_path,
            geometry_path,
            extpar_path,
            "2020-02-10T01:00:00Z",
            output_path,
        )
    report = atmosphere.decode_icon_atmosphere(
        dynamic_path,
        geometry_path,
        extpar_path,
        "2020-02-10T01:00:00Z",
        output_path,
        missing_qi_policy="source-absent-zero",
    )
    assert report["dynamic_message_count"] == 561
    assert report["geometry_message_count"] == 83
    with netCDF4.Dataset(output_path) as dataset:
        assert dataset.valid_time == "2020-02-10T01:00:00Z"
        assert dataset.reference_time == "2020-02-10T00:00:00Z"
        assert dataset.vertical_order == "bottom_to_top"
        assert dataset.missing_qi_policy == "source_absent_zero"
        np.testing.assert_allclose(dataset["QI"][:], 0.0)
        assert dataset["P"][0, 0] > dataset["P"][-1, 0]
        assert dataset["HHL"][0, 0] < dataset["HHL"][-1, 0]
        assert dataset["source_level"][0] == 80
        assert dataset["source_half_level"][0] == 81


def test_decode_rejects_nonmonotone_pressure(tmp_path, monkeypatch):
    dynamic, geometry = inventories()
    p_level_79 = next(
        field
        for field in dynamic
        if field._metadata["shortName"] == "P" and field._metadata["level"] == 79
    )
    p_level_79.values[:] = 90_000.0
    dynamic_path = tmp_path / "dynamic.grib"
    geometry_path = tmp_path / "geometry.grib"
    extpar_path = tmp_path / "extpar.nc"
    dynamic_path.write_bytes(b"dynamic")
    geometry_path.write_bytes(b"geometry")
    write_extpar(extpar_path)
    monkeypatch.setattr(
        atmosphere,
        "read_grib_fields",
        lambda path: dynamic if path == dynamic_path else geometry,
    )
    with pytest.raises(ValueError, match="strictly decreasing"):
        atmosphere.decode_icon_atmosphere(
            dynamic_path,
            geometry_path,
            extpar_path,
            "2020-02-10T01:00:00Z",
            tmp_path / "output.nc",
            missing_qi_policy="source-absent-zero",
        )
