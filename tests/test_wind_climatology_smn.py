import json
from pathlib import Path
import subprocess
import sys

import netCDF4
import numpy as np

from case_studies.swiss_200m.validation.compare_hicar_rea_l_to_smn import (
    OBSERVATION_PARAMETERS,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "validation"
    / "compare_wind_climatology_to_smn.py"
)
VALIDATOR = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "validation"
    / "validate_wind_climatology_output.py"
)
COMPACTOR = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "validation"
    / "compact_wind_climatology_output.py"
)


def write_static(path: Path) -> tuple[float, float]:
    y, x = np.mgrid[:5, :5]
    latitude = 46.0 + y * 0.001
    longitude = 7.0 + x * 0.001 / np.cos(np.deg2rad(46.0))
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("y", 5)
        dataset.createDimension("x", 5)
        dataset.hicar_dx_m = 100.0
        for name, values in (
            ("lat", latitude),
            ("lon", longitude),
            ("topo", np.full((5, 5), 500.0)),
            ("landmask", np.ones((5, 5))),
        ):
            variable = dataset.createVariable(name, "f8", ("y", "x"))
            variable[:] = values
    return float(latitude[2, 2]), float(longitude[2, 2])


def write_output(path: Path) -> None:
    hourly_surface = (
        "u10m_mean_1h",
        "v10m_mean_1h",
        "wind_speed_10m_mean_1h",
        "wind_speed_10m_10min_max_1h",
    )
    hourly_agl = (
        "u_agl_mean_1h",
        "v_agl_mean_1h",
        "wind_speed_agl_mean_1h",
        "wind_speed_agl_10min_max_1h",
    )
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("time", 2)
        dataset.createDimension("y", 5)
        dataset.createDimension("height_agl", 7)
        dataset.createDimension("x", 5)
        time = dataset.createVariable("time", "f8", ("time",))
        time.units = "seconds since 2020-01-01 00:00:00"
        time.calendar = "standard"
        time[:] = (0.0, 3600.0)
        height = dataset.createVariable("height_agl", "f8", ("height_agl",))
        height[:] = (50, 75, 100, 125, 150, 200, 250)
        for name in hourly_surface:
            variable = dataset.createVariable(
                name, "f8", ("time", "y", "x"), fill_value=-9999.0
            )
            variable.cell_methods = (
                "time: maximum (interval: 10 minutes)"
                if "10min_max" in name
                else "time: mean (interval: 1 hour)"
            )
            variable[0] = -9999.0
            variable[1] = {
                "u10m_mean_1h": 5.0,
                "v10m_mean_1h": 0.0,
                "wind_speed_10m_mean_1h": 5.0,
                "wind_speed_10m_10min_max_1h": 6.0,
            }[name]
        for name in hourly_agl:
            variable = dataset.createVariable(
                name,
                "f8",
                ("time", "y", "height_agl", "x"),
                fill_value=-9999.0,
            )
            variable.cell_methods = (
                "time: maximum (interval: 10 minutes)"
                if "10min_max" in name
                else "time: mean (interval: 1 hour)"
            )
            variable[0] = -9999.0
            variable[1] = {
                "u_agl_mean_1h": 5.0,
                "v_agl_mean_1h": 0.0,
                "wind_speed_agl_mean_1h": 5.0,
                "wind_speed_agl_10min_max_1h": 6.0,
            }[name]


def write_observations(path: Path, latitude: float, longitude: float) -> None:
    header = ["meas_site", "termin", "latitude", "longitude", "elev", "nat_abbr"]
    row = ["10", "20200101010000", str(latitude), str(longitude), "500", "TST"]
    for parameter in OBSERVATION_PARAMETERS:
        header.extend([parameter, "pi", "mi", "dq", "uc"])
        value = {
            "fkl010h0": "5.0",
            "dkl010h0": "270.0",
        }.get(parameter, "")
        row.extend([value, "", "", "4" if value else "", ""])
    path.write_text(";".join(header) + "\n" + ";".join(row) + "\n")


def test_hourly_wind_climatology_verifies_against_smn_and_skips_cold_start(
    tmp_path: Path,
) -> None:
    static = tmp_path / "static.nc"
    output = tmp_path / "output.nc"
    observations = tmp_path / "observations.csv"
    report = tmp_path / "report.json"
    latitude, longitude = write_static(static)
    write_output(output)
    write_observations(observations, latitude, longitude)

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--static-file",
            str(static),
            "--output-file",
            str(output),
            "--observations",
            str(observations),
            "--report",
            str(report),
        ],
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(report.read_text())
    assert result["status"] == "pass"
    assert result["pair_accounting"] == {
        "accepted_pair_count": 1,
        "candidate_station_time_count": 1,
        "cold_start_or_partial_hour_record_count": 1,
    }
    statistics = result["statistics"]
    assert statistics["scalar_mean_speed_m_s"]["bias"] == 0.0
    assert statistics["direction_degrees"]["circular_bias_degrees"] == 0.0
    maximum = statistics["maximum_of_ten_minute_means_m_s"]
    assert maximum["mean"] == 6.0
    assert maximum["ge_hourly_scalar_mean_count"] == 1


def test_wind_climatology_output_contract_and_restart_are_bitwise(tmp_path: Path) -> None:
    output = tmp_path / "output.nc"
    restarted = tmp_path / "restarted.nc"
    report = tmp_path / "validation.json"
    write_output(output)
    write_output(restarted)

    completed = subprocess.run(
        [
            sys.executable,
            str(VALIDATOR),
            "--continuous",
            str(output),
            "--restarted",
            str(restarted),
            "--report",
            str(report),
        ],
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(report.read_text())
    assert result["status"] == "pass"
    assert all(result["restart_comparison"]["bitwise_equal"].values())
    assert result["continuous"]["invariants"]["10m"] == {
        "finite_count": 25,
        "scalar_mean_below_vector_mean_count": 0,
        "ten_minute_max_below_hourly_mean_count": 0,
    }


def test_compacted_wind_climatology_is_lossless_and_published_atomically(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.nc"
    compacted = tmp_path / "compacted.nc"
    report = tmp_path / "compression.json"
    write_output(source)

    completed = subprocess.run(
        [
            sys.executable,
            str(COMPACTOR),
            str(source),
            str(compacted),
            "--report",
            str(report),
        ],
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(report.read_text())
    assert result["status"] == "pass"
    assert result["deflate_level"] == 1
    assert result["shuffle"] is True
    assert compacted.is_file()
    assert not (tmp_path / ".compacted.nc.partial").exists()
