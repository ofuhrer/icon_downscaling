import importlib.util
from pathlib import Path

import netCDF4
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "validation"
    / "compare_forcing_numerical.py"
)
SPEC = importlib.util.spec_from_file_location("compare_forcing_numerical", SCRIPT)
COMPARATOR = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(COMPARATOR)


def _write(path: Path, dtype: str, values: np.ndarray) -> None:
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("level", values.shape[0])
        dataset.createDimension("point", values.shape[1])
        dataset.createVariable("T", dtype, ("level", "point"))[:] = values


def test_dtype_change_is_structural_and_reports_runtime_cast_equivalence(
    tmp_path: Path,
) -> None:
    reference = tmp_path / "reference.nc"
    candidate = tmp_path / "candidate.nc"
    values = np.array([[1.0 / 3.0, 90_000.003], [1.0e-12, -7.25]], dtype=np.float64)
    _write(reference, "f8", values)
    _write(candidate, "f4", values.astype(np.float32))

    report = COMPARATOR.compare(reference, candidate)

    assert report["structural_equal"] is True
    assert report["storage_dtype_equal"] is False
    assert report["all_variable_values_bitwise_equal"] is False
    assert report["variables"]["T"]["candidate_equal_to_reference_cast"] is True
    assert report["variables"]["T"]["reference_cast_different_value_count"] == 0
