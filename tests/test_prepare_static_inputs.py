from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import netCDF4
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "prepare_static_inputs",
    ROOT / "scripts" / "prepare_static_inputs.py",
)
STATIC = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(STATIC)
class PrepareStaticInputsTests(unittest.TestCase):
    def test_offline_soilgrids_can_build_missing_derived_subset_from_raw_cache(self) -> None:
        class RawCacheProbe(Exception):
            pass

        calls = []

        def probe(url, destination, offline, source_identities):
            calls.append((url, destination, offline, source_identities))
            raise RawCacheProbe

        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            STATIC, "cached_download", side_effect=probe
        ):
            with self.assertRaises(RawCacheProbe):
                STATIC.soilgrids_subset(
                    "sand",
                    "0-5cm_mean",
                    Path(directory),
                    STATIC.CRS.from_epsg(2056),
                    np.array([0.0, 200.0]),
                    np.array([0.0, 200.0]),
                    200.0,
                    True,
                    "test",
                    [],
                )

        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][2])
        self.assertIn("soilgrids_vrt", str(calls[0][1]))

    def test_worldcover_is_reclassified_before_fractional_aggregation(self) -> None:
        x = np.array([0.0, 200.0])
        y = np.array([0.0, 200.0])

        def fake_warp(sources, out_tif, local_crs, x_arg, y_arg, dx_m, resampling):
            del sources, out_tif, local_crs, x_arg, y_arg, dx_m
            self.assertEqual(resampling, "average")
            category = int(str(fake_warp.current_target).split("category_")[1][:2])
            value = {7: 0.25, 15: 0.75}.get(category, 0.0)
            return np.full((2, 2), value, dtype=np.float32)

        def fake_reclass(source, target, *, category=None):
            del source
            fake_warp.current_target = target
            self.assertIsNotNone(category)
            return target

        # warp_to_domain is invoked immediately after all per-category paths
        # are prepared, so capture the category from the returned source path.
        def warp_from_source(sources, *args):
            fake_warp.current_target = sources[0]
            return fake_warp(sources, *args)

        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            STATIC, "_write_reclassified_worldcover_tile", side_effect=fake_reclass
        ), mock.patch.object(STATIC, "warp_to_domain", side_effect=warp_from_source):
            dominant, fractions = STATIC.aggregate_worldcover_to_usgs(
                [Path(directory) / "worldcover.tif"],
                Path(directory) / "cache",
                STATIC.CRS.from_epsg(2056),
                x,
                y,
                200.0,
                "test",
            )

        np.testing.assert_array_equal(dominant, 15)
        np.testing.assert_allclose(fractions[6], 0.25)
        np.testing.assert_allclose(fractions[14], 0.75)
        np.testing.assert_allclose(np.sum(fractions, axis=0), 1.0)

    def test_public_candidate_can_preserve_baseline_terrain_bitwise(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.nc"
            output = root / "candidate.nc"
            x, y, lat, lon, _ = STATIC.make_grid(46.815, 8.225, 0.4, 0.4, 200.0)
            shape = lat.shape
            topo = np.arange(np.prod(shape), dtype=np.float32).reshape(shape)
            with netCDF4.Dataset(baseline, "w") as dataset:
                dataset.createDimension("x", x.size)
                dataset.createDimension("y", y.size)
                dataset.createVariable("x", "f4", ("x",))[:] = x
                dataset.createVariable("y", "f4", ("y",))[:] = y
                for name, values in (
                    ("lat", lat), ("lon", lon), ("topo", topo),
                    ("topo_highres", topo + 10), ("topo_driving", topo - 10),
                    ("topo_blend_weight", np.full(shape, 0.5, dtype=np.float32)),
                ):
                    dataset.createVariable(name, "f4", ("y", "x"))[:, :] = values
            Path(f"{baseline}.ready").touch()
            landuse = np.full(shape, 7, dtype=np.int16)
            landuse_fraction = np.zeros((24, *shape), dtype=np.float32)
            landuse_fraction[6] = 1.0
            soil_layers = np.full((4, *shape), 6, dtype=np.int16)
            vwc = np.full((4, *shape), 0.28, dtype=np.float32)
            composition = {
                "sand": np.full((4, *shape), 40.0, dtype=np.float32),
                "silt": np.full((4, *shape), 40.0, dtype=np.float32),
                "clay": np.full((4, *shape), 20.0, dtype=np.float32),
            }
            public_result = (
                None, landuse, landuse_fraction, soil_layers[0], soil_layers, vwc, composition,
                ["mock land and soil"],
                [{
                    "url": "https://example.test/source", "cache_path": "source.tif",
                    "size_bytes": 1, "sha256": "a" * 64,
                }],
            )
            argv = [
                "prepare_static_inputs.py", "--output", str(output),
                "--center-lat", "46.815", "--center-lon", "8.225",
                "--width-km", "0.4", "--height-km", "0.4", "--dx-m", "200",
                "--public-sources", "--preserve-topography-from", str(baseline),
                "--cache-dir", str(root / "cache"),
                "--generating-commit", "b" * 40,
                "--runtime-manifest-sha256", "c" * 64,
            ]
            with mock.patch.object(STATIC, "build_public_static", return_value=public_result), \
                    mock.patch.object(sys, "argv", argv):
                self.assertEqual(STATIC.main(), 0)
            with netCDF4.Dataset(output) as dataset:
                np.testing.assert_array_equal(dataset["topo"][:], topo)
                np.testing.assert_array_equal(dataset["topo_highres"][:], topo + 10)
                np.testing.assert_array_equal(dataset["topo_driving"][:], topo - 10)
                self.assertIn("preserved_topography_identity", dataset.ncattrs())

    def test_soilgrids_depth_aggregation_uses_layer_overlap(self) -> None:
        values = {
            depth: np.full((2, 3), index, dtype=np.float32)
            for index, depth in enumerate(STATIC.SOILGRIDS_DEPTH_INTERVALS_CM, start=1)
        }

        result = STATIC.aggregate_soilgrids_depths(values)

        self.assertEqual(result.shape, (4, 2, 3))
        np.testing.assert_allclose(result[:, 0, 0], [1.5, 2.75, 4.25, 5.625])

    def test_soilgrids_depth_aggregation_rejects_incomplete_column(self) -> None:
        values = {
            depth: np.ones((1, 1), dtype=np.float32)
            for depth in tuple(STATIC.SOILGRIDS_DEPTH_INTERVALS_CM)[:-1]
        }

        with self.assertRaisesRegex(ValueError, "unexpected SoilGrids depth set"):
            STATIC.aggregate_soilgrids_depths(values)

    def test_placeholder_land_surface_writes_layer_texture_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "static.nc"
            self.assertEqual(
                STATIC.main.__module__,
                "prepare_static_inputs",
            )
            # Exercise the command-line entry point because NetCDF dimension
            # order is part of the HICAR interface contract.
            import subprocess
            import sys

            subprocess.run(
                (
                    sys.executable,
                    str(ROOT / "scripts" / "prepare_static_inputs.py"),
                    "--output",
                    str(output),
                    "--width-km",
                    "0.4",
                    "--height-km",
                    "0.4",
                    "--dx-m",
                    "200",
                    "--allow-placeholder-static",
                ),
                check=True,
                capture_output=True,
                text=True,
            )

            with netCDF4.Dataset(output) as dataset:
                self.assertEqual(dataset["soil_type_layer"].shape, (4, 3, 3))
                np.testing.assert_array_equal(dataset["soil_type_layer"][:], 3)
                np.testing.assert_allclose(
                    dataset["soil_layer_bounds_cm"][:],
                    STATIC.HICAR_SOIL_LAYER_INTERVALS_CM,
                )

if __name__ == "__main__":
    unittest.main()
