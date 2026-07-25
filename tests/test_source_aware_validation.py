from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "validation"
    / "validate_hicar_against_forcing.py"
)
SPEC = importlib.util.spec_from_file_location("source_validation", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

FINALIZER_PATH = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "validation"
    / "finalize_production_qualification.py"
)
FINALIZER_SPEC = importlib.util.spec_from_file_location("qualification_finalizer", FINALIZER_PATH)
FINALIZER = importlib.util.module_from_spec(FINALIZER_SPEC)
assert FINALIZER_SPEC.loader is not None
FINALIZER_SPEC.loader.exec_module(FINALIZER)


def test_nearest_axis_indices_supports_both_axis_directions():
    values = np.array([0.1, 1.6, 2.9])
    assert np.array_equal(
        MODULE.nearest_axis_indices(np.array([0.0, 1.0, 2.0, 3.0]), values),
        np.array([0, 2, 3]),
    )
    assert np.array_equal(
        MODULE.nearest_axis_indices(np.array([3.0, 2.0, 1.0, 0.0]), values),
        np.array([3, 1, 0]),
    )


def test_vertical_interpolation_is_column_local_and_does_not_extrapolate():
    source_height = np.array([[0.0, 100.0], [10.0, 200.0], [20.0, 300.0]])
    source_value = np.array([[0.0, 10.0], [20.0, 30.0], [40.0, 50.0]])
    target_height = np.array([[-1.0, 150.0], [5.0, 250.0], [15.0, 350.0]])
    result = MODULE.interpolate_columns(source_height, source_value, target_height)
    assert np.isnan(result[0, 0])
    assert result[1, 0] == 10.0
    assert result[2, 0] == 30.0
    assert result[0, 1] == 20.0
    assert result[1, 1] == 40.0
    assert np.isnan(result[2, 1])


def test_metrics_are_exact_for_constant_offset():
    source = np.arange(1.0, 6.0)
    hicar = source + 2.0
    metrics = MODULE.metric_summary(hicar, source)
    assert metrics["paired_samples"] == 5
    assert metrics["bias"] == 2.0
    assert metrics["mae"] == 2.0
    assert metrics["rmse"] == 2.0
    assert metrics["correlation"] == pytest.approx(1.0)


def test_slurm_rss_units_are_normalized_to_kib():
    assert FINALIZER.rss_kib("1024K") == 1024
    assert FINALIZER.rss_kib("2M") == 2048
    assert FINALIZER.rss_kib("1.5G") == 1_572_864
