from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest

import netCDF4
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "validate_sleve_geometry",
    ROOT / "scripts" / "validate_sleve_geometry.py",
)
VALIDATOR = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(VALIDATOR)


def write_static(path: Path, terrain: np.ndarray) -> None:
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("lat_y", terrain.shape[0])
        dataset.createDimension("lon_x", terrain.shape[1])
        variable = dataset.createVariable("topo", "f4", ("lat_y", "lon_x"))
        variable[:, :] = terrain


class SleveGeometryValidatorTests(unittest.TestCase):
    def test_flat_terrain_is_invertible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flat.nc"
            write_static(path, np.zeros((9, 11), dtype=np.float32))
            result = VALIDATOR.validate_geometry(
                path,
                terrain_variable="topo",
                nz=80,
                top_height=12_000.0,
                lowest_layer=15.0,
                stretch_factor=0.65,
                decay_large=2.0,
                decay_small=6.0,
                sleve_exponent=1.35,
                smooth_window_radius=2,
                smooth_cycles=3,
            )
            self.assertEqual(result["status"], "PASS")
            self.assertAlmostEqual(result["minimum_mass_jacobian"]["value"], 1.0)
            self.assertGreater(
                result["minimum_interface_layer_thickness"]["value_m"], 0.0
            )

    def test_extreme_grid_scale_peak_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "peak.nc"
            terrain = np.zeros((21, 21), dtype=np.float32)
            terrain[10, 10] = 20_000.0
            write_static(path, terrain)
            result = VALIDATOR.validate_geometry(
                path,
                terrain_variable="topo",
                nz=80,
                top_height=12_000.0,
                lowest_layer=15.0,
                stretch_factor=0.65,
                decay_large=2.0,
                decay_small=6.0,
                sleve_exponent=1.35,
                smooth_window_radius=2,
                smooth_cycles=3,
            )
            self.assertEqual(result["status"], "FAIL")
            self.assertLess(result["minimum_mass_jacobian"]["value"], 0.0)

    def test_declared_engineering_margin_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flat.nc"
            write_static(path, np.zeros((9, 11), dtype=np.float32))
            result = VALIDATOR.validate_geometry(
                path,
                terrain_variable="topo",
                nz=80,
                top_height=12_000.0,
                lowest_layer=15.0,
                stretch_factor=0.65,
                decay_large=2.0,
                decay_small=6.0,
                sleve_exponent=1.35,
                smooth_window_radius=2,
                smooth_cycles=3,
                required_mass_jacobian=1.01,
                required_interface_thickness=5.0,
            )
            self.assertEqual(result["status"], "FAIL")
            self.assertEqual(result["acceptance"]["minimum_mass_jacobian"], 1.01)
            self.assertTrue(
                any("Jacobian" in failure for failure in result["failures"])
            )


if __name__ == "__main__":
    unittest.main()
