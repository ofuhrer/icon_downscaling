from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

import netCDF4
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from hicar_static_topography import (
    block_mean_change_metrics,
    filter_land_topography,
    nominal_shapiro_response,
    terrain_metrics,
)

FILTER_SPEC = importlib.util.spec_from_file_location("filter_static_topography", ROOT / "scripts" / "filter_static_topography.py")
FILTER_MODULE = importlib.util.module_from_spec(FILTER_SPEC)
assert FILTER_SPEC.loader is not None
FILTER_SPEC.loader.exec_module(FILTER_MODULE)


class FilterLandTopographyTests(unittest.TestCase):
    def test_filter_reduces_cliff_and_preserves_water(self) -> None:
        topo = np.array(
            [
                [0.0, 0.0, 0.0, 0.0, 0.0],
                [0.0, 0.0, 1000.0, 1000.0, 1000.0],
                [0.0, 0.0, 1000.0, 1000.0, 1000.0],
            ],
            dtype=np.float32,
        )
        landmask = np.ones_like(topo, dtype=np.int16)
        landmask[:, 0] = 0
        filtered = filter_land_topography(topo, landmask, passes=2, order=8)
        self.assertTrue(np.array_equal(filtered[:, 0], topo[:, 0]))
        self.assertLess(
            terrain_metrics(filtered, landmask).max_land_neighbor_difference_m,
            terrain_metrics(topo, landmask).max_land_neighbor_difference_m,
        )

    def test_sea_level_policy_is_explicit(self) -> None:
        topo = np.array([[500.0, 10.0], [500.0, 20.0]], dtype=np.float32)
        landmask = np.array([[1, 0], [1, 0]], dtype=np.int16)
        filtered = filter_land_topography(topo, landmask, passes=1, order=8, water_policy="sea-level", sea_level_m=0.0)
        self.assertTrue(np.array_equal(filtered[:, 1], np.zeros(2, dtype=np.float32)))

    def test_order_eight_is_scale_selective(self) -> None:
        nx = 256
        x = np.arange(nx, dtype=np.float64)
        landmask = np.ones((65, nx), dtype=np.int16)
        short = np.tile(np.cos(np.pi * x), (65, 1))
        long = np.tile(np.cos(2.0 * np.pi * x / 32.0), (65, 1))
        short_filtered = filter_land_topography(short, landmask, passes=1, order=8)
        long_filtered = filter_land_topography(long, landmask, passes=1, order=8)
        interior = (slice(16, -16), slice(16, -16))
        short_ratio = np.std(short_filtered[interior]) / np.std(short[interior])
        long_ratio = np.std(long_filtered[interior]) / np.std(long[interior])
        self.assertLess(short_ratio, 1.0e-5)
        self.assertGreater(long_ratio, 0.999999)
        self.assertEqual(nominal_shapiro_response(8, 1.0, 2.0), 0.0)
        self.assertGreater(nominal_shapiro_response(8, 1.0, 5.0), 0.98)

    def test_block_metrics_detect_only_uncancelled_large_scale_change(self) -> None:
        checkerboard = np.indices((20, 20)).sum(axis=0) % 2
        delta = np.where(checkerboard == 0, -10.0, 10.0)
        metrics = block_mean_change_metrics(
            np.zeros((20, 20)), delta, np.ones((20, 20), dtype=np.int16), block_cells=10
        )
        self.assertAlmostEqual(metrics["rms_change_m"], 0.0)


class FilterStaticFileTests(unittest.TestCase):
    def test_copy_preserves_nonterrain_fields_and_reapplies_blend(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.nc"
            output = Path(directory) / "filtered.nc"
            raw = np.array([[0.0, 1000.0, 1000.0], [0.0, 1000.0, 1000.0]], dtype=np.float32)
            driving = np.full(raw.shape, 100.0, dtype=np.float32)
            weight = np.array([[0.0, 0.5, 1.0], [0.0, 0.5, 1.0]], dtype=np.float32)
            soil = np.arange(6, dtype=np.int16).reshape(raw.shape)
            with netCDF4.Dataset(source, "w") as ds:
                ds.createDimension("y", 2)
                ds.createDimension("x", 3)
                ds.hicar_dx_m = 5000.0
                for name, values, dtype in (
                    ("topo_highres", raw, "f4"),
                    ("topo_driving", driving, "f4"),
                    ("topo_blend_weight", weight, "f4"),
                    ("topo", (1.0 - weight) * driving + weight * raw, "f4"),
                    ("landmask", np.ones(raw.shape, dtype=np.int16), "i2"),
                    ("lat", np.full(raw.shape, 46.0), "f4"),
                    ("lon", np.full(raw.shape, 8.0), "f4"),
                    ("soil_type", soil, "i2"),
                ):
                    var = ds.createVariable(name, dtype, ("y", "x"))
                    var[:, :] = values

            report = FILTER_MODULE.filter_static_file(
                source,
                output,
                passes=1,
                order=8,
                strength=1.0,
                water_policy="preserve",
                sea_level_m=0.0,
            )
            with netCDF4.Dataset(output) as ds:
                filtered_raw = np.asarray(ds.variables["topo_highres"][:])
                expected = (1.0 - weight) * driving + weight * filtered_raw
                np.testing.assert_allclose(ds.variables["topo"][:], expected)
                np.testing.assert_array_equal(ds.variables["soil_type"][:], soil)
                np.testing.assert_array_equal(ds.variables["topo_highres_unfiltered"][:], raw)
                self.assertIn("topo_highres_filter_delta", ds.variables)
            self.assertIn("soil_type", report["unchanged_variables_verified"])
            self.assertTrue(Path(str(output) + ".ready").is_file())


if __name__ == "__main__":
    unittest.main()
