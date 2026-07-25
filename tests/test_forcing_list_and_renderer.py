from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import netCDF4


ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "case_studies" / "swiss_200m"
WRITER = CASE / "scripts" / "write_forcing_list.py"
RENDERER = CASE / "scripts" / "render_hicar_namelist.py"
PLANNER = CASE / "streaming" / "create_chunk_plan.py"


def write_time_record(path: Path, hour: int) -> None:
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("time", 1)
        time = dataset.createVariable("time", "f8", ("time",))
        time.units = "hours since 2010-01-01 00:00:00"
        time[:] = [hour]
    Path(f"{path}.ready").touch()


class ForcingListAndRendererTests(unittest.TestCase):
    def test_exact_terminal_record_renders_validated_sleve_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            forcing = []
            for hour in range(3):
                path = root / f"forcing_{hour}.nc"
                write_time_record(path, hour)
                forcing.append(path)
            forcing_list = root / "forcing.txt"
            writer = subprocess.run(
                [
                    sys.executable,
                    str(WRITER),
                    "--output",
                    str(forcing_list),
                    "--expected-start",
                    "2010-01-01T00:00:00",
                    "--expected-end",
                    "2010-01-01T02:00:00",
                    *map(str, reversed(forcing)),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(writer.returncode, 0, writer.stderr)
            self.assertTrue(Path(f"{forcing_list}.ready").is_file())
            self.assertTrue(all(line.startswith('"') for line in forcing_list.read_text().splitlines()))

            static = root / "static.nc"
            static.touch()
            Path(f"{static}.ready").touch()
            namelist = root / "input.nml"
            renderer = subprocess.run(
                [
                    sys.executable,
                    str(RENDERER),
                    "--static-file",
                    str(static),
                    "--forcing-file-list",
                    str(forcing_list),
                    "--start-date",
                    "2010-01-01 00:00:00",
                    "--end-date",
                    "2010-01-01 02:00:00",
                    "--output-interval",
                    "7200",
                    "--output-profile",
                    "engineering",
                    "--output-dir",
                    str(root / "output"),
                    "--restart-dir",
                    str(root / "restart"),
                    "--output",
                    str(namelist),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(renderer.returncode, 0, renderer.stderr)
            rendered = namelist.read_text()
            self.assertIn("terrain_smooth_windowsize = 5", rendered)
            self.assertIn("terrain_smooth_cycles = 10", rendered)
            self.assertIn("outputinterval = 7200", rendered)
            self.assertIn("'w_grid'", rendered)
            self.assertIn("soil_t_var = 'soil_temperature'", rendered)
            self.assertIn("soil_vwc_var = 'soil_vwc'", rendered)

    def test_gap_in_forcing_series_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "forcing_0.nc"
            last = root / "forcing_2.nc"
            write_time_record(first, 0)
            write_time_record(last, 2)
            result = subprocess.run(
                [
                    sys.executable,
                    str(WRITER),
                    "--output",
                    str(root / "forcing.txt"),
                    str(first),
                    str(last),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("not continuous", result.stderr)

    def test_published_stream_plan_allows_future_files_and_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chunk = root / "chunk"
            plan = chunk / "chunk_plan.json"
            forcing_list = chunk / "forcing_list.txt"
            planner = subprocess.run(
                [
                    sys.executable,
                    str(PLANNER),
                    "--start",
                    "2010-01-01T00:00:00",
                    "--end",
                    "2010-01-01T02:00:00",
                    "--chunk-id",
                    "test",
                    "--chunk-root",
                    str(chunk),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(planner.returncode, 0, planner.stderr)
            static = root / "static.nc"
            static.touch()
            Path(f"{static}.ready").touch()
            namelist = root / "input.nml"
            renderer = subprocess.run(
                [
                    sys.executable,
                    str(RENDERER),
                    "--static-file",
                    str(static),
                    "--forcing-file-list",
                    str(forcing_list),
                    "--forcing-plan",
                    str(plan),
                    "--start-date",
                    "2010-01-01 00:00:00",
                    "--end-date",
                    "2010-01-01 02:00:00",
                    "--output-interval",
                    "3600",
                    "--restart-interval",
                    "2",
                    "--restart-from",
                    "2010-01-01 00:00:00",
                    "--output-dir",
                    str(root / "output"),
                    "--restart-dir",
                    str(root / "restart"),
                    "--output",
                    str(namelist),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(renderer.returncode, 0, renderer.stderr)
            rendered = namelist.read_text()
            self.assertIn("restartinterval = 2", rendered)
            self.assertIn("restart_run = .True.", rendered)
            self.assertIn("restart_date = '2010-01-01 00:00:00'", rendered)

    def test_routine_profile_contains_near_surface_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chunk = root / "chunk"
            plan = chunk / "chunk_plan.json"
            forcing_list = chunk / "forcing_list.txt"
            planner = subprocess.run(
                [
                    sys.executable,
                    str(PLANNER),
                    "--start",
                    "2010-01-01T00:00:00",
                    "--end",
                    "2010-01-01T01:00:00",
                    "--chunk-id",
                    "routine",
                    "--chunk-root",
                    str(chunk),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(planner.returncode, 0, planner.stderr)
            static = root / "static.nc"
            static.touch()
            Path(f"{static}.ready").touch()
            namelist = root / "input.nml"
            renderer = subprocess.run(
                [
                    sys.executable,
                    str(RENDERER),
                    "--static-file",
                    str(static),
                    "--forcing-file-list",
                    str(forcing_list),
                    "--forcing-plan",
                    str(plan),
                    "--start-date",
                    "2010-01-01 00:00:00",
                    "--end-date",
                    "2010-01-01 01:00:00",
                    "--output-profile",
                    "routine",
                    "--output-dir",
                    str(root / "output"),
                    "--restart-dir",
                    str(root / "restart"),
                    "--output",
                    str(namelist),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(renderer.returncode, 0, renderer.stderr)
            rendered = namelist.read_text()
            for name in ("'taix'", "'hus2m'", "'u10m'", "'v10m'"):
                self.assertIn(name, rendered)

    def test_qualification_profile_contains_land_budget_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chunk = root / "chunk"
            plan = chunk / "chunk_plan.json"
            forcing_list = chunk / "forcing_list.txt"
            planner = subprocess.run(
                [
                    sys.executable,
                    str(PLANNER),
                    "--start",
                    "2010-01-01T00:00:00",
                    "--end",
                    "2010-01-01T03:00:00",
                    "--chunk-id",
                    "qualification",
                    "--chunk-root",
                    str(chunk),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(planner.returncode, 0, planner.stderr)
            static = root / "static.nc"
            static.touch()
            Path(f"{static}.ready").touch()
            namelist = root / "input.nml"
            renderer = subprocess.run(
                [
                    sys.executable,
                    str(RENDERER),
                    "--static-file",
                    str(static),
                    "--forcing-file-list",
                    str(forcing_list),
                    "--forcing-plan",
                    str(plan),
                    "--start-date",
                    "2010-01-01 00:00:00",
                    "--end-date",
                    "2010-01-01 03:00:00",
                    "--output-interval",
                    "10800",
                    "--output-profile",
                    "qualification",
                    "--rea-l-land-initialization",
                    "--output-dir",
                    str(root / "output"),
                    "--restart-dir",
                    str(root / "restart"),
                    "--output",
                    str(namelist),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(renderer.returncode, 0, renderer.stderr)
            rendered = namelist.read_text()
            for name in (
                "'hfss'",
                "'hfls'",
                "'swet'",
                "'soil_column_total_water'",
                "'soil_water_content'",
                "'soil_temperature'",
                "'runoff_surface'",
                "'runoff_subsurface'",
                "'runoff_surface_cumulative'",
                "'runoff_subsurface_cumulative'",
                "'evaporation_net_cumulative'",
                "'water_aquifer'",
                "'storage_gw'",
                "'wetland_h20_store'",
            ):
                self.assertIn(name, rendered)
            self.assertIn("outputinterval = 10800", rendered)
            self.assertIn("swe_var = 'swe'", rendered)
            self.assertIn("snowh_var = 'snow_height'", rendered)

    def test_wind_climatology_profile_is_separate_and_source_complete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chunk = root / "chunk"
            plan = chunk / "chunk_plan.json"
            forcing_list = chunk / "forcing_list.txt"
            planner = subprocess.run(
                [
                    sys.executable,
                    str(PLANNER),
                    "--start",
                    "2010-01-01T00:00:00",
                    "--end",
                    "2010-01-01T01:00:00",
                    "--chunk-id",
                    "wind",
                    "--chunk-root",
                    str(chunk),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(planner.returncode, 0, planner.stderr)
            static = root / "static.nc"
            static.touch()
            Path(f"{static}.ready").touch()
            namelist = root / "input.nml"
            renderer = subprocess.run(
                [
                    sys.executable,
                    str(RENDERER),
                    "--static-file",
                    str(static),
                    "--forcing-file-list",
                    str(forcing_list),
                    "--forcing-plan",
                    str(plan),
                    "--start-date",
                    "2010-01-01 00:00:00",
                    "--end-date",
                    "2010-01-01 01:00:00",
                    "--output-profile",
                    "wind_climatology",
                    "--output-dir",
                    str(root / "output"),
                    "--restart-dir",
                    str(root / "restart"),
                    "--output",
                    str(namelist),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(renderer.returncode, 0, renderer.stderr)
            rendered = namelist.read_text()
            for name in (
                "'u10m'",
                "'v10m'",
                "'u_agl'",
                "'v_agl'",
                "'rho_agl'",
                "'ustar'",
                "'surface_roughness'",
                "'sfc_Ri'",
                "'hpbl'",
            ):
                self.assertIn(name, rendered)
            for excluded in (
                "'u'",
                "'v'",
                "'density'",
                "'soil_water_content'",
            ):
                self.assertNotIn(excluded, rendered)


if __name__ == "__main__":
    unittest.main()
