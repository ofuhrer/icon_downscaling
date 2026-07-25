import importlib.util
from datetime import date
from pathlib import Path
import sys

import netCDF4
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "validation"
    / "retrieve_ogd_grid_references.py"
)
SPEC = importlib.util.spec_from_file_location("ogd_grid_retriever", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def asset(key):
    return {"href": f"https://example.invalid/{key}", "type": "application/x-netcdf"}


def test_selects_annual_surface_and_requested_monthly_radiation_assets():
    surface_keys = [
        "x.rhiresd_ch01h.swiss.lv95_20200101000000_20201231000000.nc",
        "x.tabsd_ch01r.swiss.lv95_20200101000000_20201231000000.nc",
    ]
    satellite_keys = []
    for month, last_day in ((1, 31), (7, 31)):
        for product in ("sis", "sis-no-horizon"):
            satellite_keys.append(
                f"x.msg.{product}.h_ch02.lonlat_2020{month:02d}01000000_"
                f"2020{month:02d}{last_day:02d}230000.nc"
            )
    selected = MODULE.selected_assets(
        2020,
        [7, 1],
        {"assets": {key: asset(key) for key in surface_keys}},
        {"assets": {key: asset(key) for key in satellite_keys}},
    )
    assert len(selected) == 6
    assert {item["year"] for item in selected} == {2020}
    assert [item["product"] for item in selected[:2]] == ["rhiresd", "tabsd"]
    assert [item["month"] for item in selected[2:]] == [1, 1, 7, 7]


def test_hydrological_year_selects_both_surface_years_and_all_months():
    year_months = MODULE.year_months_for_period(
        date(2019, 10, 1),
        date(2020, 10, 1),
    )

    assert year_months == {
        2019: [10, 11, 12],
        2020: [1, 2, 3, 4, 5, 6, 7, 8, 9],
    }


def test_period_asset_selection_keeps_year_identity():
    surface_keys = []
    satellite_keys = []
    for year in (2019, 2020):
        for product in ("rhiresd", "tabsd"):
            surface_keys.append(
                f"x.{product}_ch01h.swiss.lv95_{year}0101000000_"
                f"{year}1231000000.nc"
            )
        month = 12 if year == 2019 else 1
        last_day = 31
        for product in ("sis", "sis-no-horizon"):
            satellite_keys.append(
                f"x.msg.{product}.h_ch02.lonlat_{year}{month:02d}01000000_"
                f"{year}{month:02d}{last_day:02d}230000.nc"
            )

    selected = MODULE.selected_period_assets(
        {2019: [12], 2020: [1]},
        {"assets": {key: asset(key) for key in surface_keys}},
        {"assets": {key: asset(key) for key in satellite_keys}},
    )

    assert len(selected) == 8
    assert [(item["year"], item["product"]) for item in selected[:4]] == [
        (2019, "rhiresd"),
        (2019, "tabsd"),
        (2019, "sis"),
        (2019, "sis-no-horizon"),
    ]
    assert {item["year"] for item in selected[4:]} == {2020}


def test_netcdf_inspection_checks_time_and_finite_endpoint_data(tmp_path):
    path = tmp_path / "reference.nc"
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("time", 4)
        dataset.createDimension("y", 2)
        dataset.createDimension("x", 2)
        time = dataset.createVariable("time", "f8", ("time",))
        time.standard_name = "time"
        time.units = "hours since 2020-07-01 00:00:00"
        time[:] = [0, 1, 2, 3]
        field = dataset.createVariable(
            "reference_field", "f4", ("time", "y", "x")
        )
        field[:] = np.arange(16, dtype=np.float32).reshape(4, 2, 2)

    report = MODULE.inspect_netcdf(path, minimum_time_records=4)

    assert report["time_record_count"] == 4
    assert report["sampled_data_variable"] == "reference_field"
    assert report["endpoint_samples"][0]["finite_count"] == 4
    assert report["endpoint_samples"][1]["maximum"] == 15.0
