import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import netCDF4
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "validation"
    / "compare_restart_states_exact.py"
)
SPEC = importlib.util.spec_from_file_location("compare_restart_states_exact", SCRIPT)
COMPARATOR = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(COMPARATOR)


def write_restart(path: Path, *, core_delta: float = 0.0, guard_delta: float = 0.0) -> None:
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("time", 1)
        dataset.createDimension("level", 2)
        dataset.createDimension("lat_y", 10)
        dataset.createDimension("lon_x", 11)
        time = dataset.createVariable("time", "f8", ("time",))
        time[:] = [3600.0]
        state = dataset.createVariable(
            "temperature", "f4", ("time", "level", "lat_y", "lon_x"), fill_value=-999.0
        )
        values = np.arange(220, dtype=np.float32).reshape(1, 2, 10, 11)
        values[..., :3, :] += guard_delta
        values[..., 3:-3, 3:-3] += core_delta
        state[:] = values
        terrain = dataset.createVariable("terrain", "f4", ("lat_y", "lon_x"))
        terrain[:] = 1000.0
        dataset.history = "attributes are outside the restart-state contract"


def test_exact_comparison_excludes_guard_and_reports_coordinate(tmp_path: Path) -> None:
    left = tmp_path / "left.nc"
    right = tmp_path / "right.nc"
    write_restart(left)
    write_restart(right, guard_delta=10.0)

    result = COMPARATOR.compare_restart_files(left, right, max_chunk_bytes=32)

    assert result["bitwise_equal_model_core_state"] is True
    assert result["schema"]["compared_variable_count"] == 2
    assert result["schema"]["excluded_shared_variables"] == {
        "time": "canonical_dimension_coordinate"
    }
    assert result["left"]["sha256"] != ""
    assert result["right"]["path"] == str(right.resolve())

    all_variables = COMPARATOR.compare_restart_files(
        left, right, include_coordinate_variables=True, max_chunk_bytes=32
    )
    assert all_variables["schema"]["compared_variable_count"] == 3
    assert all_variables["schema"]["excluded_shared_variables"] == {}


def test_exact_comparison_finds_core_delta_with_streaming(tmp_path: Path) -> None:
    left = tmp_path / "left.nc"
    right = tmp_path / "right.nc"
    write_restart(left)
    write_restart(right, core_delta=0.25)

    result = COMPARATOR.compare_restart_files(left, right, max_chunk_bytes=32)
    difference = result["differing_variables"]["temperature"]

    assert result["bitwise_equal_model_core_state"] is False
    assert difference["different_elements"] == 40
    assert difference["maximum_absolute_difference"] == 0.25
    assert difference["chunks"] == 2
    assert result["maximum_absolute_difference"] == 0.25


def test_variable_shape_and_dtype_mismatches_are_explicit() -> None:
    class Variable:
        def __init__(self, name: str, shape: tuple[int, ...], dtype: str):
            self.name = name
            self.shape = shape
            self.dtype = np.dtype(dtype)
            self.dimensions = ("time", "lat_y", "lon_x")

    left = Variable("field", (1, 10, 11), "f4")
    wrong_shape = Variable("field", (1, 10, 12), "f4")
    wrong_dtype = Variable("field", (1, 10, 11), "f8")

    assert COMPARATOR.compare_variable_exact(left, wrong_shape)["reason"] == "shape_mismatch"
    assert COMPARATOR.compare_variable_exact(left, wrong_dtype)["reason"] == "dtype_mismatch"


def test_mask_and_float_bits_are_compared_exactly() -> None:
    left = np.ma.array(
        np.array([0.0, 1.0, np.nan, 8.0], dtype=np.float32),
        mask=[False, False, False, True],
    )
    right = np.ma.array(
        np.array([-0.0, 1.0, np.nan, 99.0], dtype=np.float32),
        mask=[False, True, False, True],
    )

    result = COMPARATOR.compare_arrays_exact(left, right)

    assert result["different_elements"] == 2  # signed zero plus differing mask
    assert result["different_payload_elements"] == 1
    assert result["different_mask_elements"] == 1
    assert result["maximum_absolute_difference"] == 0.0


def test_cli_json_and_exit_status(tmp_path: Path) -> None:
    left = tmp_path / "left.nc"
    right = tmp_path / "right.nc"
    report = tmp_path / "comparison.json"
    write_restart(left)
    write_restart(right, core_delta=1.0)

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), str(left), str(right), "--output", str(report)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    payload = json.loads(report.read_text())
    assert payload["bitwise_equal_model_core_state"] is False
    assert payload["comparison_contract"]["schema_strict"] is True
