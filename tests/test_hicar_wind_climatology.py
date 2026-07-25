from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import netCDF4
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
DERIVER_SPEC = importlib.util.spec_from_file_location(
    "derive_hicar_wind_climatology",
    ROOT / "scripts" / "derive_hicar_wind_climatology.py",
)
DERIVER = importlib.util.module_from_spec(DERIVER_SPEC)
assert DERIVER_SPEC.loader is not None
DERIVER_SPEC.loader.exec_module(DERIVER)
VALIDATOR_SPEC = importlib.util.spec_from_file_location(
    "validate_hicar_wind_climatology",
    ROOT / "scripts" / "validate_hicar_wind_climatology.py",
)
VALIDATOR = importlib.util.module_from_spec(VALIDATOR_SPEC)
assert VALIDATOR_SPEC.loader is not None
VALIDATOR_SPEC.loader.exec_module(VALIDATOR)
REDUCER_SPEC = importlib.util.spec_from_file_location(
    "reduce_hicar_wind_climatology",
    ROOT / "scripts" / "reduce_hicar_wind_climatology.py",
)
REDUCER = importlib.util.module_from_spec(REDUCER_SPEC)
assert REDUCER_SPEC.loader is not None
REDUCER_SPEC.loader.exec_module(REDUCER)
MERGER_SPEC = importlib.util.spec_from_file_location(
    "merge_hicar_wind_statistics",
    ROOT / "scripts" / "merge_hicar_wind_statistics.py",
)
MERGER = importlib.util.module_from_spec(MERGER_SPEC)
assert MERGER_SPEC.loader is not None
MERGER_SPEC.loader.exec_module(MERGER)
USER_PRODUCT_SPEC = importlib.util.spec_from_file_location(
    "derive_hicar_wind_user_products",
    ROOT / "scripts" / "derive_hicar_wind_user_products.py",
)
USER_PRODUCT = importlib.util.module_from_spec(USER_PRODUCT_SPEC)
assert USER_PRODUCT_SPEC.loader is not None
USER_PRODUCT_SPEC.loader.exec_module(USER_PRODUCT)


def write_synthetic_case(
    output_path: Path,
    static_path: Path,
    *,
    top_agl_m: float = 300.0,
) -> None:
    nt, nz, ny, nx = 2, 4, 3, 4
    terrain = (
        500.0
        + 20.0 * np.arange(ny, dtype=np.float64)[:, None]
        + 10.0 * np.arange(nx, dtype=np.float64)[None, :]
    )
    agl = np.array([25.0, 75.0, 150.0, top_agl_m], dtype=np.float64)

    with netCDF4.Dataset(static_path, "w") as static:
        static.createDimension("y", ny)
        static.createDimension("x", nx)
        topo = static.createVariable("topo", "f4", ("y", "x"))
        topo.units = "m"
        topo[:, :] = terrain

    with netCDF4.Dataset(output_path, "w") as dataset:
        dataset.createDimension("time", nt)
        dataset.createDimension("level", nz)
        dataset.createDimension("lat_y", ny)
        dataset.createDimension("lon_x", nx)
        dataset.createDimension("lat_v", ny + 1)
        dataset.createDimension("lon_u", nx + 1)

        time = dataset.createVariable("time", "f8", ("time",))
        time[:] = [0.0, 1.0]
        time.units = "hours since 2000-01-01 00:00:00"
        time.calendar = "standard"

        lat = dataset.createVariable("lat", "f4", ("lat_y", "lon_x"))
        lon = dataset.createVariable("lon", "f4", ("lat_y", "lon_x"))
        lat.units = "degrees_north"
        lon.units = "degrees_east"
        lat[:, :] = 46.0 + 0.01 * np.arange(ny)[:, None]
        lon[:, :] = 7.0 + 0.01 * np.arange(nx)[None, :]

        z = dataset.createVariable("z", "f4", ("level", "lat_y", "lon_x"))
        z[:, :, :] = terrain[None, :, :] + agl[:, None, None]

        density = dataset.createVariable(
            "density", "f4", ("time", "level", "lat_y", "lon_x")
        )
        for time_index in range(nt):
            density[time_index, :, :, :] = (
                1.25 - 0.0005 * agl[:, None, None] - 0.01 * time_index
            )

        u = dataset.createVariable(
            "u", "f4", ("time", "level", "lat_y", "lon_u")
        )
        v = dataset.createVariable(
            "v", "f4", ("time", "level", "lat_v", "lon_x")
        )
        x_face = np.arange(nx + 1, dtype=np.float64)
        y_face = np.arange(ny + 1, dtype=np.float64)
        for time_index in range(nt):
            u[time_index, :, :, :] = (
                4.0
                + time_index
                + 0.02 * agl[:, None, None]
                + 2.0 * x_face[None, None, :]
            )
            v[time_index, :, :, :] = (
                -1.0
                + 0.01 * agl[:, None, None]
                + 3.0 * y_face[None, :, None]
            )


