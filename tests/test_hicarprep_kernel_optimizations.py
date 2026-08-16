"""Focused regression tests for recurring forcing kernel optimizations."""

from __future__ import annotations

from pathlib import Path
import tempfile
from unittest import mock

import netCDF4
import numpy as np

from preprocessing.hicarprep.accelerated_rbf import _rbf_thread_count, apply_rbf
from preprocessing.hicarprep.pipeline import (
    _remap_hydrometeors,
    _source_absent_zero_qi,
    convert_water_to_hicar_mixing_ratios,
)


def _ordered_reference(
    source: np.ndarray,
    donor_index: np.ndarray,
    weight: np.ndarray,
    *,
    monotone: bool,
) -> np.ndarray:
    source_2d = np.asarray(source, dtype=np.float64).reshape((-1, source.shape[-1]))
    result = np.empty((source_2d.shape[0], donor_index.shape[0]), dtype=np.float64)
    for leading in range(source_2d.shape[0]):
        for target in range(donor_index.shape[0]):
            total = 0.0
            lower = np.inf
            upper = -np.inf
            for donor in range(donor_index.shape[1]):
                value = source_2d[leading, donor_index[target, donor]]
                total += value * weight[target, donor]
                lower = min(lower, value)
                upper = max(upper, value)
            result[leading, target] = np.clip(total, lower, upper) if monotone else total
    return result


def test_parallel_rbf_retains_fixed_donor_order_and_repeatability() -> None:
    rng = np.random.default_rng(82026)
    source = rng.normal(size=(2, 97))
    donor_index = rng.integers(0, source.shape[-1], size=(9_000, 10), dtype=np.int64)
    weight = rng.normal(size=donor_index.shape)
    weight /= np.sum(weight, axis=1, keepdims=True)

    expected = _ordered_reference(source, donor_index, weight, monotone=True)
    with (
        mock.patch.dict("os.environ", {"HICARPREP_RBF_THREADS": "4"}, clear=False),
        mock.patch(
            "preprocessing.hicarprep.accelerated_rbf._available_cpu_count",
            return_value=4,
        ),
    ):
        first = apply_rbf(source, donor_index, weight, monotone=True)
        second = apply_rbf(source, donor_index, weight, monotone=True)

    np.testing.assert_array_equal(first, expected)
    np.testing.assert_array_equal(second, first)


def test_parallel_rbf_preserves_constants_for_single_level_work() -> None:
    donor_index = np.arange(70, dtype=np.int64).reshape(7, 10)
    weight = np.full(donor_index.shape, 0.1)
    actual = apply_rbf(np.full((1, 70), 17.25), donor_index, weight, monotone=True)
    np.testing.assert_allclose(actual, 17.25, rtol=0.0, atol=2.0e-15)


def test_rbf_thread_count_honors_slurm_task_allocation_and_explicit_cap() -> None:
    with (
        mock.patch.dict(
            "os.environ",
            {"SLURM_CPUS_PER_TASK": "4", "HICARPREP_RBF_THREADS": "12"},
            clear=False,
        ),
        mock.patch(
            "preprocessing.hicarprep.accelerated_rbf._available_cpu_count",
            return_value=4,
        ),
    ):
        assert _rbf_thread_count(1_000_000) == 4


def test_water_conversion_shares_unchanged_arrays_and_preserves_inputs() -> None:
    shape = (3, 4, 5)
    state = {
        "T": np.full(shape, 280.0),
        "P": np.full(shape, 90_000.0),
        "U": np.arange(np.prod(shape), dtype=np.float64).reshape(shape),
        "QV": np.full(shape, 0.010),
        "QC": np.full(shape, 0.001),
        "QI": np.full(shape, 0.002),
    }
    state["RHO"] = state["P"] / (
        287.05
        * state["T"]
        * (1.0 + 0.608 * state["QV"] - state["QC"] - state["QI"])
    )
    original_water = {name: state[name].copy() for name in ("QV", "QC", "QI")}

    converted = convert_water_to_hicar_mixing_ratios(state)
    dry = 1.0 - state["QV"] - state["QC"] - state["QI"]

    for name in ("T", "P", "U"):
        assert converted[name] is state[name]
    for name in ("QV", "QC", "QI", "RHO"):
        assert converted[name] is not state[name]
    for name in original_water:
        np.testing.assert_array_equal(state[name], original_water[name])
        np.testing.assert_allclose(converted[name], state[name] / dry)
    np.testing.assert_allclose(converted["RHO"], state["RHO"], rtol=1.0e-14)


def test_source_absent_qi_requires_visible_decoder_provenance() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "native.nc"
        with netCDF4.Dataset(path, "w") as dataset:
            dataset.createDimension("level", 2)
            dataset.createDimension("cell", 3)
            qi = dataset.createVariable("QI", "f8", ("level", "cell"))
            qi[:] = 0.0
            dataset.missing_qi_policy = "source_absent_zero"
            qi.source_policy = "source_absent_zero"
        with netCDF4.Dataset(path) as dataset:
            assert _source_absent_zero_qi(dataset)

        with netCDF4.Dataset(path, "a") as dataset:
            dataset["QI"].source_policy = "source_values"
        with netCDF4.Dataset(path) as dataset:
            assert not _source_absent_zero_qi(dataset)


def test_source_absent_qi_is_not_horizontally_remapped() -> None:
    class RecordingWeights:
        def __init__(self) -> None:
            self.calls = 0

        def apply(self, values, *, monotone, backend):
            self.calls += 1
            assert monotone
            assert backend == "numba"
            return np.asarray(values).copy()

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "native.nc"
        with netCDF4.Dataset(path, "w") as dataset:
            dataset.createDimension("level", 2)
            dataset.createDimension("cell", 3)
            qc = dataset.createVariable("QC", "f8", ("level", "cell"))
            qi = dataset.createVariable("QI", "f8", ("level", "cell"))
            qc[:] = 0.001
            qi[:] = 0.0
            qc.units = "kg kg-1"
            qi.units = "kg kg-1"
            dataset.missing_qi_policy = "source_absent_zero"
            qi.source_policy = "source_absent_zero"

        weights = RecordingWeights()
        with netCDF4.Dataset(path) as dataset:
            hydro, absent = _remap_hydrometeors(dataset, weights, rbf_backend="numba")

    assert absent
    assert weights.calls == 1
    assert set(hydro) == {"QC"}
    np.testing.assert_array_equal(hydro["QC"], np.full((2, 3), 0.001))
