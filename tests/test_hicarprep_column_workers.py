from __future__ import annotations

import multiprocessing as mp
import unittest

import numpy as np

from preprocessing.hicarprep.pipeline import (
    _column_ranges,
    _reconstruct_target_columns,
)


def _column_inputs() -> dict[str, object]:
    ny, nx = 2, 3
    source_offsets = np.array([0.0, 400.0, 900.0, 1500.0, 2300.0])
    target_offsets = np.array([0.0, 300.0, 700.0, 1300.0])
    source_surface = np.array([[100.0, 130.0, 180.0], [80.0, 160.0, 220.0]])
    target_delta = np.array([[-50.0, 0.0, 100.0], [40.0, -20.0, 150.0]])
    source_hhl = source_offsets[:, None, None] + source_surface[None, :, :]
    target_hhl = (
        target_offsets[:, None, None] + source_surface[None, :, :] + target_delta[None, :, :]
    )
    source_z = 0.5 * (source_hhl[:-1] + source_hhl[1:])
    horizontal = np.arange(ny * nx, dtype=np.float64).reshape(ny, nx)
    remapped = {
        "T": 286.0 - 0.006 * source_z + 0.01 * horizontal[None, :, :],
        "P": 100_000.0 * np.exp(-source_z / 8200.0),
        "QV": np.maximum(0.009 - source_z * 2.0e-6, 1.0e-4),
        "U": 2.0 + source_z * 1.0e-3 + 0.1 * horizontal[None, :, :],
        "V": -1.0 + source_z * 5.0e-4 - 0.05 * horizontal[None, :, :],
    }
    hydro = {
        "QC": np.maximum(2.0e-4 - np.abs(source_z - 900.0) * 2.0e-7, 0.0),
        "QI": np.zeros_like(source_z),
    }
    source_w = np.linspace(-0.2, 0.3, source_hhl.shape[0])[:, None, None]
    source_w = np.broadcast_to(source_w, source_hhl.shape).copy()
    return {
        "source_hhl": source_hhl,
        "target_hhl": target_hhl,
        "remapped": remapped,
        "hydro": hydro,
        "source_w": source_w,
    }


class ColumnWorkerTests(unittest.TestCase):
    def test_worker_count_validation_and_partition_are_deterministic(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one"):
            _column_ranges(6, 0)
        with self.assertRaisesRegex(ValueError, "at least one"):
            _column_ranges(6, -1)
        with self.assertRaisesRegex(ValueError, "integer"):
            _column_ranges(6, True)
        self.assertEqual(_column_ranges(7, 3), [(0, 3), (3, 5), (5, 7)])
        self.assertEqual(_column_ranges(2, 8), [(0, 1), (1, 2)])

    @unittest.skipUnless("fork" in mp.get_all_start_methods(), "requires fork")
    def test_fork_workers_are_bitwise_equal_to_serial_for_every_output(self) -> None:
        inputs = _column_inputs()
        serial = _reconstruct_target_columns(**inputs, column_workers=1)
        parallel = _reconstruct_target_columns(**inputs, column_workers=2)
        serial_state, serial_w, serial_terrain = serial[:3]
        parallel_state, parallel_w, parallel_terrain = parallel[:3]
        self.assertEqual(set(serial_state), set(parallel_state))
        for name in serial_state:
            np.testing.assert_array_equal(parallel_state[name], serial_state[name])
        np.testing.assert_array_equal(parallel_w, serial_w)
        np.testing.assert_array_equal(parallel_terrain, serial_terrain)
        self.assertEqual(parallel[3:6], serial[3:6])
        self.assertEqual(serial[6], 1)
        self.assertEqual(parallel[6], 2)

    @unittest.skipUnless("fork" in mp.get_all_start_methods(), "requires fork")
    def test_worker_exception_propagates_and_pool_is_cleaned_up(self) -> None:
        inputs = _column_inputs()
        bad_remapped = {
            name: np.asarray(values).copy() for name, values in inputs["remapped"].items()
        }
        bad_remapped["P"][:, 0, 0] = -1.0
        bad_inputs = {**inputs, "remapped": bad_remapped}
        children_before = {process.pid for process in mp.active_children()}
        with self.assertRaisesRegex(ValueError, "physical domain"):
            _reconstruct_target_columns(**bad_inputs, column_workers=2)
        children_after = {process.pid for process in mp.active_children()}
        self.assertEqual(children_after - children_before, set())

        serial = _reconstruct_target_columns(**inputs, column_workers=1)
        recovered = _reconstruct_target_columns(**inputs, column_workers=2)
        for name in serial[0]:
            np.testing.assert_array_equal(recovered[0][name], serial[0][name])



if __name__ == "__main__":
    unittest.main()