def add_online_fields(
    output_path: Path,
    static_path: Path,
    *,
    perturb_u: float = 0.0,
) -> None:
    heights = DERIVER.DEFAULT_HEIGHTS_M
    with netCDF4.Dataset(static_path) as static:
        topo = np.asarray(static["topo"][:], dtype=np.float64)
    with netCDF4.Dataset(output_path, "a") as dataset:
        nt = len(dataset.dimensions["time"])
        nz = len(dataset.dimensions["level"])
        ny = len(dataset.dimensions["lat_y"])
        nx = len(dataset.dimensions["lon_x"])
        dataset.createDimension("height_agl", len(heights))
        height = dataset.createVariable("height_agl", "f4", ("height_agl",))
        height[:] = heights
        variables = {
            name: dataset.createVariable(
                name,
                "f4",
                ("time", "height_agl", "lat_y", "lon_x"),
            )
            for name in ("u_agl", "v_agl", "rho_agl")
        }
        z_agl = np.asarray(dataset["z"][:], dtype=np.float64) - topo[None, :, :]
        for time_index in range(nt):
            u_mass, v_mass, _ = DERIVER._read_mass_winds(
                dataset,
                time_index,
                slice(0, ny),
                nz,
                ny,
                nx,
            )
            density = np.asarray(
                dataset["density"][time_index, :, :, :],
                dtype=np.float64,
            )
            variables["u_agl"][time_index, ...] = DERIVER.interpolate_columns(
                z_agl, u_mass, heights, field_name="u"
            )
            variables["v_agl"][time_index, ...] = DERIVER.interpolate_columns(
                z_agl, v_mass, heights, field_name="v"
            )
            variables["rho_agl"][time_index, ...] = DERIVER.interpolate_columns(
                z_agl, density, heights, field_name="rho"
            )
        if perturb_u:
            variables["u_agl"][0, 0, 0, 0] += perturb_u


def write_fixed_height_stream(
    path: Path,
    *,
    pbl_variables: tuple[str, ...] = REDUCER.PBL_SOURCE_VARIABLES,
) -> None:
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("time", 3)
        dataset.createDimension("height_agl", 6)
        dataset.createDimension("lat_y", 2)
        dataset.createDimension("lon_x", 2)
        time = dataset.createVariable("time", "f8", ("time",))
        time.units = "hours since 2000-01-01 00:00:00"
        time.calendar = "standard"
        time[:] = [0.0, 1.0, 2.0]
        height = dataset.createVariable("height_agl", "f4", ("height_agl",))
        height[:] = REDUCER.EXPECTED_HEIGHTS_M
        lat = dataset.createVariable("lat", "f4", ("lat_y", "lon_x"))
        lon = dataset.createVariable("lon", "f4", ("lat_y", "lon_x"))
        lat.standard_name = "latitude"
        lat.units = "degrees_north"
        lon.standard_name = "longitude"
        lon.units = "degrees_east"
        lat[:] = 46.0
        lon[:] = 7.0
        u = dataset.createVariable(
            "u_agl",
            "f4",
            ("time", "height_agl", "lat_y", "lon_x"),
        )
        v = dataset.createVariable(
            "v_agl",
            "f4",
            ("time", "height_agl", "lat_y", "lon_x"),
        )
        rho = dataset.createVariable(
            "rho_agl",
            "f4",
            ("time", "height_agl", "lat_y", "lon_x"),
        )
        u10 = dataset.createVariable("u10m", "f4", ("time", "lat_y", "lon_x"))
        v10 = dataset.createVariable("v10m", "f4", ("time", "lat_y", "lon_x"))
        u[0], u[1], u[2] = 0.0, 3.0, 4.0
        v[0], v[1], v[2] = 0.0, 4.0, 3.0
        rho[:] = 1.0
        u10[0], u10[1], u10[2] = 0.0, 6.0, 8.0
        v10[0], v10[1], v10[2] = 0.0, 8.0, 6.0
        pbl_values = {
            "ustar": (0.0, 1.0, 3.0),
            "surface_roughness": (0.1, 0.2, 0.4),
            "sfc_Ri": (-0.1, 0.1, 0.3),
            "hpbl": (100.0, 400.0, 800.0),
        }
        for name in pbl_variables:
            variable = dataset.createVariable(
                name,
                "f4",
                ("time", "lat_y", "lon_x"),
            )
            for time_index, value in enumerate(pbl_values[name]):
                variable[time_index] = value


