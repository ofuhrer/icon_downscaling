from __future__ import annotations

from datetime import datetime, timedelta
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
    / "validate_event_restart_checkpoints.py"
)
COMMIT = "2ea31109801a2477a946840693934318f8d50c95"
STATIC_BASENAME = "domain_static_swiss_200m_rea_l_20200701_0000"
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


def write_checkpoint(path: Path, timestamp: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with netCDF4.Dataset(path, "w") as dataset:
        for name, size in DIMENSIONS.items():
            dataset.createDimension(name, size)
        time = dataset.createVariable("time", "f8", ("time",))
        time.units = "seconds since 2020-07-01 00:00:00"
        time.calendar = "proleptic_gregorian"
        time[:] = netCDF4.date2num(timestamp, time.units, time.calendar)
        for name, dimensions in VARIABLE_DIMENSIONS.items():
            dataset.createVariable(name, "f4", dimensions)
        dataset.dt_seconds = np.float32(4.0)
        dataset.git = "feature/test-0-g2ea31109"
        dataset.git_tag = "2ea31109"


def setup_run(tmp_path: Path, omit_hour: int | None = None) -> Path:
    run = tmp_path / "run"
    run.mkdir()
    completion = run / "model_chunk_completion.json"
    completion.write_text(
        json.dumps(
            {
                "status": "PASS",
                "chunk_id": "science_summer_20200701_00_20200704_00",
            }
        )
    )
    Path(f"{completion}.ready").touch()
    start = datetime.fromisoformat("2020-07-01T00:00:00")
    for elapsed in (24, 48, 72):
        if elapsed == omit_hour:
            continue
        timestamp = start + timedelta(hours=elapsed)
        write_checkpoint(
            run
            / "restart"
            / f"{STATIC_BASENAME}_{timestamp:%Y-%m-%d_%H-%M-%S}.nc",
            timestamp,
        )
    return run


def invoke(run: Path, report: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(VALIDATOR),
            "--run-dir",
            str(run),
            "--static-basename",
            STATIC_BASENAME,
            "--start",
            "2020-07-01T00:00:00",
            "--duration-hours",
            "72",
            "--interval-hours",
            "24",
            "--expected-source-commit",
            COMMIT,
            "--report",
            str(report),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_event_restart_validator_publishes_three_boundary_audit(tmp_path):
    run = setup_run(tmp_path)
    report = tmp_path / "event_restarts.json"

    completed = invoke(run, report)

    assert completed.returncode == 0, completed.stderr + completed.stdout
    payload = json.loads(report.read_text())
    assert payload["status"] == "PASS"
    assert payload["checkpoint_count"] == 3
    assert [item["elapsed_hours"] for item in payload["checkpoints"]] == [24, 48, 72]
    assert all(item["status"] == "PASS" for item in payload["checkpoints"])
    assert Path(f"{report}.ready").is_file()


def test_event_restart_validator_rejects_missing_boundary(tmp_path):
    run = setup_run(tmp_path, omit_hour=48)
    report = tmp_path / "event_restarts.json"

    completed = invoke(run, report)

    assert completed.returncode == 1
    payload = json.loads(report.read_text())
    assert payload["status"] == "FAIL"
    assert payload["checkpoints"][1]["status"] == "FAIL"
    assert not Path(f"{report}.ready").exists()
