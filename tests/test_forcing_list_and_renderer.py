from __future__ import annotations

import json
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
V29_DIAGNOSIS = CASE / "validation" / "diagnose_v29_surface_regime.py"
V29_MECHANISM_ASSESSOR = CASE / "validation" / "assess_v29_mechanism_baseline.py"


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
            self.assertIn("nmp_opt_soil = 1", rendered)
            self.assertNotIn("soiltexture_var", rendered)
            self.assertIn("update_interval_rad = 600.0", rendered)
            self.assertIn("icloud = 3", rendered)
            self.assertIn("cldovrlp = 2", rendered)
            self.assertIn("terrain_shading = .False.", rendered)
            self.assertIn("terrain_direct_sw = .False.", rendered)
            self.assertIn("terrain_diffuse_sw = .False.", rendered)
            self.assertIn("terrain_reflected_sw = .False.", rendered)
            self.assertIn("terrain_longwave = .False.", rendered)
            self.assertNotIn("hlm_var", rendered)
            self.assertIn("alpha_const = 1.0", rendered)
            self.assertIn("Sx = .True.", rendered)
            self.assertIn("advect_density = .True.", rendered)

            with netCDF4.Dataset(static, "w") as dataset:
                dataset.createDimension("soil_layer", 4)
                dataset.createDimension("y", 1)
                dataset.createDimension("x", 1)
                dataset.createVariable(
                    "soil_type_layer", "i2", ("soil_layer", "y", "x")
                )[:, :, :] = 6
            soil_namelist = root / "depth_soil.nml"
            depth_soil = subprocess.run(
                [
                    sys.executable,
                    str(RENDERER),
                    "--static-file", str(static),
                    "--depth-varying-soil",
                    "--forcing-file-list", str(forcing_list),
                    "--start-date", "2010-01-01 00:00:00",
                    "--end-date", "2010-01-01 02:00:00",
                    "--output-profile", "engineering",
                    "--output-dir", str(root / "soil_output"),
                    "--restart-dir", str(root / "soil_restart"),
                    "--output", str(soil_namelist),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(depth_soil.returncode, 0, depth_soil.stderr)
            soil_text = soil_namelist.read_text()
            self.assertIn("soiltexture_var = 'soil_type_layer'", soil_text)
            self.assertIn("nmp_opt_soil = 2", soil_text)

            with netCDF4.Dataset(static, "a") as dataset:
                dataset.createDimension("azimuth", 90)
                dataset.createVariable("hlm", "f4", ("azimuth", "y", "x"))[:] = 90.0
                dataset.createVariable("svf", "f4", ("y", "x"))[:] = 1.0
                dataset.createVariable("slope_angle", "f4", ("y", "x"))[:] = 0.0
                dataset.createVariable("aspect_angle", "f4", ("y", "x"))[:] = 0.0
                dataset.terrain_radiation_geometry_sha256 = "a" * 64
                dataset.terrain_radiation_horizon_convention = "hlm_zenith_angle_degrees_flat_90"
                dataset.terrain_radiation_search_distance_km = 20.0
            terrain_namelist = root / "terrain_direct_diffuse.nml"
            terrain = subprocess.run(
                [
                    sys.executable, str(RENDERER),
                    "--static-file", str(static),
                    "--forcing-file-list", str(forcing_list),
                    "--start-date", "2010-01-01 00:00:00",
                    "--end-date", "2010-01-01 02:00:00",
                    "--output-profile", "engineering",
                    "--terrain-radiation-profile", "direct-diffuse",
                    "--output-dir", str(root / "terrain_output"),
                    "--restart-dir", str(root / "terrain_restart"),
                    "--output", str(terrain_namelist),
                ],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            self.assertEqual(terrain.returncode, 0, terrain.stderr)
            terrain_text = terrain_namelist.read_text()
            self.assertIn("terrain_shading = .True.", terrain_text)
            self.assertIn("terrain_direct_sw = .True.", terrain_text)
            self.assertIn("terrain_diffuse_sw = .True.", terrain_text)
            self.assertIn("terrain_reflected_sw = .False.", terrain_text)
            self.assertIn("terrain_longwave = .False.", terrain_text)
            self.assertIn("hlm_var = 'hlm'", terrain_text)

            pathway_namelist = root / "pathway.nml"
            pathway = subprocess.run(
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
                    "--output-profile",
                    "engineering",
                    "--sx",
                    "off",
                    "--advect-density",
                    "off",
                    "--restart-from",
                    "2010-01-01 00:00:00",
                    "--restart-override-check",
                    "--output-dir",
                    str(root / "pathway_output"),
                    "--restart-dir",
                    str(root / "pathway_restart"),
                    "--output",
                    str(pathway_namelist),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(pathway.returncode, 0, pathway.stderr)
            pathway_text = pathway_namelist.read_text()
            self.assertIn("Sx = .False.", pathway_text)
            self.assertIn("advect_density = .False.", pathway_text)
            self.assertIn("override_check = .True.", pathway_text)

            mechanism_namelist = root / "mechanism_diagnosis.nml"
            mechanism = subprocess.run(
                [
                    sys.executable,
                    str(RENDERER),
                    "--static-file",
                    str(static),
                    "--rea-l-land-initialization",
                    "--forcing-file-list",
                    str(forcing_list),
                    "--start-date",
                    "2010-01-01 00:00:00",
                    "--end-date",
                    "2010-01-01 02:00:00",
                    "--output-profile",
                    "mechanism_diagnosis",
                    "--output-dir",
                    str(root / "mechanism_output"),
                    "--restart-dir",
                    str(root / "mechanism_restart"),
                    "--output",
                    str(mechanism_namelist),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(mechanism.returncode, 0, mechanism.stderr)
            mechanism_text = mechanism_namelist.read_text()
            for field in (
                "'cldfrac'",
                "'qc'",
                "'qi'",
                "'qr'",
                "'qs'",
                "'qg'",
                "'swcf'",
                "'lwcf'",
                "'tend_th_swrad'",
                "'tend_th_lwrad'",
            ):
                self.assertIn(field, mechanism_text)

            causal_namelist = root / "causal_surface_30min.nml"
            causal = subprocess.run(
                [
                    sys.executable,
                    str(RENDERER),
                    "--static-file", str(static),
                    "--rea-l-land-initialization",
                    "--forcing-file-list", str(forcing_list),
                    "--start-date", "2010-01-01 00:00:00",
                    "--end-date", "2010-01-01 02:00:00",
                    "--output-interval", "1800",
                    "--output-profile", "causal_surface_30min",
                    "--output-dir", str(root / "causal_output"),
                    "--restart-dir", str(root / "causal_restart"),
                    "--output", str(causal_namelist),
                ],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            self.assertEqual(causal.returncode, 0, causal.stderr)
            causal_text = causal_namelist.read_text()
            self.assertIn("outputinterval = 1800", causal_text)
            self.assertIn("'cldfrac'", causal_text)
            self.assertIn("'soil_column_total_water'", causal_text)
            self.assertNotIn("'qc'", causal_text)

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

    def test_qualification_profile_rejects_cold_start(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chunk = root / "chunk"
            plan = chunk / "chunk_plan.json"
            forcing_list = chunk / "forcing_list.txt"
            planner = subprocess.run(
                [
                    sys.executable, str(PLANNER), "--start", "2010-01-01T00:00:00",
                    "--end", "2010-01-01T01:00:00", "--chunk-id", "cold-start",
                    "--chunk-root", str(chunk),
                ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            self.assertEqual(planner.returncode, 0, planner.stderr)
            static = root / "static.nc"
            static.touch()
            Path(f"{static}.ready").touch()
            result = subprocess.run(
                [
                    sys.executable, str(RENDERER), "--static-file", str(static),
                    "--forcing-file-list", str(forcing_list), "--forcing-plan", str(plan),
                    "--start-date", "2010-01-01 00:00:00", "--end-date", "2010-01-01 01:00:00",
                    "--output-profile", "qualification", "--output-dir", str(root / "output"),
                    "--restart-dir", str(root / "restart"), "--output", str(root / "input.nml"),
                ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("requires --rea-l-land-initialization", result.stderr)

    def test_mechanism_diagnosis_profile_contains_discriminating_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chunk = root / "chunk"
            result = subprocess.run(
                [
                    sys.executable, str(PLANNER), "--start", "2010-01-01T00:00:00",
                    "--end", "2010-01-01T03:00:00", "--chunk-id", "diagnosis",
                    "--chunk-root", str(chunk),
                ], text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            static = root / "static.nc"
            static.touch()
            Path(f"{static}.ready").touch()
            namelist = root / "input.nml"
            result = subprocess.run(
                [
                    sys.executable, str(RENDERER), "--static-file", str(static),
                    "--forcing-file-list", str(chunk / "forcing_list.txt"),
                    "--forcing-plan", str(chunk / "chunk_plan.json"),
                    "--start-date", "2010-01-01 00:00:00", "--end-date", "2010-01-01 03:00:00",
                    "--output-profile", "mechanism_diagnosis", "--rea-l-land-initialization",
                    "--output-dir", str(root / "output"), "--restart-dir", str(root / "restart"),
                    "--output", str(namelist),
                ], text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            rendered = namelist.read_text()
            for name in ("'cldfrac'", "'qc'", "'swcf'", "'lwcf'", "'hfss'", "'hfls'", "'soil_column_total_water'"):
                self.assertIn(name, rendered)

    def test_v29_surface_diagnosis_reads_history_without_model_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            static = root / "static.nc"
            with netCDF4.Dataset(static, "w") as dataset:
                dataset.createDimension("y", 2)
                dataset.createDimension("x", 2)
                for name, values in {
                    "landmask": [[1, 1], [1, 0]],
                    "landuse": [[1, 1], [24, 16]],
                    "topo": [[800, 1500], [2200, 500]],
                }.items():
                    variable = dataset.createVariable(name, "f4", ("y", "x"))
                    variable[:] = values
            history = root / "history.nc"
            with netCDF4.Dataset(history, "w") as dataset:
                dataset.createDimension("time", 2)
                dataset.createDimension("y", 2)
                dataset.createDimension("x", 2)
                time = dataset.createVariable("time", "f8", ("time",))
                time.units = "hours since 2020-07-01 00:00:00"
                time[:] = [0, 12]
                for name, values in {
                    "taix": [[[280, 282], [276, 279]], [[290, 292], [286, 289]]],
                    "rsds": [[[0, 0], [0, 0]], [[600, 620], [580, 610]]],
                }.items():
                    variable = dataset.createVariable(name, "f4", ("time", "y", "x"))
                    variable[:] = values
            assessment = root / "assessment.json"
            assessment.write_text(json.dumps({"decision": "HOLD_AND_DIAGNOSE", "failed_science_metrics": {"temperature": {"hicar_bias": 3.6}}}))
            input_namelist = root / "input.nml"
            input_namelist.write_text("""&forcing
  pvar = 'P'
  tvar = 'T'
  qvvar = 'QV'
  uvar = 'U'
  vvar = 'V'
  wvar = 'W'
/
&physics
  mp = 'morrison'
  rad = 'rrtmgp'
  lsm = 'noahmp'
  pbl = 'ysu'
/
""")
            station = root / "station.json"
            station.write_text(json.dumps({"metrics": {"hicar": {"all_sites": {"temperature_2m_height_adjusted_k": {"bias": 3.6}}}}}))
            ogd = root / "ogd.json"
            ogd.write_text(json.dumps({"metrics": {"tabsd": {"hicar": {"all": {"bias": 3.4}}}, "rhiresd": {"hicar": {"all": {"bias": -8.2}}}}}))
            source = root / "source.json"
            source.write_text(json.dumps({"metrics": {"active_soil_interior": {"temperature_2m_height_adjusted_k": {"bias": 2.8}}}}))
            report = root / "diagnosis.json"
            result = subprocess.run(
                [
                    sys.executable, str(V29_DIAGNOSIS), "--static-file", str(static),
                    "--history", str(history), "--assessment", str(assessment),
                    "--input-namelist", str(input_namelist),
                    "--station-report", str(station), "--ogd-report", str(ogd),
                    "--source-report", str(source),
                    "--report", str(report),
                ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            diagnosed = json.loads(report.read_text())
            self.assertEqual(diagnosed["history_records"], 2)
            self.assertTrue(Path(f"{report}.ready").is_file())
            manifest = json.loads(Path(f"{report}.manifest.json").read_text())
            self.assertFalse(manifest["model_rerun"])
            self.assertEqual(diagnosed["frozen_assessment"]["decision"], "HOLD_AND_DIAGNOSE")
            self.assertEqual(diagnosed["ranked_mechanism_hypotheses"][0]["rank"], 1)
            self.assertFalse(diagnosed["follow_up_experiment"]["authorized"])
            separation = diagnosed["mechanism_separation"]
            self.assertEqual(separation["physics"]["rad"], "'rrtmgp'")
            self.assertEqual(separation["forcing_path"]["hydrometeor_mappings_absent"], ["qcvar", "qivar", "qrvar", "qgvar", "qsvar"])
            self.assertEqual(separation["temporal_discrimination"]["decision"], "NOT_SEPARABLE_FROM_RETAINED_ARTIFACTS")
            self.assertIn("2020-07-01T12:00:00", diagnosed["by_valid_time_and_surface_class"])
            self.assertEqual(diagnosed["by_utc_hour_and_surface_class"]["12"]["active_soil_1000_2000m"]["rsds"]["mean_of_record_means"], 620.0)

    def test_mechanism_assessor_requires_matched_rain_snow_and_graupel(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            static = root / "static.nc"
            with netCDF4.Dataset(static, "w") as dataset:
                dataset.createDimension("y", 1)
                dataset.createDimension("x", 1)
                for name, value in {"landmask": 1, "landuse": 1}.items():
                    variable = dataset.createVariable(name, "i4", ("y", "x"))
                    variable[:] = [[value]]
            history = root / "history.nc"
            required_hicar = (
                "cldfrac", "precipitation", "snowfall", "graupel", "qc", "qi", "qr", "qs", "qg",
                "swtb", "swtd", "lwtr", "hfss", "hfls", "soil_column_total_water",
                "soil_water_content", "soil_temperature",
            )
            with netCDF4.Dataset(history, "w") as dataset:
                dataset.createDimension("time", 9)
                dataset.createDimension("y", 1)
                dataset.createDimension("x", 1)
                time = dataset.createVariable("time", "f8", ("time",))
                time.units = "hours since 2020-07-01 00:00:00"
                time[:] = list(range(0, 25, 3))
                for index, name in enumerate(required_hicar):
                    variable = dataset.createVariable(name, "f4", ("time", "y", "x"))
                    variable[:] = [[[float(index + hour)] ] for hour in range(9)]
            reference_dir = root / "reference"
            reference_dir.mkdir()
            required_reference = (
                "cloud_area_fraction_ref", "rain_interval_ref", "snow_interval_ref", "graupel_interval_ref",
                "sw_direct_down_interval_ref", "sw_diffuse_down_interval_ref", "lw_down_interval_ref",
                "latent_heat_flux_interval_ref", "sensible_heat_flux_interval_ref",
            )
            for hour in range(0, 25, 3):
                reference = reference_dir / f"rea_l_surface_reference_202007{1 if hour < 24 else 2:02d}_{hour % 24:02d}00.nc"
                with netCDF4.Dataset(reference, "w") as dataset:
                    dataset.createDimension("time", 1)
                    dataset.createDimension("latitude", 2)
                    dataset.createDimension("longitude", 1)
                    latitude = dataset.createVariable("latitude", "f4", ("latitude",))
                    latitude[:] = [45.0, 47.0]
                    for index, name in enumerate(required_reference):
                        variable = dataset.createVariable(name, "f4", ("time", "latitude", "longitude"))
                        variable[:] = [[[float(index)], [float(index)]]]
                Path(f"{reference}.ready").touch()
            contract = root / "contract.json"
            contract.write_text(json.dumps({"status": "PREDECLARED_NOT_YET_RUN", "baseline": {"output_profile": "mechanism_diagnosis"}}))
            report = root / "assessment.json"
            result = subprocess.run(
                [
                    sys.executable, str(V29_MECHANISM_ASSESSOR), "--contract", str(contract),
                    "--static-file", str(static), "--hicar-history", str(history),
                    "--reference-dir", str(reference_dir), "--report", str(report),
                ], text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            assessed = json.loads(report.read_text())
            self.assertEqual(assessed["status"], "PASS_NON_PROMOTING")
            self.assertEqual(len(assessed["records"]), 9)
            self.assertIn("rain", assessed["records"][1]["precipitation_intervals"]["rea_l"])
            self.assertTrue(Path(f"{report}.ready").is_file())

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
