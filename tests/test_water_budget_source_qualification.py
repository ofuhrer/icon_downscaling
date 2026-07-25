from __future__ import annotations

import importlib.util
from pathlib import Path

from netCDF4 import Dataset
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "validation"
    / "validate_water_budget_source_qualification.py"
)
SPEC = importlib.util.spec_from_file_location(
    "validate_water_budget_source_qualification", SCRIPT
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def write_output(path: Path, *, records: int, include_new: bool) -> None:
    with Dataset(path, "w") as dataset:
        dataset.createDimension("time", records)
        dataset.createDimension("y", 2)
        dataset.createDimension("x", 3)
        time = dataset.createVariable("time", "f8", ("time",))
        time[:] = np.arange(records, dtype=np.float64)
        temperature = dataset.createVariable(
            "temperature", "f4", ("time", "y", "x"), fill_value=-9999.0
        )
        temperature[:] = np.arange(records * 6, dtype=np.float32).reshape(
            records, 2, 3
        )
        if include_new:
            for index, name in enumerate(sorted(MODULE.NEW_FIELDS), start=1):
                variable = dataset.createVariable(
                    name, "f4", ("time", "y", "x"), fill_value=-9999.0
                )
                variable.units = "kg m-2"
                variable.accumulation_semantics = (
                    "cumulative since simulation start; no output reset; "
                    "restart-persistent"
                )
                variable.interval_semantics = (
                    "difference consecutive records gives amount over "
                    "(previous_time, time]"
                )
                values = np.zeros((records, 2, 3), dtype=np.float32)
                values[-1] = index
                variable[:] = values


def test_exact_parent_child_permits_only_new_water_fields(tmp_path: Path) -> None:
    parent = tmp_path / "parent.nc"
    child = tmp_path / "child.nc"
    write_output(parent, records=3, include_new=False)
    write_output(child, records=3, include_new=True)

    result = MODULE.compare_exact(parent, child)

    assert result["mismatch_count"] == 0
    assert result["compared_field_count"] == 2
    assert set(result["candidate_only_fields"]) == MODULE.NEW_FIELDS


def test_exact_parent_child_detects_common_field_change(tmp_path: Path) -> None:
    parent = tmp_path / "parent.nc"
    child = tmp_path / "child.nc"
    write_output(parent, records=3, include_new=False)
    write_output(child, records=3, include_new=True)
    with Dataset(child, "a") as dataset:
        dataset.variables["temperature"][2, 0, 0] += 1

    result = MODULE.compare_exact(parent, child)

    assert result["mismatch_count"] == 1
    assert result["mismatches"][0]["variable"] == "temperature"


def test_exact_parent_child_can_require_no_new_fields(tmp_path: Path) -> None:
    parent = tmp_path / "parent.nc"
    child = tmp_path / "child.nc"
    write_output(parent, records=3, include_new=False)
    write_output(child, records=3, include_new=False)

    result = MODULE.compare_exact(
        parent,
        child,
        expected_candidate_only_fields=set(),
    )

    assert result["mismatch_count"] == 0
    assert result["candidate_only_fields"] == []


def test_restart_comparison_uses_last_record_and_observes_runoff(
    tmp_path: Path,
) -> None:
    continuous = tmp_path / "continuous.nc"
    segmented = tmp_path / "segmented.nc"
    write_output(continuous, records=3, include_new=True)
    write_output(segmented, records=1, include_new=True)
    with Dataset(segmented, "a") as dataset:
        dataset.variables["time"][0] = 2
        dataset.variables["temperature"][0] = np.arange(
            12, 18, dtype=np.float32
        ).reshape(2, 3)

    tolerance_path = tmp_path / "tolerances.yaml"
    tolerance_path.write_text(
        "defaults: {rtol: 0.0, atol: 0.0, frac: 0.0}\n"
        "aggregate:\n"
        "  precipitation: {rtol: 0.02, atol: 1.0e-9}\n"
    )
    result = MODULE.compare_tolerant(
        continuous,
        segmented,
        MODULE.load_tolerances(tolerance_path),
        last_time=True,
    )
    runoff = MODULE.field_stats(
        continuous, "runoff_surface_cumulative", time_index=2
    )

    assert result["failure_count"] == 0
    assert runoff["maximum"] > 0
    assert runoff["positive_count"] == 6
