from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path
import sys

import netCDF4
import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "assess_sst_fallback_support.py"
SPEC = importlib.util.spec_from_file_location("sst_fallback_support", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def make_inputs(tmp_path: Path, *, fallback_on_land: bool = False, no_fallback: bool = False):
    static = tmp_path / "static.nc"
    forcing = tmp_path / "forcing.nc"
    observations = tmp_path / "observations.csv"
    report = tmp_path / "report.json"

    ny, nx = 6, 8
    y, x = np.mgrid[:ny, :nx]
    latitude = 46.0 + y * 0.005
    longitude = 7.0 + x * 0.01
    landmask = np.ones((ny, nx), dtype=np.int8)
    water_cells = ((0, 0), (0, 1), (1, 0), (1, 1), (2, 2), (3, 2), (4, 2), (5, 7))
    for row, column in water_cells:
        landmask[row, column] = 0

    with netCDF4.Dataset(static, "w") as dataset:
        dataset.createDimension("y", ny)
        dataset.createDimension("x", nx)
        dataset.createVariable("x", "f8", ("x",))[:] = np.arange(nx) * 1_000.0
        dataset.createVariable("y", "f8", ("y",))[:] = np.arange(ny) * 500.0
        dataset.createVariable("lat", "f8", ("y", "x"))[:] = latitude
        dataset.createVariable("lon", "f8", ("y", "x"))[:] = longitude
        dataset.createVariable("landmask", "i1", ("y", "x"))[:] = landmask

    fallback_mask = np.zeros((ny, nx), dtype=np.int8)
    fallback_distance = np.full((ny, nx), np.nan, dtype=np.float64)
    if not no_fallback:
        fallback_cells = ((0, 0, 1.0), (2, 2, 3.0), (4, 2, 5.0))
        for row, column, distance in fallback_cells:
            fallback_mask[row, column] = 1
            fallback_distance[row, column] = distance
    if fallback_on_land:
        fallback_mask[0, 7] = 1
        fallback_distance[0, 7] = 2.0

    fallback_count = int(np.count_nonzero(fallback_mask))
    with netCDF4.Dataset(forcing, "w") as dataset:
        dataset.createDimension("y_1", ny)
        dataset.createDimension("x_1", nx)
        dataset.createVariable("lat_1", "f8", ("y_1", "x_1"))[:] = latitude
        dataset.createVariable("lon_1", "f8", ("y_1", "x_1"))[:] = longitude
        dataset.createVariable(
            "SST_global_fallback_mask", "i1", ("y_1", "x_1")
        )[:] = fallback_mask
        distance = dataset.createVariable(
            "SST_global_fallback_distance_km",
            "f8",
            ("y_1", "x_1"),
            fill_value=np.nan,
        )
        distance[:] = fallback_distance
        dataset.sst_water_cell_count = len(water_cells)
        dataset.sst_water_global_fallback_count = fallback_count
        dataset.sst_maximum_global_fallback_distance_km = (
            float(np.nanmax(fallback_distance)) if fallback_count else 0.0
        )

    header = ["meas_site", "nat_abbr", "latitude", "longitude", "termin"]
    rows = [
        ["1", "EXA", str(latitude[0, 0]), str(longitude[0, 0]), "20200101000000"],
        ["1", "EXA", str(latitude[0, 0]), str(longitude[0, 0]), "20200101010000"],
        ["2", "EXB", str(latitude[5, 7]), str(longitude[5, 7]), "20200101000000"],
    ]
    observations.write_text(
        ";".join(header) + "\n" + "\n".join(";".join(row) for row in rows) + "\n",
        encoding="utf-8",
    )
    return static, forcing, observations, report


def test_reports_area_components_distances_and_unique_stations(tmp_path: Path) -> None:
    static, forcing, observations, report = make_inputs(tmp_path)

    assert MODULE.main(
        [
            "--forcing",
            str(forcing),
            "--static",
            str(static),
            "--observations",
            str(observations),
            "--report",
            str(report),
        ]
    ) == 0

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["grid"]["cell_area_km2"] == 0.5
    summary = payload["summary"]
    assert summary["water_cell_count"] == 8
    assert summary["water_area_km2"] == 4.0
    assert summary["global_fallback_cell_count"] == 3
    assert summary["global_fallback_area_km2"] == 1.5
    assert summary["global_fallback_fraction_of_water"] == 3 / 8
    distances = summary["sst_source_donor_distance_km"]
    assert distances["maximum"] == 5.0
    assert distances["p50"] == 3.0
    assert math.isclose(distances["p90"], 4.6)
    assert math.isclose(distances["p99"], 4.96)

    components = payload["water_components"]
    assert components["connectivity"].startswith("four-neighbour")
    assert components["total_component_count"] == 3
    assert components["fallback_affected_component_count"] == 2
    by_area = components["components"]
    assert [component["water_cell_count"] for component in by_area] == [4, 3, 1]
    assert [component["fallback_cell_count"] for component in by_area] == [1, 2, 0]
    assert by_area[0]["fallback_fraction_of_component_water"] == 0.25
    assert by_area[0]["centroid"]["latitude"] == pytest.approx(46.0025)
    assert by_area[0]["centroid"]["longitude"] == pytest.approx(7.005)
    assert by_area[1]["bounds"]["latitude_min"] == 46.01
    assert by_area[1]["bounds"]["latitude_max"] == 46.02

    stations = payload["stations"]
    assert stations["unique_station_count"] == 2
    by_key = {site["key"]: site for site in stations["sites"]}
    assert by_key["EXA:1"]["nearest_fallback_cell_distance_km"] == pytest.approx(0.0)
    assert by_key["EXA:1"]["nearest_fallback_cell"]["sst_source_donor_distance_km"] == 1.0
    assert by_key["EXB:2"]["nearest_fallback_cell_distance_km"] > 1.0


def test_no_fallback_has_null_distribution_and_station_distance(tmp_path: Path) -> None:
    static, forcing, observations, _ = make_inputs(tmp_path, no_fallback=True)

    payload = MODULE.build_report(forcing, static, observations)

    assert payload["summary"]["sst_source_donor_distance_km"] == {
        "count": 0,
        "maximum": None,
        "p50": None,
        "p90": None,
        "p99": None,
    }
    assert payload["water_components"]["fallback_affected_component_count"] == 0
    assert all(
        site["nearest_fallback_cell_distance_km"] is None
        for site in payload["stations"]["sites"]
    )


def test_rejects_fallback_mask_on_static_land(tmp_path: Path) -> None:
    static, forcing, observations, report = make_inputs(tmp_path, fallback_on_land=True)

    with pytest.raises(ValueError, match="includes target land"):
        MODULE.main(
            [
                "--forcing",
                str(forcing),
                "--static",
                str(static),
                "--observations",
                str(observations),
                "--report",
                str(report),
            ]
        )

    assert not report.exists()


def test_component_retention_keeps_largest_and_material_components() -> None:
    water_counts = np.zeros(26, dtype=np.int64)
    fallback_counts = np.zeros(26, dtype=np.int64)
    water_counts[1:] = 1

    selected, reasons = MODULE.select_component_ids(
        water_counts, fallback_counts, cell_area_km2=0.04
    )

    assert selected == set(range(1, 21))
    assert reasons[1] == ["largest_20_by_water_area"]

    water_counts[21:] = 25
    fallback_counts[25] = 5
    selected, reasons = MODULE.select_component_ids(
        water_counts, fallback_counts, cell_area_km2=0.04
    )

    assert set(range(21, 26)) <= selected
    assert "water_area_ge_1_km2" in reasons[21]
    assert "fallback_area_ge_0.2_km2" in reasons[25]
