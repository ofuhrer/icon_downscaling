from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys

import netCDF4
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "validation"
    / "validate_restart_checkpoint.py"
)

DIMENSIONS = {
    "lon_x": 2,
    "lon_u": 3,
    "lat_y": 2,
    "lat_v": 3,
    "level": 80,
    "level_i": 81,
    "time": 1,
    "nsoil": 4,
}

VARIABLE_DIMENSIONS = {
    "u": ("time", "level", "lat_y", "lon_u"),
    "v": ("time", "level", "lat_v", "lon_x"),
    "w": ("time", "level_i", "lat_y", "lon_x"),
    "pressure": ("time", "level", "lat_y", "lon_x"),
    "potential_temperature": ("time", "level", "lat_y", "lon_x"),
    "qv": ("time", "level", "lat_y", "lon_x"),
    "qc": ("time", "level", "lat_y", "lon_x"),
    "qr": ("time", "level", "lat_y", "lon_x"),
    "qi": ("time", "level", "lat_y", "lon_x"),
    "qs": ("time", "level", "lat_y", "lon_x"),
    "qg": ("time", "level", "lat_y", "lon_x"),
    "soil_temperature": ("time", "nsoil", "lat_y", "lon_x"),
    "soil_water_content": ("time", "nsoil", "lat_y", "lon_x"),
    "snow_height": ("time", "lat_y", "lon_x"),
    "canopy_water": ("time", "lat_y", "lon_x"),
    "precipitation": ("time", "lat_y", "lon_x"),
}


def checkpoint(path: Path, timestamp: str = "2020-07-02 00:00:00") -> None:
    with netCDF4.Dataset(path, "w") as dataset:
        for name, size in DIMENSIONS.items():
            dataset.createDimension(name, size)
        time = dataset.createVariable("time", "f8", ("time",))
        time.units = "seconds since 2020-07-01 00:00:00"
        time.calendar = "proleptic_gregorian"
        time[:] = netCDF4.date2num(
            datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S"),
            time.units,
            time.calendar,
        )
        for name, dimensions in VARIABLE_DIMENSIONS.items():
            dataset.createVariable(name, "f4", dimensions)
        dataset.dt_seconds = np.float32(4.0)
        dataset.git = "feature/test-0-g2ea31109"
        dataset.git_tag = "2ea31109"


def run(path: Path, report: Path, expected: str = "2020-07-02T00:00:00"):
    return subprocess.run(
        [
            sys.executable,
            str(VALIDATOR),
            "--checkpoint",
            str(path),
            "--expected-time",
            expected,
            "--expected-source-commit",
            "2ea31109801a2477a946840693934318f8d50c95",
            "--report",
            str(report),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_restart_checkpoint_validator_publishes_matching_inventory(tmp_path: Path):
    source = tmp_path / "restart.nc"
    report = tmp_path / "report.json"
    checkpoint(source)

    completed = run(source, report)

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(report.read_text())
    assert payload["status"] == "PASS"
    assert payload["checkpoint_time"] == "2020-07-02T00:00:00"
    assert payload["variable_count"] == 17
    assert payload["sha256"]
    assert Path(f"{report}.ready").is_file()


def test_restart_checkpoint_validator_rejects_wrong_time(tmp_path: Path):
    source = tmp_path / "restart.nc"
    report = tmp_path / "report.json"
    checkpoint(source, "2020-07-02 01:00:00")

    completed = run(source, report)

    assert completed.returncode == 1
    payload = json.loads(report.read_text())
    assert payload["status"] == "FAIL"
    assert any("is not expected" in failure for failure in payload["failures"])
    assert not Path(f"{report}.ready").exists()