def write_wind_static_companion(
    path: Path,
    *,
    topo_offset_m: float = 0.0,
    longitude_offset_deg: float = 0.0,
) -> None:
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("y", 2)
        dataset.createDimension("x", 2)
        x = dataset.createVariable("x", "f4", ("x",))
        y = dataset.createVariable("y", "f4", ("y",))
        x[:] = [0.0, 200.0]
        y[:] = [0.0, 200.0]
        lat = dataset.createVariable("lat", "f4", ("y", "x"))
        lon = dataset.createVariable("lon", "f4", ("y", "x"))
        topo = dataset.createVariable("topo", "f4", ("y", "x"))
        landmask = dataset.createVariable("landmask", "i2", ("y", "x"))
        landuse = dataset.createVariable("landuse", "i2", ("y", "x"))
        lat[:] = 46.0
        lon[:] = 7.0 + longitude_offset_deg
        topo[:] = 500.0 + topo_offset_m
        landmask[:] = 1
        landuse[:] = 7
        dataset.hicar_dx_m = 200.0
        dataset.hicar_projection = (
            "+proj=aeqd +lat_0=46 +lon_0=7 +x_0=0 +y_0=0 "
            "+datum=WGS84 +units=m +no_defs"
        )
    Path(f"{path}.ready").touch()


def copy_stream_records(source: Path, target: Path, indices: list[int]) -> None:
    with netCDF4.Dataset(source) as original, netCDF4.Dataset(target, "w") as copy:
        copy.createDimension("time", len(indices))
        copy.createDimension("height_agl", 6)
        copy.createDimension("lat_y", 2)
        copy.createDimension("lon_x", 2)
        for name, dimensions in (
            ("time", ("time",)),
            ("height_agl", ("height_agl",)),
            ("lat", ("lat_y", "lon_x")),
            ("lon", ("lat_y", "lon_x")),
            ("u10m", ("time", "lat_y", "lon_x")),
            ("v10m", ("time", "lat_y", "lon_x")),
            ("ustar", ("time", "lat_y", "lon_x")),
            ("surface_roughness", ("time", "lat_y", "lon_x")),
            ("sfc_Ri", ("time", "lat_y", "lon_x")),
            ("hpbl", ("time", "lat_y", "lon_x")),
            ("u_agl", ("time", "height_agl", "lat_y", "lon_x")),
            ("v_agl", ("time", "height_agl", "lat_y", "lon_x")),
            ("rho_agl", ("time", "height_agl", "lat_y", "lon_x")),
        ):
            source_variable = original[name]
            variable = copy.createVariable(name, "f8", dimensions)
            for attribute in source_variable.ncattrs():
                variable.setncattr(attribute, source_variable.getncattr(attribute))
            if "time" in dimensions:
                variable[:] = source_variable[indices, ...]
            else:
                variable[:] = source_variable[:]


