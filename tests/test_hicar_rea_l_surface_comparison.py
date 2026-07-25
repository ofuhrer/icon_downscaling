import json
from pathlib import Path
import subprocess
import sys

import netCDF4
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
COMPARATOR = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "validation"
    / "compare_hicar_to_rea_l_surface.py"
)


def write_static(path: Path) -> None:
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("y", 2)
        dataset.createDimension("x", 2)
        dataset.hicar_dx_m = 200.0
        dataset.createVariable("lat", "f8", ("y", "x"))[:] = [
            [46.0, 46.0],
            [46.1, 46.1],
        ]
        dataset.createVariable("lon", "f8", ("y", "x"))[:] = [
            [7.0, 7.1],
            [7.0, 7.1],
        ]
        dataset.createVariable("topo", "f4", ("y", "x"))[:] = 500.0
        dataset.createVariable("landmask", "i2", ("y", "x"))[:] = 1
        dataset.createVariable("landuse", "i2", ("y", "x"))[:] = 7


def write_reference(path: Path, hour: int, precipitation: float) -> None:
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("time", 1)
        dataset.createDimension("latitude", 2)
        dataset.createDimension("longitude", 2)
        time = dataset.createVariable("time", "f8", ("time",))
        time.units = "hours since 2020-07-01 00:00:00"
        time[:] = hour
        dataset.createVariable("latitude", "f8", ("latitude",))[:] = [46.0, 46.1]
        dataset.createVariable("longitude", "f8", ("longitude",))[:] = [7.0, 7.1]
        values = {
            "ta2m_ref": 280.0,
            "hus2m_ref": 0.005,
            "psfc_ref": 90_000.0,
            "u10m_ref": 3.0,
            "v10m_ref": 4.0,
            "snow_height_ref": 0.2,
            "swe_ref": 40.0,
            "precipitation_interval_ref": precipitation,
            "source_terrain": 500.0,
        }
        for name, value in values.items():
            dataset.createVariable(
                name, "f4", ("time", "latitude", "longitude")
            )[:] = value


def write_output(path: Path) -> None:
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("time", 2)
        dataset.createDimension("y", 2)
        dataset.createDimension("x", 2)
        time = dataset.createVariable("time", "f8", ("time",))
        time.units = "hours since 2020-07-01 00:00:00"
        encoded_offset_hours = 0.432 / 3600.0
        time[:] = [encoded_offset_hours, 3 + encoded_offset_hours]
        values = {
            "taix": [280.0, 280.0],
            "hus2m": [0.005, 0.005],
            "psfc": [90_000.0, 90_000.0],
            "u10m": [3.0, 3.0],
            "v10m": [4.0, 4.0],
            "snow_height": [0.2, 0.2],
            "swet": [40.0, 40.0],
            "precipitation": [0.0, 3.0],
        }
        for name, series in values.items():
            dataset.createVariable(name, "f4", ("time", "y", "x"))[:] = (
                np.asarray(series)[:, None, None]
            )


def test_source_comparison_reports_exact_synthetic_match(tmp_path):
    static = tmp_path / "static.nc"
    output = tmp_path / "output.nc"
    reference0 = tmp_path / "reference0.nc"
    reference3 = tmp_path / "reference3.nc"
    reference_list = tmp_path / "reference_list.txt"
    report_path = tmp_path / "report.json"
    write_static(static)
    write_output(output)
    write_reference(reference0, 0, 0.0)
    write_reference(reference3, 3, 3.0)
    reference_list.write_text(f'"{reference0}"\n"{reference3}"\n')
    result = subprocess.run(
        [
            sys.executable,
            str(COMPARATOR),
            "--event-name",
            "synthetic",
            "--static-file",
            str(static),
            "--output-file",
            str(output),
            "--reference-list",
            str(reference_list),
            "--report",
            str(report_path),
        ],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    report = json.loads(report_path.read_text())
    assert report["status"] == "PASS"
    assert Path(f"{report_path}.ready").is_file()
    metrics = report["metrics"]["active_soil_all"]
    assert metrics["temperature_2m_height_adjusted_k"]["bias"] == 0.0
    assert metrics["wind_speed_10m_m_s"]["root_mean_squared_error"] == 0.0
    assert metrics["precipitation_interval_kg_m2"]["bias"] == 0.0
