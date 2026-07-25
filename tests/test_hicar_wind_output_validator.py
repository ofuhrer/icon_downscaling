from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest

import netCDF4
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_SPEC = importlib.util.spec_from_file_location(
    "validate_hicar_wind_output",
    ROOT / "scripts" / "validate_hicar_wind_output.py",
)
VALIDATOR = importlib.util.module_from_spec(VALIDATOR_SPEC)
assert VALIDATOR_SPEC.loader is not None
VALIDATOR_SPEC.loader.exec_module(VALIDATOR)


def write_output(path: Path, *, invalid: str | None = None) -> None:
    nt, nz, ny, nx = 2, 4, 3, 5
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("time", nt)
        dataset.createDimension("level", nz)
        dataset.createDimension("lat_y", ny)
        dataset.createDimension("lon_x", nx)
        dataset.createDimension("lat_v", ny + 1)
        dataset.createDimension("lon_u", nx + 1)

        time = dataset.createVariable("time", "f8", ("time",))
        time[:] = [0.0, 1.0]
        time.units = "hours since 2000-01-01 00:00:00"

        z_levels = 500.0 + 100.0 * np.arange(nz, dtype=np.float32)
        z = dataset.createVariable("z", "f4", ("level", "lat_y", "lon_x"))
        z[:, :, :] = z_levels[:, None, None]
        jacobian = dataset.createVariable(
            "jacobian", "f4", ("level", "lat_y", "lon_x")
        )
        jacobian[:, :, :] = 1.0

        mass_dims = ("time", "level", "lat_y", "lon_x")
        fields = {
            "w": 0.2,
            "w_grid": 0.25,
            "pressure": np.linspace(90_000.0, 60_000.0, nz, dtype=np.float32)[
                None, :, None, None
            ],
            "temperature": 275.0,
            "potential_temperature": 300.0,
            "qv": 0.005,
            "density": 1.0,
            "wind_alpha": 1.0,
        }
        for name, values in fields.items():
            variable = dataset.createVariable(name, "f4", mass_dims)
            variable[:, :, :, :] = values

        u = dataset.createVariable(
            "u", "f4", ("time", "level", "lat_y", "lon_u")
        )
        v = dataset.createVariable(
            "v", "f4", ("time", "level", "lat_v", "lon_x")
        )
        u[:, :, :, :] = 8.0
        v[:, :, :, :] = -3.0
        precipitation = dataset.createVariable(
            "precipitation", "f4", ("time", "lat_y", "lon_x")
        )
        precipitation[:, :, :] = 2.0

        if invalid == "nonfinite":
            dataset.variables["w"][0, 0, 0, 0] = np.nan
        elif invalid == "geometry":
            dataset.variables["z"][2, 0, 0] = 550.0
        elif invalid == "wind":
            dataset.variables["u"][0, 0, 0, 0] = 500.0


class HicarWindOutputValidatorTests(unittest.TestCase):
    def test_valid_output_passes_chunked_checks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "valid.nc"
            write_output(path)
            report = VALIDATOR.validate_file(path, expected_levels=4, max_elements=17)
            self.assertEqual(report["status"], "PASS")
            self.assertEqual(report["variables"]["u"]["nonfinite"], 0)
            self.assertGreater(report["z_vertical_difference"]["minimum"], 0.0)
            self.assertLess(report["pressure_vertical_difference"]["maximum"], 0.0)

    def test_nonfinite_output_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nonfinite.nc"
            write_output(path, invalid="nonfinite")
            report = VALIDATOR.validate_file(path, expected_levels=4, max_elements=19)
            self.assertEqual(report["status"], "FAIL")
            self.assertTrue(any("w contains" in failure for failure in report["failures"]))

    def test_nonmonotone_geometry_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "geometry.nc"
            write_output(path, invalid="geometry")
            report = VALIDATOR.validate_file(path, expected_levels=4, max_elements=23)
            self.assertEqual(report["status"], "FAIL")
            self.assertTrue(any("z is not" in failure for failure in report["failures"]))

    def test_implausible_wind_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wind.nc"
            write_output(path, invalid="wind")
            report = VALIDATOR.validate_file(path, expected_levels=4, max_elements=29)
            self.assertEqual(report["status"], "FAIL")
            self.assertTrue(any("u range" in failure for failure in report["failures"]))

    def test_unexpected_time_count_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "time.nc"
            write_output(path)
            report = VALIDATOR.validate_file(
                path,
                expected_levels=4,
                expected_times=3,
                max_elements=31,
            )
            self.assertEqual(report["status"], "FAIL")
            self.assertTrue(
                any("time dimension is 2, expected 3" in failure for failure in report["failures"])
            )


if __name__ == "__main__":
    unittest.main()