class HicarWindClimatologyTests(unittest.TestCase):
    def test_fixed_height_product_destaggers_and_interpolates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            source = work / "hicar.nc"
            static = work / "static.nc"
            target = work / "wind.nc"
            report_path = work / "wind-report.json"
            write_synthetic_case(source, static)

            report = DERIVER.create_product(
                source,
                static,
                target,
                heights_m=(50.0, 100.0, 200.0),
                y_block_size=2,
                report_path=report_path,
            )

            self.assertEqual(report["status"], "PASS")
            self.assertTrue(target.is_file())
            self.assertTrue(report_path.is_file())
            self.assertTrue(Path(f"{target}.ready").is_file())
            with netCDF4.Dataset(target) as dataset:
                self.assertEqual(
                    dataset.product_contract,
                    "hicar-wind-climatology-reference-v1",
                )
                np.testing.assert_allclose(
                    dataset.variables["height_agl"][:],
                    [50.0, 100.0, 200.0],
                )
                height = 100.0
                time_index, y_index, x_index = 1, 2, 3
                expected_u = 4.0 + time_index + 0.02 * height + 2.0 * x_index + 1.0
                expected_v = -1.0 + 0.01 * height + 3.0 * y_index + 1.5
                expected_density = 1.25 - 0.0005 * height - 0.01 * time_index
                expected_speed = np.hypot(expected_u, expected_v)
                actual_index = (time_index, 1, y_index, x_index)
                self.assertAlmostEqual(
                    float(dataset.variables["eastward_wind"][actual_index]),
                    expected_u,
                    places=5,
                )
                self.assertAlmostEqual(
                    float(dataset.variables["northward_wind"][actual_index]),
                    expected_v,
                    places=5,
                )
                self.assertAlmostEqual(
                    float(dataset.variables["air_density"][actual_index]),
                    expected_density,
                    places=5,
                )
                self.assertAlmostEqual(
                    float(dataset.variables["wind_speed"][actual_index]),
                    expected_speed,
                    places=5,
                )
                self.assertAlmostEqual(
                    float(dataset.variables["wind_power_density"][actual_index]),
                    0.5 * expected_density * expected_speed**3,
                    places=3,
                )

    def test_out_of_range_height_is_not_extrapolated_or_published(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            source = work / "hicar.nc"
            static = work / "static.nc"
            target = work / "wind.nc"
            write_synthetic_case(source, static, top_agl_m=180.0)

            with self.assertRaisesRegex(ValueError, "extrapolation is forbidden"):
                DERIVER.create_product(
                    source,
                    static,
                    target,
                    heights_m=(50.0, 200.0),
                )
            self.assertFalse(target.exists())
            self.assertFalse(Path(f"{target}.ready").exists())

    def test_nonmonotone_geometry_is_rejected(self) -> None:
        z_agl = np.array([10.0, 40.0, 30.0])[:, None, None]
        field = np.ones_like(z_agl)
        with self.assertRaisesRegex(ValueError, "not strictly increasing"):
            DERIVER.interpolate_columns(
                z_agl,
                field,
                (20.0,),
                field_name="test field",
            )

    def test_online_fields_match_reference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            source = work / "hicar.nc"
            static = work / "static.nc"
            write_synthetic_case(source, static)
            add_online_fields(source, static)

            report = VALIDATOR.validate_file(
                source,
                static,
                absolute_tolerance=2.0e-6,
                y_block_size=2,
            )
            self.assertEqual(report["status"], "PASS")
            self.assertLess(
                report["variables"]["u_agl"]["maximum_absolute_error"],
                2.0e-6,
            )

    def test_online_reference_difference_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            source = work / "hicar.nc"
            static = work / "static.nc"
            write_synthetic_case(source, static)
            add_online_fields(source, static, perturb_u=0.01)

            report = VALIDATOR.validate_file(
                source,
                static,
                absolute_tolerance=2.0e-6,
            )
            self.assertEqual(report["status"], "FAIL")
            self.assertTrue(
                any("u_agl maximum absolute error" in item for item in report["failures"])
            )

    def test_interval_reducer_has_exact_bounds_and_non_gust_maximum(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            source = work / "fixed-height.nc"
            target = work / "daily.nc"
            report_path = work / "daily.json"
            write_fixed_height_stream(source)

            report = REDUCER.reduce_product(
                source,
                target,
                interval_seconds=7200,
                y_block_size=1,
                report_path=report_path,
            )

            self.assertEqual(report["status"], "PASS")
            self.assertEqual(report["samples_per_interval"], [2])
            self.assertTrue(report["surface_pbl_statistics"])
            self.assertTrue(Path(f"{target}.ready").is_file())
            self.assertTrue(Path(f"{report_path}.ready").is_file())
            with netCDF4.Dataset(target) as dataset:
                np.testing.assert_allclose(
                    dataset["time_bounds"][:],
                    [[0.0, 2.0]],
                )
                self.assertEqual(int(dataset["sample_count"][0]), 2)
                self.assertAlmostEqual(
                    float(dataset["eastward_wind_mean"][0, 0, 0, 0]),
                    3.5,
                )
                self.assertAlmostEqual(
                    float(dataset["northward_wind_mean"][0, 0, 0, 0]),
                    3.5,
                )
                self.assertAlmostEqual(
                    float(dataset["wind_speed_mean"][0, 0, 0, 0]),
                    5.0,
                )
                self.assertAlmostEqual(
                    float(
                        dataset["wind_speed_standard_deviation"][0, 0, 0, 0]
                    ),
                    0.0,
                )
                self.assertAlmostEqual(
                    float(dataset["wind_power_density_mean"][0, 0, 0, 0]),
                    62.5,
                )
                self.assertAlmostEqual(
                    float(dataset["resolved_wind_speed_max"][0, 0, 0, 0]),
                    5.0,
                )
                self.assertAlmostEqual(
                    float(dataset["resolved_wind_speed_10m_max"][0, 0, 0]),
                    10.0,
                )
                self.assertAlmostEqual(
                    float(dataset["friction_velocity_mean"][0, 0, 0]),
                    2.0,
                )
                self.assertAlmostEqual(
                    float(dataset["friction_velocity_max"][0, 0, 0]),
                    3.0,
                )
                self.assertAlmostEqual(
                    float(dataset["surface_roughness_length_mean"][0, 0, 0]),
                    0.3,
                    places=6,
                )
                self.assertAlmostEqual(
                    float(
                        dataset["surface_bulk_richardson_number_mean"][0, 0, 0]
                    ),
                    0.2,
                    places=6,
                )
                self.assertAlmostEqual(
                    float(dataset["boundary_layer_height_mean"][0, 0, 0]),
                    600.0,
                )
                self.assertAlmostEqual(
                    float(dataset["boundary_layer_height_max"][0, 0, 0]),
                    800.0,
                )
                self.assertEqual(
                    dataset["friction_velocity_mean"].standard_name,
                    "magnitude_of_surface_friction_velocity_in_air",
                )
                self.assertEqual(
                    dataset["friction_velocity_mean"].coordinates,
                    "lat lon",
                )
                np.testing.assert_array_equal(
                    dataset["wind_distribution_height_agl"][:],
                    [10, 50, 75, 100, 125, 150, 200],
                )
                self.assertEqual(
                    int(
                        dataset["wind_from_direction_sector_count"][
                            0, 0, 7, 0, 0
                        ]
                    ),
                    1,
                )
                self.assertEqual(
                    int(
                        dataset["wind_from_direction_sector_count"][
                            0, 0, 8, 0, 0
                        ]
                    ),
                    1,
                )
                np.testing.assert_array_equal(
                    dataset["wind_speed_threshold_exceedance_count"][
                        0, 0, :, 0, 0
                    ],
                    [2, 2, 2, 0, 0, 0],
                )
                np.testing.assert_array_equal(
                    dataset["wind_speed_threshold_exceedance_count"][
                        0, 1, :, 0, 0
                    ],
                    [2, 2, 0, 0, 0, 0],
                )
                self.assertEqual(
                    int(dataset["calm_wind_count"][0, 0, 0, 0]),
                    0,
                )
                self.assertEqual(
                    int(
                        np.sum(
                            dataset["wind_from_direction_sector_count"][
                                0, 0, :, 0, 0
                            ]
                        )
                    ),
                    2,
                )
                self.assertIn(
                    "not a 3-second gust",
                    dataset["resolved_wind_speed_max"].comment,
                )
                self.assertEqual(
                    dataset["resolved_wind_speed_max"].cell_methods,
                    "time: maximum",
                )

    def test_interval_reducer_rejects_partial_intervals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            source = work / "fixed-height.nc"
            target = work / "partial.nc"
            write_fixed_height_stream(source)

            with self.assertRaisesRegex(
                ValueError,
                "multiple of the sample interval",
            ):
                REDUCER.reduce_product(
                    source,
                    target,
                    interval_seconds=5400,
                )
            self.assertFalse(target.exists())

    def test_interval_reducer_rejects_incomplete_pbl_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            source = work / "fixed-height.nc"
            target = work / "partial-pbl.nc"
            write_fixed_height_stream(source, pbl_variables=("ustar",))

            with self.assertRaisesRegex(
                ValueError,
                "incomplete surface/PBL diagnostic set",
            ):
                REDUCER.reduce_product(
                    source,
                    target,
                    interval_seconds=7200,
                )
            self.assertFalse(target.exists())

    def test_static_domain_is_hash_bound_and_propagated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            source = work / "fixed-height.nc"
            static = work / "static.nc"
            reduced = work / "reduced.nc"
            merged = work / "merged.nc"
            derived = work / "derived.nc"
            write_fixed_height_stream(source)
            write_wind_static_companion(static)

            reduced_report = REDUCER.reduce_product(
                source,
                reduced,
                interval_seconds=3600,
                static_file=static,
            )
            merged_report = MERGER.merge_products(
                (reduced,),
                merged,
                group_by="all",
            )
            derived_report = USER_PRODUCT.create_product(merged, derived)

            identity = reduced_report["static_domain"]
            self.assertIsNotNone(identity)
            self.assertEqual(identity["static_domain_sha256"], REDUCER._sha256(static))
            self.assertEqual(identity["static_domain_grid_ny"], 2)
            self.assertEqual(identity["static_domain_grid_nx"], 2)
            self.assertEqual(
                merged_report["static_domain"]["static_domain_sha256"],
                identity["static_domain_sha256"],
            )
            self.assertEqual(
                derived_report["static_domain"]["static_domain_sha256"],
                identity["static_domain_sha256"],
            )
            for path in (reduced, merged, derived):
                with netCDF4.Dataset(path) as dataset:
                    self.assertEqual(
                        dataset.static_domain_sha256,
                        identity["static_domain_sha256"],
                    )
                    self.assertIn(
                        "deterministic derivatives",
                        dataset.static_domain_companion_policy,
                    )

    def test_static_domain_generator_writes_projected_crs_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            static = Path(directory) / "static.nc"
            subprocess.run(
                (
                    sys.executable,
                    str(ROOT / "scripts" / "prepare_static_inputs.py"),
                    "--output",
                    str(static),
                    "--width-km",
                    "0.4",
                    "--height-km",
                    "0.4",
                    "--dx-m",
                    "200",
                    "--static-field-set",
                    "core",
                    "--allow-placeholder-static",
                ),
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertTrue(Path(f"{static}.ready").is_file())
            with netCDF4.Dataset(static) as dataset:
                self.assertEqual(
                    dataset["x"].standard_name,
                    "projection_x_coordinate",
                )
                self.assertEqual(
                    dataset["y"].standard_name,
                    "projection_y_coordinate",
                )
                self.assertEqual(
                    dataset["azimuthal_equidistant"].grid_mapping_name,
                    "azimuthal_equidistant",
                )
                self.assertEqual(
                    dataset["topo"].grid_mapping,
                    "azimuthal_equidistant",
                )
                self.assertIn("+proj=aeqd", dataset.hicar_projection)

    def test_interval_reducer_rejects_invalid_static_companion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            source = work / "fixed-height.nc"
            static = work / "static.nc"
            target = work / "reduced.nc"
            write_fixed_height_stream(source)
            write_wind_static_companion(static, longitude_offset_deg=0.1)

            with self.assertRaisesRegex(
                ValueError,
                "static-domain lon does not match",
            ):
                REDUCER.reduce_product(
                    source,
                    target,
                    interval_seconds=7200,
                    static_file=static,
                )
            self.assertFalse(target.exists())

            Path(f"{static}.ready").unlink()
            with self.assertRaisesRegex(
                ValueError,
                "static-domain publication is incomplete",
            ):
                REDUCER.reduce_product(
                    source,
                    target,
                    interval_seconds=7200,
                    static_file=static,
                )

    def test_compact_merger_rejects_different_static_companions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            source = work / "fixed-height.nc"
            first_source = work / "first-source.nc"
            second_source = work / "second-source.nc"
            first_static = work / "first-static.nc"
            second_static = work / "second-static.nc"
            first = work / "first.nc"
            second = work / "second.nc"
            merged = work / "merged.nc"
            write_fixed_height_stream(source)
            copy_stream_records(source, first_source, [0, 1])
            copy_stream_records(source, second_source, [1, 2])
            write_wind_static_companion(first_static)
            write_wind_static_companion(second_static, topo_offset_m=1.0)
            REDUCER.reduce_product(
                first_source,
                first,
                interval_seconds=3600,
                static_file=first_static,
            )
            REDUCER.reduce_product(
                second_source,
                second,
                interval_seconds=3600,
                static_file=second_static,
            )

            with self.assertRaisesRegex(
                ValueError,
                "different static-domain companions",
            ):
                MERGER.merge_products((first, second), merged, group_by="all")
            self.assertFalse(merged.exists())

    def test_distribution_counts_handle_calm_and_north_sector_wrap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            source = work / "fixed-height.nc"
            target = work / "distribution.nc"
            write_fixed_height_stream(source)
            with netCDF4.Dataset(source, "a") as dataset:
                for u_name, v_name in (("u10m", "v10m"), ("u_agl", "v_agl")):
                    dataset[u_name][1] = 0.0
                    dataset[v_name][1] = -1.0
                    dataset[u_name][2] = 0.0
                    dataset[v_name][2] = 0.0

            REDUCER.reduce_product(
                source,
                target,
                interval_seconds=7200,
            )

            with netCDF4.Dataset(target) as dataset:
                for height_index in range(
                    len(REDUCER.DISTRIBUTION_HEIGHTS_M)
                ):
                    self.assertEqual(
                        int(
                            dataset["wind_from_direction_sector_count"][
                                0, height_index, 0, 0, 0
                            ]
                        ),
                        1,
                    )
                    self.assertEqual(
                        int(
                            np.sum(
                                dataset["wind_from_direction_sector_count"][
                                    0, height_index, :, 0, 0
                                ]
                            )
                        ),
                        1,
                    )
                    self.assertEqual(
                        int(
                            dataset["calm_wind_count"][
                                0, height_index, 0, 0
                            ]
                        ),
                        1,
                    )

    def test_interval_reducer_spans_multiple_hicar_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            source = work / "fixed-height.nc"
            first = work / "first.nc"
            second = work / "second.nc"
            target = work / "combined.nc"
            write_fixed_height_stream(source)
            copy_stream_records(source, first, [0, 1])
            copy_stream_records(source, second, [2])

            REDUCER.reduce_product(
                (first, second),
                target,
                interval_seconds=7200,
                y_block_size=1,
            )

            with netCDF4.Dataset(target) as dataset:
                self.assertEqual(int(dataset["sample_count"][0]), 2)
                self.assertAlmostEqual(
                    float(dataset["wind_speed_mean"][0, 0, 0, 0]),
                    5.0,
                )

    def test_interval_reducer_accepts_restart_continuation_without_boundary_record(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            source = work / "fixed-height.nc"
            continuation = work / "continuation.nc"
            target = work / "restart-reduced.nc"
            write_fixed_height_stream(source)
            copy_stream_records(source, continuation, [1, 2])

            REDUCER.reduce_product(
                continuation,
                target,
                interval_seconds=7200,
                interval_start="2000-01-01T00:00:00",
            )

            with netCDF4.Dataset(target) as dataset:
                np.testing.assert_allclose(dataset["time_bounds"][:], [[0.0, 2.0]])
                self.assertEqual(int(dataset["sample_count"][0]), 2)
                self.assertAlmostEqual(
                    float(dataset["resolved_wind_speed_max"][0, 0, 0, 0]),
                    5.0,
                )

    def test_interval_reducer_canonicalizes_subsecond_hicar_time_drift(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            source = work / "fixed-height.nc"
            target = work / "canonical-time.nc"
            write_fixed_height_stream(source)
            with netCDF4.Dataset(source, "a") as dataset:
                dataset["time"][:] = np.asarray(dataset["time"][:]) + 0.00012

            report = REDUCER.reduce_product(
                source,
                target,
                interval_seconds=7200,
                interval_start="2000-01-01T00:00:00",
            )

            self.assertTrue(report["time_coordinate_canonicalized"])
            self.assertEqual(report["sample_interval_seconds"], 3600.0)
            with netCDF4.Dataset(target) as dataset:
                np.testing.assert_allclose(dataset["time"][:], [2.0])
                np.testing.assert_allclose(dataset["time_bounds"][:], [[0.0, 2.0]])

    def test_restart_reducer_canonicalizes_varying_subsecond_time_drift(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            source = work / "fixed-height.nc"
            continuation = work / "continuation.nc"
            target = work / "restart-canonical-time.nc"
            write_fixed_height_stream(source)
            copy_stream_records(source, continuation, [1, 2])
            with netCDF4.Dataset(continuation, "a") as dataset:
                dataset["time"][:] = np.asarray(dataset["time"][:]) + np.asarray(
                    [0.432, 0.432013]
                ) / 3600.0

            report = REDUCER.reduce_product(
                continuation,
                target,
                interval_seconds=7200,
                interval_start="2000-01-01T00:00:00",
            )

            self.assertTrue(report["time_coordinate_canonicalized"])
            self.assertEqual(report["sample_interval_seconds"], 3600.0)
            with netCDF4.Dataset(target) as dataset:
                np.testing.assert_allclose(dataset["time"][:], [2.0])
                np.testing.assert_allclose(dataset["time_bounds"][:], [[0.0, 2.0]])

    def test_compact_merger_matches_direct_two_hour_reduction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            source = work / "fixed-height.nc"
            hourly = work / "hourly.nc"
            direct = work / "direct.nc"
            merged = work / "merged.nc"
            write_fixed_height_stream(source)
            REDUCER.reduce_product(source, hourly, interval_seconds=3600)
            REDUCER.reduce_product(source, direct, interval_seconds=7200)

            report = MERGER.merge_products(
                (hourly,),
                merged,
                group_by="all",
                y_block_size=1,
            )

            self.assertEqual(report["samples_per_group"], [2])
            self.assertTrue(report["surface_pbl_statistics"])
            self.assertTrue(Path(f"{merged}.ready").is_file())
            self.assertTrue(Path(f"{merged}.json.ready").is_file())
            with netCDF4.Dataset(direct) as expected, netCDF4.Dataset(merged) as actual:
                for name in (
                    *REDUCER.FIXED_HEIGHT_STATISTICS,
                    *REDUCER.TEN_METRE_STATISTICS,
                    *REDUCER.PBL_STATISTICS,
                    *REDUCER.DISTRIBUTION_VARIABLES,
                ):
                    np.testing.assert_allclose(
                        actual[name][:],
                        expected[name][:],
                        rtol=1.0e-6,
                        atol=1.0e-6,
                    )
                np.testing.assert_array_equal(actual["sample_count"][:], [2])
                np.testing.assert_array_equal(
                    actual["contributing_interval_count"][:],
                    [2],
                )
                np.testing.assert_allclose(
                    actual["time_bounds"][:],
                    expected["time_bounds"][:],
                )

    def test_compact_merger_partitions_records_by_calendar_month(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            source = work / "fixed-height.nc"
            hourly = work / "hourly.nc"
            merged = work / "monthly.nc"
            write_fixed_height_stream(source)
            with netCDF4.Dataset(source, "a") as dataset:
                dataset["time"].units = "hours since 2000-01-31 23:00:00"
            REDUCER.reduce_product(source, hourly, interval_seconds=3600)

            report = MERGER.merge_products(
                (hourly,),
                merged,
                group_by="month",
            )

            self.assertEqual(report["group_count"], 2)
            with netCDF4.Dataset(merged) as dataset:
                np.testing.assert_array_equal(dataset["sample_count"][:], [1, 1])
                np.testing.assert_array_equal(
                    dataset["contributing_interval_count"][:],
                    [1, 1],
                )

    def test_user_product_supplies_direction_variability_shear_and_veer(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            source = work / "fixed-height.nc"
            reduced = work / "reduced.nc"
            derived = work / "derived.nc"
            write_fixed_height_stream(source)
            REDUCER.reduce_product(source, reduced, interval_seconds=7200)

            report = USER_PRODUCT.create_product(
                reduced,
                derived,
                y_block_size=1,
            )

            self.assertEqual(report["status"], "PASS")
            self.assertTrue(Path(f"{derived}.ready").is_file())
            with netCDF4.Dataset(derived) as dataset:
                self.assertAlmostEqual(
                    float(
                        dataset["wind_from_direction_of_vector_mean"][
                            0, 0, 0, 0
                        ]
                    ),
                    225.0,
                )
                self.assertAlmostEqual(
                    float(
                        dataset["wind_speed_coefficient_of_variation"][
                            0, 0, 0, 0
                        ]
                    ),
                    0.0,
                )
                np.testing.assert_allclose(
                    dataset["wind_shear_exponent_of_mean_speed"][0, :, 0, 0],
                    0.0,
                )
                np.testing.assert_allclose(
                    dataset["wind_directional_veer_of_vector_mean"][
                        0, :, 0, 0
                    ],
                    0.0,
                )


if __name__ == "__main__":
    unittest.main()
