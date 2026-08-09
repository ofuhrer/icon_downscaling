import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import netCDF4
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "case_studies" / "swiss_200m" / "validation" / "compare_output_slices_exact.py"
SPEC = importlib.util.spec_from_file_location("compare_output_slices_exact", SCRIPT)
COMPARATOR = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(COMPARATOR)


def write_output(
    path: Path,
    *,
    times: tuple[float, ...],
    selected_field_delta: float = 0.0,
    static_delta: float = 0.0,
    add_right_only: bool = False,
) -> None:
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("time", len(times))
        dataset.createDimension("level", 2)
        dataset.createDimension("y", 3)
        dataset.createDimension("x", 5)
        time = dataset.createVariable("time", "f8", ("time",))
        time[:] = times
        state = dataset.createVariable("temperature", "f4", ("time", "level", "y", "x"))
        values = np.arange(len(times) * 30, dtype=np.float32).reshape(len(times), 2, 3, 5)
        # The caller selects index zero from this file in delta tests.
        values[0, ...] += selected_field_delta
        state[:] = values
        static = dataset.createVariable("terrain", "f4", ("y", "x"))
        static[:] = np.arange(15, dtype=np.float32).reshape(3, 5) + static_delta
        scalar = dataset.createVariable("projection_code", "i4")
        scalar.assignValue(7)
        if add_right_only:
            extra = dataset.createVariable("right_only", "f4", ("y", "x"))
            extra[:] = 1.0


def test_independent_time_indices_and_static_variables_are_compared_once(
    tmp_path: Path,
) -> None:
    left = tmp_path / "left.nc"
    right = tmp_path / "right.nc"
    write_output(left, times=(0.0, 3600.0))
    write_output(right, times=(3600.0,))

    # Make right index 0 identical to left index 1, including the time value.
    with netCDF4.Dataset(right, "r+") as dataset:
        dataset["temperature"][0, ...] = np.arange(60, dtype=np.float32).reshape(2, 2, 3, 5)[1, ...]

    result = COMPARATOR.compare_output_slices(
        left,
        right,
        left_time_index=1,
        right_time_index=0,
        max_chunk_bytes=24,
    )

    assert result["bitwise_equal_selected_output_state"] is True
    assert result["left"]["resolved_time_index"] == 1
    assert result["right"]["resolved_time_index"] == 0
    assert result["left"]["encoded_time_value"] == 3600.0
    assert result["right"]["encoded_time_value"] == 3600.0
    assert result["schema"]["compared_variable_count"] == 4
    assert result["left"]["sha256"]
    assert result["comparison_contract"]["non_time_variables"] == "compared_in_full_once"


def test_streaming_reports_time_slice_and_static_differences(tmp_path: Path) -> None:
    left = tmp_path / "left.nc"
    right = tmp_path / "right.nc"
    write_output(left, times=(0.0,))
    write_output(
        right,
        times=(0.0,),
        selected_field_delta=0.25,
        static_delta=2.0,
    )

    result = COMPARATOR.compare_output_slices(
        left, right, left_time_index=0, right_time_index=0, max_chunk_bytes=24
    )

    assert result["bitwise_equal_selected_output_state"] is False
    assert result["differing_variable_count"] == 2
    assert result["maximum_absolute_difference"] == 2.0
    assert result["differing_variables"]["temperature"]["different_elements"] == 30
    assert result["differing_variables"]["temperature"]["maximum_absolute_difference"] == 0.25
    assert result["differing_variables"]["temperature"]["chunks"] > 1
    assert result["differing_variables"]["terrain"]["time_dependent"] is False


def test_missing_variable_is_a_strict_schema_failure(tmp_path: Path) -> None:
    left = tmp_path / "left.nc"
    right = tmp_path / "right.nc"
    write_output(left, times=(0.0,))
    write_output(right, times=(0.0,), add_right_only=True)

    result = COMPARATOR.compare_output_slices(left, right)

    assert result["schema"]["missing_from_left"] == ["right_only"]
    assert result["differing_variable_count"] == 0
    assert result["bitwise_equal_selected_output_state"] is False


def test_selected_shape_and_dtype_mismatches_are_explicit() -> None:
    class Variable:
        def __init__(self, shape: tuple[int, ...], dtype: str):
            self.name = "field"
            self.shape = shape
            self.dtype = np.dtype(dtype)
            self.dimensions = ("time", "y", "x")

    left = Variable((2, 3, 5), "f4")
    wrong_shape = Variable((1, 4, 5), "f4")
    wrong_dtype = Variable((1, 3, 5), "f8")

    shape_result = COMPARATOR.compare_variable_slice_exact(
        left,
        wrong_shape,
        time_dimension="time",
        left_time_index=0,
        right_time_index=0,
    )
    dtype_result = COMPARATOR.compare_variable_slice_exact(
        left,
        wrong_dtype,
        time_dimension="time",
        left_time_index=0,
        right_time_index=0,
    )

    assert shape_result["reason"] == "selected_shape_mismatch"
    assert dtype_result["reason"] == "dtype_mismatch"


def test_cli_writes_report_atomically_and_returns_difference_status(
    tmp_path: Path,
) -> None:
    left = tmp_path / "left.nc"
    right = tmp_path / "right.nc"
    report = tmp_path / "comparison.json"
    write_output(left, times=(0.0,))
    write_output(right, times=(0.0,), static_delta=1.0)
    report.write_text('{"stale": true}\n', encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(left),
            str(right),
            "--left-time-index",
            "0",
            "--right-time-index",
            "0",
            "--chunk-mib",
            "0.00002",
            "--output",
            str(report),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["bitwise_equal_selected_output_state"] is False
    assert payload["comparison_contract"]["schema_strict"] is True
    assert list(tmp_path.glob(f".{report.name}.*.tmp")) == []
