from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import importlib.util
import subprocess
import sys
import tempfile
import unittest

import netCDF4
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
PUBLISHER = (
    ROOT / "case_studies" / "swiss_200m" / "fixed_parameters" /
    "publish_terrain_radiation_static.py"
)
SYNTHETIC_PREPARER = (
    ROOT / "case_studies" / "swiss_200m" / "fixed_parameters" /
    "prepare_synthetic_terrain_radiation_gate.py"
)
MODEL_PREPARER = (
    ROOT / "case_studies" / "swiss_200m" / "fixed_parameters" /
    "prepare_terrain_radiation_model_gate.py"
)
RADIATION_DRIVER = ROOT / "HICAR" / "src" / "physics" / "ra_driver.F90"


def load_model_preparer():
    spec = importlib.util.spec_from_file_location("terrain_model_gate", MODEL_PREPARER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TerrainRadiationStaticTests(unittest.TestCase):
    def test_gpu_solar_reduction_is_synchronized_before_sun_up_gate(self) -> None:
        source = RADIATION_DRIVER.read_text(encoding="utf-8")
        reduction = source.index("reduction(max:coszen_max) async(1)")
        sun_up = source.index("sun_up = (coszen_max > 0.0)", reduction)
        self.assertIn("!$acc wait(1)", source[reduction:sun_up])

    def test_model_gate_solar_path_samples_shadow_and_visibility(self) -> None:
        module = load_model_preparer()
        samples = []
        by_time = {}
        for minute in range(6 * 60 + 30, 9 * 60 + 31, 5):
            when = datetime(2020, 7, 20, minute // 60, minute % 60, tzinfo=timezone.utc)
            elevation, azimuth = module.hicar_solar_position(when, 46.815, 8.225)
            sector = int(np.floor(azimuth / 4.0))
            by_time[when.strftime("%H:%M")] = (elevation, sector)
            if sector == module.BLOCKED_SECTOR:
                samples.append(elevation)
        self.assertTrue(any(value < module.HORIZON_ELEVATION_DEG for value in samples))
        self.assertTrue(any(value >= module.HORIZON_ELEVATION_DEG for value in samples))
        self.assertEqual(by_time["06:50"][1], module.BLOCKED_SECTOR)
        self.assertLess(by_time["06:50"][0], module.HORIZON_ELEVATION_DEG)
        self.assertEqual(by_time["07:05"][1], module.BLOCKED_SECTOR)
        self.assertGreaterEqual(by_time["07:05"][0], module.HORIZON_ELEVATION_DEG)

    def test_synthetic_gate_publishes_analytic_flat_and_blocked_fixtures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "gate"
            result = subprocess.run(
                [
                    sys.executable, str(SYNTHETIC_PREPARER),
                    "--output-dir", str(output), "--size", "5",
                    "--hicar-root", str(ROOT / "HICAR"),
                ],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            contract_path = output / "experiment_contract.json"
            self.assertTrue(Path(f"{contract_path}.ready").is_file())
            import json
            contract = json.loads(contract_path.read_text())
            self.assertEqual(contract["schema"], "hicar-terrain-radiation-synthetic-gate/v2")
            self.assertIn("within each enabled flat run", contract["gates"]["flat_identity"])
            self.assertEqual(contract["geometry"]["blocked_zero_based_sector"], 22)
            self.assertFalse(contract["analytic_expectations"]["at_blocked_azimuth"][0]["visible"])
            self.assertTrue(contract["analytic_expectations"]["at_blocked_azimuth"][1]["visible"])
            with netCDF4.Dataset(output / "static_single_sector_blocked.nc") as dataset:
                np.testing.assert_allclose(dataset["hlm"][22], 60.0)
                np.testing.assert_allclose(
                    dataset["svf"][:], 1.0 - np.sin(np.deg2rad(30.0)) ** 2 / 90.0,
                )

    def test_audited_geometry_is_merged_and_published(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "base.nc"
            geometry = root / "geometry.nc"
            output = root / "terrain_static.nc"
            manifest = root / "terrain_static.json"
            with netCDF4.Dataset(base, "w") as dataset:
                dataset.createDimension("y", 2)
                dataset.createDimension("x", 3)
                dataset.createVariable("topo", "f4", ("y", "x"))[:] = 1000.0
                dataset.createVariable("slope_angle", "f4", ("y", "x"))[:] = 0.0
                dataset.createVariable("aspect_angle", "f4", ("y", "x"))[:] = 0.0
            Path(f"{base}.ready").touch()
            with netCDF4.Dataset(geometry, "w") as dataset:
                dataset.createDimension("azimuth", 90)
                dataset.createDimension("y", 2)
                dataset.createDimension("x", 3)
                dataset.createVariable("azimuth", "f4", ("azimuth",))[:] = np.arange(0, 360, 4)
                dataset.createVariable("hlm", "f4", ("azimuth", "y", "x"))[:] = 90.0
                dataset.createVariable("svf", "f4", ("y", "x"))[:] = 1.0
                dataset.generator = "synthetic-test"
                dataset.generator_version = "1"
                dataset.source_dem_sha256 = "a" * 64
                dataset.vertical_datum = "EGM96 orthometric converted to WGS84 ellipsoidal"
                dataset.horizon_convention = "hlm_zenith_angle_degrees_flat_90"
                dataset.search_distance_km = 20.0
            Path(f"{geometry}.ready").touch()

            result = subprocess.run(
                [
                    sys.executable, str(PUBLISHER),
                    "--base-static", str(base),
                    "--geometry", str(geometry),
                    "--output", str(output),
                    "--manifest", str(manifest),
                ],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(Path(f"{output}.ready").is_file())
            self.assertTrue(Path(f"{manifest}.ready").is_file())
            with netCDF4.Dataset(output) as dataset:
                self.assertEqual(dataset.variables["hlm"].shape, (90, 2, 3))
                self.assertTrue(np.all(dataset.variables["svf"][:] == 1.0))
                self.assertEqual(
                    dataset.terrain_radiation_horizon_convention,
                    "hlm_zenith_angle_degrees_flat_90",
                )

    def test_shifted_azimuth_convention_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "base.nc"
            geometry = root / "geometry.nc"
            with netCDF4.Dataset(base, "w") as dataset:
                dataset.createDimension("y", 1)
                dataset.createDimension("x", 1)
                dataset.createVariable("topo", "f4", ("y", "x"))[:] = 0.0
                dataset.createVariable("slope_angle", "f4", ("y", "x"))[:] = 0.0
                dataset.createVariable("aspect_angle", "f4", ("y", "x"))[:] = 0.0
            Path(f"{base}.ready").touch()
            with netCDF4.Dataset(geometry, "w") as dataset:
                dataset.createDimension("azimuth", 90)
                dataset.createDimension("y", 1)
                dataset.createDimension("x", 1)
                dataset.createVariable("azimuth", "f4", ("azimuth",))[:] = np.arange(2, 362, 4)
                dataset.createVariable("hlm", "f4", ("azimuth", "y", "x"))[:] = 90.0
                dataset.createVariable("svf", "f4", ("y", "x"))[:] = 1.0
                for name, value in {
                    "generator": "test", "generator_version": "1",
                    "source_dem_sha256": "a" * 64, "vertical_datum": "test",
                    "horizon_convention": "hlm_zenith_angle_degrees_flat_90",
                    "search_distance_km": 20.0,
                }.items():
                    setattr(dataset, name, value)
            Path(f"{geometry}.ready").touch()
            result = subprocess.run(
                [
                    sys.executable, str(PUBLISHER), "--base-static", str(base),
                    "--geometry", str(geometry), "--output", str(root / "out.nc"),
                    "--manifest", str(root / "out.json"),
                ],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("azimuth must", result.stderr)


if __name__ == "__main__":
    unittest.main()
