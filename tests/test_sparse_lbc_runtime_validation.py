from __future__ import annotations

import pathlib
import tempfile
import unittest

import netCDF4
import numpy as np

from case_studies.swiss_200m.validation.validate_sparse_lbc_runtime_output import (
    FIELD_BOUNDS,
    validate,
)


class SparseLbcRuntimeValidationTests(unittest.TestCase):
    def _write_case(self, root: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
        output = root / "runtime.nc"
        with netCDF4.Dataset(output, "w") as dataset:
            dataset.createDimension("time", 3)
            dataset.createDimension("level", 1)
            dataset.createDimension("y", 1)
            dataset.createDimension("x", 1)
            time = dataset.createVariable("time", "f8", ("time",))
            time.units = "days since 2020-02-10 00:00:00"
            time.calendar = "proleptic_gregorian"
            time[:] = np.asarray([0.0, 600.0, 1200.0]) / 86400.0
            for name, (lower, upper) in FIELD_BOUNDS.items():
                variable = dataset.createVariable(
                    name, "f4", ("time", "level", "y", "x")
                )
                variable[:] = lower + 0.25 * (upper - lower)
            dataset.git = "test-commit"
        model_log = root / "model.out"
        model_log.write_text(
            "Sparse target-grid LBC runtime enabled\n"
            "Sparse LBC bracket advanced: left= 2 right= 3\n"
        )
        return output, model_log

    def test_valid_chunked_runtime_output_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output, model_log = self._write_case(pathlib.Path(temporary))
            report = validate(output, model_log, 3, 600.0)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["hicar_git_commit"], "test-commit")
        self.assertEqual(set(report["variables"]), set(FIELD_BOUNDS))

    def test_submillisecond_calendar_noise_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output, model_log = self._write_case(pathlib.Path(temporary))
            with netCDF4.Dataset(output, "a") as dataset:
                dataset["time"][:] += np.asarray([0.0, 4.0e-6, -3.5e-5]) / 86400.0
            report = validate(output, model_log, 3, 600.0)
        self.assertEqual(report["status"], "PASS")

    def test_gross_wind_failure_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output, model_log = self._write_case(pathlib.Path(temporary))
            with netCDF4.Dataset(output, "a") as dataset:
                dataset["u"][1, 0, 0, 0] = 201.0
            with self.assertRaisesRegex(ValueError, "u range"):
                validate(output, model_log, 3, 600.0)


if __name__ == "__main__":
    unittest.main()
