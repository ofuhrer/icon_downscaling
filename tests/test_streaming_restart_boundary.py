from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import netCDF4


ROOT = Path(__file__).resolve().parents[1]
COMPARATOR = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "streaming"
    / "compare_restart_boundary.py"
)


def write_output(path: Path, hours: list[int], values: list[float]) -> None:
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("time", len(hours))
        dataset.createDimension("y", 1)
        dataset.createDimension("x", 1)
        time = dataset.createVariable("time", "f8", ("time",))
        time.units = "hours since 2020-01-01 00:00:00"
        time[:] = hours
        variable = dataset.createVariable("psfc", "f4", ("time", "y", "x"))
        variable[:, 0, 0] = values


def test_restart_boundary_comparator_accepts_matching_record(tmp_path):
    before = tmp_path / "before.nc"
    after = tmp_path / "after.nc"
    write_output(before, [0, 1, 2], [10.0, 11.0, 12.0])
    write_output(after, [2, 3], [12.0, 13.0])
    report = tmp_path / "comparison.json"
    result = subprocess.run(
        [
            sys.executable,
            str(COMPARATOR),
            "--before",
            str(before),
            "--after",
            str(after),
            "--boundary",
            "2020-01-01T02:00:00",
            "--variable",
            "psfc",
            "--report",
            str(report),
        ],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(report.read_text())
    assert payload["status"] == "PASS"
    assert payload["variables"]["psfc"]["bitwise_equal"]
    assert Path(f"{report}.ready").is_file()


def test_restart_boundary_comparator_rejects_changed_record(tmp_path):
    before = tmp_path / "before.nc"
    after = tmp_path / "after.nc"
    write_output(before, [2], [12.0])
    write_output(after, [2], [12.5])
    report = tmp_path / "comparison.json"
    result = subprocess.run(
        [
            sys.executable,
            str(COMPARATOR),
            "--before",
            str(before),
            "--after",
            str(after),
            "--boundary",
            "2020-01-01T02:00:00",
            "--variable",
            "psfc",
            "--report",
            str(report),
        ],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 1
    payload = json.loads(report.read_text())
    assert payload["status"] == "FAIL"
    assert payload["variables"]["psfc"]["tolerance_failure_count"] == 1
