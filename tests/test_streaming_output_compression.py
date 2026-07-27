from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import netCDF4
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
COMPRESSOR = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "streaming"
    / "compress_output_file.py"
)


def test_lossless_output_compression_is_published_and_idempotent(tmp_path):
    source = tmp_path / "source.nc"
    with netCDF4.Dataset(source, "w") as dataset:
        dataset.createDimension("time", 2)
        dataset.createDimension("y", 8)
        dataset.createDimension("x", 8)
        field = dataset.createVariable("field", "f4", ("time", "y", "x"))
        field.units = "1"
        field[:] = np.arange(128, dtype=np.float32).reshape(2, 8, 8)
    target = tmp_path / "target.nc"
    report = tmp_path / "compression.json"
    command = [
        sys.executable,
        str(COMPRESSOR),
        "--source",
        str(source),
        "--target",
        str(target),
        "--report",
        str(report),
    ]
    first = subprocess.run(command, text=True, capture_output=True)
    assert first.returncode == 0, first.stderr + first.stdout
    payload = json.loads(report.read_text())
    assert payload["status"] == "PASS"
    assert target.is_file()
    assert Path(f"{target}.ready").is_file()
    assert Path(f"{report}.ready").is_file()

    Path(f"{report}.ready").unlink()
    second = subprocess.run(command, text=True, capture_output=True)
    assert second.returncode == 0, second.stderr + second.stdout
    assert "already published and verified" in second.stdout
    assert Path(f"{report}.ready").is_file()

    Path(f"{target}.ready").unlink()
    Path(f"{report}.ready").unlink()
    recovered = subprocess.run(command, text=True, capture_output=True)
    assert recovered.returncode == 0, recovered.stderr + recovered.stdout
    assert "recovered publication marker" in recovered.stdout
    assert Path(f"{target}.ready").is_file()
    assert Path(f"{report}.ready").is_file()


def test_incomplete_compression_is_quarantined_before_retry(tmp_path):
    source = tmp_path / "source.nc"
    with netCDF4.Dataset(source, "w") as dataset:
        dataset.createDimension("time", 1)
        field = dataset.createVariable("field", "f4", ("time",))
        field[:] = np.array([1], dtype=np.float32)
    target = tmp_path / "target.nc"
    target.write_bytes(b"incomplete")
    report = tmp_path / "compression.json"
    command = [
        sys.executable,
        str(COMPRESSOR),
        "--source",
        str(source),
        "--target",
        str(target),
        "--report",
        str(report),
    ]
    result = subprocess.run(command, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr + result.stdout
    assert Path(f"{target}.ready").is_file()
    quarantined = list((tmp_path / "recovery").glob("target.nc.*/target.nc"))
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == b"incomplete"
