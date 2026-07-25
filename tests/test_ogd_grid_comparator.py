import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import netCDF4
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "validation"
    / "compare_hicar_rea_l_to_ogd_grids.py"
)
SPEC = importlib.util.spec_from_file_location("ogd_grid_comparator", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_polynomial_inverse_recovers_local_coordinates():
    x = np.arange(0.0, 20_000.0, 200.0)
    y = np.arange(0.0, 18_000.0, 200.0)
    xx, yy = np.meshgrid(x, y)
    latitude = 46.0 + yy / 111_000.0 + xx / 12_000_000.0
    longitude = 8.0 + xx / 76_000.0 - yy / 15_000_000.0

    transform = MODULE.fit_local_coordinates(latitude, longitude, x, y)
    recovered_x, recovered_y = MODULE.local_coordinates(transform, latitude, longitude)

    error = np.hypot(recovered_x - xx, recovered_y - yy)
    assert np.max(error) < 5.0
    assert transform["maximum_verification_error_m"] < 5.0


def test_regular_bilinear_handles_descending_coordinates_and_outside_points():
    latitude = np.array([2.0, 1.0, 0.0])
    longitude = np.array([3.0, 2.0, 1.0, 0.0])
    lon_grid, lat_grid = np.meshgrid(longitude, latitude)
    values = 10.0 * lat_grid + lon_grid
    target_latitude = np.array([0.5, 1.5, 3.0])
    target_longitude = np.array([0.5, 2.5, 1.0])

    setup = MODULE.regular_bilinear_setup(
        latitude,
        longitude,
        target_latitude,
        target_longitude,
    )
    interpolated = MODULE.regular_bilinear(values, setup)

    np.testing.assert_allclose(interpolated[:2], [5.5, 17.5])
    assert np.isnan(interpolated[2])


def test_box_mean_ignores_nonfinite_cells_and_domain_edges():
    values = np.arange(25.0).reshape(5, 5)
    values[2, 2] = np.nan
    setup = {
        "x_index": np.array([2, 0], dtype=np.int32),
        "y_index": np.array([2, 0], dtype=np.int32),
        "inside": np.array([True, True]),
    }

    result = MODULE.box_mean(values, setup, half_width_cells=1)

    expected_center = np.nanmean(values[1:4, 1:4])
    expected_corner = np.mean(values[0:2, 0:2])
    np.testing.assert_allclose(result, [expected_center, expected_corner])


def test_statistics_reports_expected_error_metrics():
    statistics = MODULE.Statistics()
    statistics.add(
        np.array([1.0, 4.0, np.nan]),
        np.array([2.0, 3.0, 4.0]),
        np.array([True, True, True]),
    )

    result = statistics.result()

    assert result["count"] == 2
    assert result["bias"] == 0.0
    assert result["mean_absolute_error"] == 1.0
    assert result["root_mean_squared_error"] == 1.0
    assert result["correlation"] == 1.0


def write_time(variable, hours):
    variable.units = "hours since 2020-07-01 00:00:00"
    variable.calendar = "standard"
    variable[:] = hours


def test_end_to_end_event_matches_precipitation_temperature_and_radiation(tmp_path):
    static_path = tmp_path / "static.nc"
    x = np.arange(100, dtype=np.float64) * 200.0
    y = np.arange(100, dtype=np.float64) * 200.0
    xx, yy = np.meshgrid(x, y)
    latitude = 46.0 + yy / 111_000.0 + xx / 12_000_000.0
    longitude = 8.0 + xx / 76_000.0 - yy / 15_000_000.0
    with netCDF4.Dataset(static_path, "w") as dataset:
        dataset.createDimension("x", len(x))
        dataset.createDimension("y", len(y))
        dataset.createVariable("x", "f8", ("x",))[:] = x
        dataset.createVariable("y", "f8", ("y",))[:] = y
        dataset.createVariable("lat", "f8", ("y", "x"))[:] = latitude
        dataset.createVariable("lon", "f8", ("y", "x"))[:] = longitude
        dataset.createVariable("topo", "f4", ("y", "x"))[:] = 1200.0

    output_path = tmp_path / "output.nc"
    output_hours = np.arange(0.0, 34.0, 3.0)
    with netCDF4.Dataset(output_path, "w") as dataset:
        dataset.createDimension("time", len(output_hours))
        dataset.createDimension("y", len(y))
        dataset.createDimension("x", len(x))
        time = dataset.createVariable("time", "f8", ("time",))
        write_time(time, output_hours)
        precipitation = dataset.createVariable(
            "precipitation", "f4", ("time", "y", "x")
        )
        precipitation[:] = np.arange(len(output_hours))[:, None, None]
        temperature = dataset.createVariable("taix", "f4", ("time", "y", "x"))
        temperature[:] = 280.0
        radiation = dataset.createVariable("rsds", "f4", ("time", "y", "x"))
        radiation[:] = 100.0

    target_indices = np.array([25, 50, 75])
    target_latitude = latitude[np.ix_(target_indices, target_indices)]
    target_longitude = longitude[np.ix_(target_indices, target_indices)]
    rhires_path = tmp_path / "rhires.nc"
    with netCDF4.Dataset(rhires_path, "w") as dataset:
        dataset.createDimension("time", 1)
        dataset.createDimension("N", 3)
        dataset.createDimension("E", 3)
        time = dataset.createVariable("time", "f8", ("time",))
        write_time(time, [0.0])
        dataset.createVariable("lat", "f8", ("N", "E"))[:] = target_latitude
        dataset.createVariable("lon", "f8", ("N", "E"))[:] = target_longitude
        dataset.createVariable("RhiresD", "f4", ("time", "N", "E"))[:] = 8.0

    tabsd_path = tmp_path / "tabsd.nc"
    with netCDF4.Dataset(tabsd_path, "w") as dataset:
        dataset.createDimension("time", 1)
        dataset.createDimension("N", 3)
        dataset.createDimension("E", 3)
        time = dataset.createVariable("time", "f8", ("time",))
        write_time(time, [0.0])
        dataset.createVariable("lat", "f8", ("N", "E"))[:] = target_latitude
        dataset.createVariable("lon", "f8", ("N", "E"))[:] = target_longitude
        dataset.createVariable("TabsD", "f4", ("time", "N", "E"))[:] = 6.85

    sis_latitude = latitude[target_indices, 50]
    sis_longitude = longitude[50, target_indices]
    sis_paths = {}
    for product, variable_name in (
        ("sis", "SIS"),
        ("sis-no-horizon", "SIS-No-Horizon"),
    ):
        path = tmp_path / f"{product}.nc"
        sis_paths[product] = path
        with netCDF4.Dataset(path, "w") as dataset:
            dataset.createDimension("time", len(output_hours) - 2)
            dataset.createDimension("lat", 3)
            dataset.createDimension("lon", 3)
            time = dataset.createVariable("time", "f8", ("time",))
            write_time(time, output_hours[1:-1])
            dataset.createVariable("lat", "f8", ("lat",))[:] = sis_latitude
            dataset.createVariable("lon", "f8", ("lon",))[:] = sis_longitude
            dataset.createVariable(variable_name, "f4", ("time", "lat", "lon"))[:] = (
                100.0
            )

    reference_paths = []
    reference_latitude = np.linspace(
        float(np.min(latitude)), float(np.max(latitude)), 12
    )
    reference_longitude = np.linspace(
        float(np.min(longitude)), float(np.max(longitude)), 12
    )
    for hour in range(0, 31, 3):
        path = tmp_path / f"reference_{hour:02d}.nc"
        reference_paths.append(path)
        with netCDF4.Dataset(path, "w") as dataset:
            dataset.createDimension("time", 1)
            dataset.createDimension("latitude", 12)
            dataset.createDimension("longitude", 12)
            time = dataset.createVariable("time", "f8", ("time",))
            write_time(time, [float(hour)])
            dataset.createVariable("latitude", "f8", ("latitude",))[:] = (
                reference_latitude
            )
            dataset.createVariable("longitude", "f8", ("longitude",))[:] = (
                reference_longitude
            )
            dataset.createVariable(
                "precipitation_interval_ref",
                "f4",
                ("time", "latitude", "longitude"),
            )[:] = 1.0
            dataset.createVariable(
                "ta2m_ref",
                "f4",
                ("time", "latitude", "longitude"),
            )[:] = 280.0
    reference_list = tmp_path / "reference_list.txt"
    reference_list.write_text("".join(f'"{path}"\n' for path in reference_paths))

    manifest_path = tmp_path / "ogd_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "status": "PASS",
                "assets": [
                    {"product": "rhiresd", "path": str(rhires_path)},
                    {"product": "tabsd", "path": str(tabsd_path)},
                    {
                        "product": "sis",
                        "month": 7,
                        "path": str(sis_paths["sis"]),
                    },
                    {
                        "product": "sis-no-horizon",
                        "month": 7,
                        "path": str(sis_paths["sis-no-horizon"]),
                    },
                ],
            }
        )
    )
    report_path = tmp_path / "report.json"
    result = subprocess.run(
        [
            sys.executable,
            str(MODULE_PATH),
            "--event-name",
            "synthetic",
            "--static-file",
            str(static_path),
            "--output-file",
            str(output_path),
            "--reference-list",
            str(reference_list),
            "--ogd-manifest",
            str(manifest_path),
            "--report",
            str(report_path),
            "--boundary-width-m",
            "1000",
            "--minimum-pairs",
            "1",
        ],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    report = json.loads(report_path.read_text())
    assert report["status"] == "PASS"
    assert len(report["matched_daily_windows"]) == 1
    assert len(report["matched_temperature_days"]) == 1
    assert len(report["matched_radiation_times"]) == 10
    assert (
        report["seasonal_metrics"]["JJA"]["rhiresd"]["hicar"][
            "interior_ge_10km"
        ]["count"]
        > 0
    )
    assert (
        report["seasonal_metrics"]["DJF"]["rhiresd"]["hicar"][
            "interior_ge_10km"
        ]["count"]
        == 0
    )
    assert report["skipped_radiation_times_outside_reference"] == [
        "2020-07-02T09:00:00+00:00"
    ]
    assert (
        report["metrics"]["rhiresd"]["hicar"]["interior_ge_10km"][
            "root_mean_squared_error"
        ]
        == 0.0
    )
    assert (
        report["metrics"]["rhiresd"]["rea_l"]["interior_ge_10km"][
            "root_mean_squared_error"
        ]
        == 0.0
    )
    assert (
        abs(
            report["metrics"]["tabsd"]["hicar"]["interior_ge_10km"][
                "root_mean_squared_error"
            ]
        )
        < 1.0e-5
    )
    assert (
        abs(
            report["metrics"]["tabsd"]["rea_l"]["interior_ge_10km"][
                "root_mean_squared_error"
            ]
        )
        < 1.0e-5
    )
    assert Path(f"{report_path}.ready").is_file()
