from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import netCDF4
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = (
    ROOT
    / "case_studies"
    / "swiss_100m"
    / "validation"
    / "validate_forcing.py"
)


def write_static(path: Path) -> None:
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("y", 2)
        dataset.createDimension("x", 3)
        lat = dataset.createVariable("lat", "f8", ("y",))
        lon = dataset.createVariable("lon", "f8", ("x",))
        lat[:] = [46.0, 47.0]
        lon[:] = [7.0, 8.0, 9.0]


def write_forcing(path: Path, *, invalid_hfl: bool = False) -> None:
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("time", 1)
        dataset.createDimension("z", 2)
        dataset.createDimension("z_hl", 3)
        dataset.createDimension("y_1", 2)
        dataset.createDimension("x_1", 3)
        time = dataset.createVariable("time", "f8", ("time",))
        time.units = "minutes since 2010-01-01 00:00:00"
        time[:] = [180.0]
        lat = dataset.createVariable("lat_1", "f8", ("y_1",))
        lon = dataset.createVariable("lon_1", "f8", ("x_1",))
        lat[:] = [45.5, 47.5]
        lon[:] = [6.5, 8.0, 9.5]

        full_dims = ("time", "z", "y_1", "x_1")
        half_dims = ("time", "z_hl", "y_1", "x_1")
        values = {
            "P": 80_000.0,
            "QV": 0.004,
            "T": 270.0,
            "U": 8.0,
            "V": -2.0,
        }
        for name, value in values.items():
            variable = dataset.createVariable(name, "f4", full_dims)
            variable[:] = value
        w = dataset.createVariable("W", "f4", half_dims)
        w[:] = 0.1

        hhl = dataset.createVariable("HHL", "f4", ("z_hl", "y_1", "x_1"))
        hhl[:] = np.array([500.0, 600.0, 800.0], dtype=np.float32)[:, None, None]
        hfl = dataset.createVariable("HFL", "f4", ("z", "y_1", "x_1"))
        hfl_values = np.array([550.0, 700.0], dtype=np.float32)
        if invalid_hfl:
            hfl_values[1] = 710.0
        hfl[:] = hfl_values[:, None, None]
        hsurf = dataset.createVariable("HSURF", "f4", ("y_1", "x_1"))
        land = dataset.createVariable("FR_LAND", "f4", ("y_1", "x_1"))
        hsurf[:] = 500.0
        land[:] = 0.75


class ReaLForcingValidatorTests(unittest.TestCase):
    def run_validator(
        self,
        forcing: Path,
        static: Path,
        expected: str,
        report: Path,
        published: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = [
                sys.executable,
                str(VALIDATOR),
                "--forcing-file",
                str(forcing),
                "--static-file",
                str(static),
                "--expected-valid-time",
                expected,
                "--report",
                str(report),
            ]
        if published is not None:
            command.extend(["--published-forcing-file", str(published)])
        return subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def test_valid_record_passes_and_publishes_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            forcing = root / "forcing.nc"
            static = root / "static.nc"
            report = root / "report.json"
            write_forcing(forcing)
            write_static(static)
            result = self.run_validator(
                forcing,
                static,
                "2010-01-01T03:00:00",
                report,
                published=root / "published.nc",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(report.is_file())
            self.assertIn('"status": "PASS"', report.read_text())
            self.assertIn(str(root / "published.nc"), report.read_text())

    def test_wrong_valid_time_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            forcing = root / "forcing.nc"
            static = root / "static.nc"
            write_forcing(forcing)
            write_static(static)
            result = self.run_validator(
                forcing, static, "2010-01-01T04:00:00", root / "report.json"
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("does not match", result.stderr)

    def test_inconsistent_full_level_height_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            forcing = root / "forcing.nc"
            static = root / "static.nc"
            write_forcing(forcing, invalid_hfl=True)
            write_static(static)
            result = self.run_validator(
                forcing, static, "2010-01-01T03:00:00", root / "report.json"
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("HFL is inconsistent", result.stderr)


if __name__ == "__main__":
    unittest.main()
