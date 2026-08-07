from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import netCDF4
import numpy as np

from preprocessing.hicarprep.balance import (
    BalanceCertificate,
    PRESSURE_OPERATOR,
    STAGGERING,
    WIND_OPERATOR,
    issue_balance_certificate,
    load_hicar_initialized_state,
    state_fingerprint,
)
from preprocessing.hicarprep.boundary import validate_boundary_sequence
from preprocessing.hicarprep.cli import parser as hicarprep_parser
from preprocessing.hicarprep.geometry import SleveConfig, build_sleve_geometry
from preprocessing.hicarprep.external import append_epoch, evaluate_external_fields
from preprocessing.hicarprep.pipeline import (
    boundary_relaxation_weights,
    boundary_point_indices,
    convert_water_to_hicar_mixing_ratios,
    load_valid_time_inputs,
    transform_icon_state,
    write_boundary_condition,
    write_initial_condition,
)
from preprocessing.hicarprep.products import (
    assemble_hicar_runtime_domain,
    append_sleve_geometry,
    partition_domain_inputs,
    validate_hicar_runtime_domain,
    validate_product_lifetimes,
    validate_product_set,
)
from preprocessing.hicarprep.registry import FieldLifetime, FieldRegistry
from preprocessing.hicarprep.remap import (
    RBFWeights,
    build_rbf_weights,
    build_vector_rbf_weights,
    coordinates_in_degrees,
    grid_fingerprint,
    reconstruct_vector_from_normals,
)
from preprocessing.hicarprep.vertical import (
    adjust_vertical_velocity,
    hydrostatic_residual,
    reconstruct_column_state,
    saturation_specific_humidity,
)
from preprocessing.hicarprep.surface import (
    HICAR_SOIL_BOUNDS_M,
    ICON_TERRA_FIELD_CAPACITY,
    ICON_TERRA_POROSITY,
    ICON_TERRA_WILTING_POINT,
    ICON_T_SO_DEPTHS_M,
    ICON_W_SO_BOUNDS_M,
    _supported_remap,
    icon_soil_water_to_relative_saturation,
    icon_soil_water_to_smi,
    noahmp_relative_saturation_to_vwc,
    parse_noahmp_stas_hydraulics,
    prepare_surface_state,
)
from preprocessing.hicarprep.surface_validation import validate_surface_case


class RegistryAndStaticTests(unittest.TestCase):
    def test_registry_separates_long_run_lifetimes(self) -> None:
        registry = FieldRegistry.default()
        self.assertIs(registry.get("topo").lifetime, FieldLifetime.INVARIANT)
        self.assertIs(registry.get("landuse").lifetime, FieldLifetime.EPOCH)
        self.assertIs(registry.get("ROOTDP").lifetime, FieldLifetime.EPOCH)
        self.assertIs(registry.get("Z0").lifetime, FieldLifetime.EPOCH)
        self.assertIs(registry.get("LAI").lifetime, FieldLifetime.CLIMATOLOGY)
        self.assertIs(registry.get("NDVI").lifetime, FieldLifetime.CLIMATOLOGY)
        self.assertIs(registry.get("SST").lifetime, FieldLifetime.TIME_SERIES)
        self.assertIs(registry.get("soil_vwc").lifetime, FieldLifetime.INITIAL_ONLY)
        with self.assertRaisesRegex(KeyError, "classify it explicitly"):
            registry.get("mystery_static")

    def test_domain_partition_removes_initial_state_from_static(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.nc"
            static = root / "static.nc"
            external = root / "external.nc"
            initial = root / "initial_surface.nc"
            with netCDF4.Dataset(source, "w") as dataset:
                dataset.createDimension("x", 3)
                dataset.createDimension("y", 2)
                dataset.createDimension("soil_layer", 4)
                dataset.createVariable("x", "f8", ("x",))[:] = [0, 200, 400]
                dataset.createVariable("y", "f8", ("y",))[:] = [0, 200]
                for name, value in (("lat", 46.0), ("lon", 8.0), ("topo", 500.0)):
                    dataset.createVariable(name, "f8", ("y", "x"))[:] = value
                dataset["lat"].units = "degrees_north"
                dataset["lon"].units = "degrees_east"
                dataset["topo"].units = "m"
                dataset.createVariable("landmask", "i2", ("y", "x"))[:] = 1
                dataset.createVariable("landuse", "i2", ("y", "x"))[:] = 7
                dataset.createVariable("soil_type", "i2", ("y", "x"))[:] = 6
                dataset.createVariable("soil_layer", "i2", ("soil_layer",))[:] = range(4)
                dataset.createVariable("soil_temperature", "f8", ("soil_layer", "y", "x"))[:] = (
                    280.0
                )
                dataset.createVariable("soil_vwc", "f8", ("soil_layer", "y", "x"))[:] = 0.3
                dataset["soil_temperature"].units = "K"
                dataset["soil_vwc"].units = "m3 m-3"

            partition_domain_inputs(
                source,
                static_path=static,
                external_path=external,
                initial_surface_path=initial,
                epoch_valid_from=dt.datetime(2021, 1, 1, tzinfo=dt.timezone.utc),
            )
            append_sleve_geometry(
                static,
                config=SleveConfig(
                    nz=8,
                    model_top_m=4000.0,
                    lowest_layer_m=40.0,
                    smooth_cycles=0,
                ),
            )
            for path in (static, external, initial):
                validate_product_lifetimes(path)
            validate_product_set(static, external, initial)
            with netCDF4.Dataset(static) as dataset:
                self.assertNotIn("soil_vwc", dataset.variables)
                self.assertNotIn("landuse", dataset.variables)
                self.assertEqual(dataset["HHL"].shape, (9, 2, 3))
            with netCDF4.Dataset(external) as dataset:
                self.assertIn("landuse", dataset.variables)
                self.assertNotIn("soil_vwc", dataset.variables)
                self.assertEqual(dataset["landuse"].dimensions, ("epoch", "y", "x"))
                self.assertEqual(dataset["epoch_time"].shape, (1,))
            with netCDF4.Dataset(initial) as dataset:
                self.assertIn("soil_vwc", dataset.variables)
                self.assertIn("soil_temperature", dataset.variables)

            later = root / "later_landuse.nc"
            with netCDF4.Dataset(later, "w") as dataset:
                dataset.createDimension("x", 3)
                dataset.createDimension("y", 2)
                dataset.createVariable("landuse", "i2", ("y", "x"))[:] = 15
            append_epoch(
                external,
                later,
                valid_from=dt.datetime(2030, 1, 1, tzinfo=dt.timezone.utc),
            )
            early = evaluate_external_fields(
                external, dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc)
            )
            late = evaluate_external_fields(
                external, dt.datetime(2035, 1, 1, tzinfo=dt.timezone.utc)
            )
            np.testing.assert_array_equal(early["landuse"], 7)
            np.testing.assert_array_equal(late["landuse"], 15)

    def test_sleve_top_is_flat_and_geometry_positive(self) -> None:
        terrain = np.array([[400.0, 600.0], [900.0, 1200.0]])
        geometry = build_sleve_geometry(
            terrain,
            SleveConfig(
                nz=12,
                model_top_m=6000.0,
                lowest_layer_m=30.0,
                smooth_cycles=2,
                smooth_window_radius=1,
            ),
        )
        np.testing.assert_allclose(geometry["HHL"][0], terrain)
        np.testing.assert_allclose(geometry["HHL"][-1], 6000.0, atol=1.0e-8)
        self.assertGreater(float(np.min(geometry["LAYER_THICKNESS"])), 0.0)
        self.assertGreater(float(np.min(geometry["SLEVE_JACOBIAN"])), 0.0)

    def test_climatology_and_continuous_external_fields_vary_with_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "external.nc"
            with netCDF4.Dataset(path, "w") as dataset:
                dataset.createDimension("epoch", None)
                dataset.createDimension("month", 12)
                dataset.createDimension("time", 2)
                dataset.createDimension("y", 1)
                dataset.createDimension("x", 1)
                dataset.product_type = "time_varying_external_parameters"
                dataset.createVariable("epoch_time", "f8", ("epoch",))[:] = [0.0]
                dataset.createVariable("month", "i2", ("month",))[:] = np.arange(1, 13)
                time = dataset.createVariable("time", "f8", ("time",))
                time.units = "seconds since 1970-01-01 00:00:00 UTC"
                time[:] = [0.0, 86_400.0]
                dataset.createVariable("LAI", "f8", ("month", "y", "x"))[:] = np.arange(12)[
                    :, None, None
                ]
                dataset.createVariable("SST", "f8", ("time", "y", "x"))[:] = np.array(
                    [280.0, 284.0]
                )[:, None, None]
            sampled = evaluate_external_fields(
                path, dt.datetime(1970, 1, 1, 12, tzinfo=dt.timezone.utc)
            )
            self.assertAlmostEqual(float(sampled["SST"][0, 0]), 282.0)
            expected_lai = (1.0 - 17.5 / 31.0) * 11.0
            self.assertAlmostEqual(float(sampled["LAI"][0, 0]), expected_lai)

    def test_epoch_append_rejects_incomplete_coupled_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            external = root / "external.nc"
            update = root / "update.nc"
            with netCDF4.Dataset(external, "w") as dataset:
                dataset.createDimension("epoch", None)
                dataset.createDimension("y", 1)
                dataset.createDimension("x", 1)
                dataset.product_type = "time_varying_external_parameters"
                dataset.createVariable("epoch_time", "f8", ("epoch",))[:] = [0.0]
                for name in ("glacier_fraction", "urban_fraction"):
                    variable = dataset.createVariable(name, "f8", ("epoch", "y", "x"))
                    variable[:] = 0.0
                    variable.hicar_lifetime = "epoch"
            with netCDF4.Dataset(update, "w") as dataset:
                dataset.createDimension("y", 1)
                dataset.createDimension("x", 1)
                dataset.createVariable("glacier_fraction", "f8", ("y", "x"))[:] = 0.1
            with self.assertRaisesRegex(ValueError, "complete record"):
                append_epoch(
                    external,
                    update,
                    valid_from=dt.datetime(2030, 1, 1, tzinfo=dt.timezone.utc),
                )


class HorizontalRemapTests(unittest.TestCase):
    def setUp(self) -> None:
        lat, lon = np.meshgrid(np.linspace(46.0, 46.3, 4), np.linspace(7.8, 8.2, 5), indexing="ij")
        self.source_lat = lat
        self.source_lon = lon
        self.target_lat = np.array([[46.08, 46.11], [46.17, 46.22]])
        self.target_lon = np.array([[7.91, 8.03], [8.08, 8.15]])

    def test_rbf_preserves_constants_and_cache_round_trip(self) -> None:
        operator = build_rbf_weights(
            self.source_lat, self.source_lon, self.target_lat, self.target_lon, donors=10
        )
        result = operator.apply(np.full(self.source_lat.size, 17.25))
        np.testing.assert_allclose(result, 17.25, atol=1.0e-12)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "weights.nc"
            operator.write(path)
            restored = RBFWeights.read(path)
            np.testing.assert_array_equal(restored.donor_index, operator.donor_index)
            np.testing.assert_allclose(restored.weight, operator.weight)
            self.assertEqual(restored.method, "int2lm_gaussian_kernel_solve_nearest10_v2")
            self.assertGreater(restored.scale_radians, 0.0)

    def test_native_radian_coordinates_are_normalized(self) -> None:
        np.testing.assert_allclose(
            coordinates_in_degrees(np.array([0.0, np.pi / 2.0]), "radian"),
            [0.0, 90.0],
        )

    def test_rbf_rejects_target_without_padded_source_coverage(self) -> None:
        with self.assertRaisesRegex(ValueError, "covered"):
            build_rbf_weights(
                self.source_lat,
                self.source_lon,
                np.array([[60.0]]),
                np.array([[20.0]]),
                donors=10,
            )

    def test_vector_normal_reconstruction_recovers_constant_wind(self) -> None:
        angle = np.linspace(0.0, 2.0 * np.pi, self.source_lat.size, endpoint=False)
        east = np.cos(angle)
        north = np.sin(angle)
        operator = build_vector_rbf_weights(
            self.source_lat,
            self.source_lon,
            east,
            north,
            self.target_lat,
            self.target_lon,
            donors=9,
        )
        vn = 7.0 * east - 3.0 * north
        u, v = reconstruct_vector_from_normals(vn, operator)
        np.testing.assert_allclose(u, 7.0, atol=1.5e-2)
        np.testing.assert_allclose(v, -3.0, atol=1.5e-2)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "vector_weights.nc"
            operator.write(path)
            restored = type(operator).read(path)
            rebuilt_u, rebuilt_v = restored.apply(vn)
            np.testing.assert_allclose(rebuilt_u, u)
            np.testing.assert_allclose(rebuilt_v, v)


class VerticalTransformTests(unittest.TestCase):
    def _source(self, terrain: float = 500.0):
        hhl = terrain + np.array([0.0, 200.0, 600.0, 1400.0, 3000.0, 6000.0])
        z = 0.5 * (hhl[:-1] + hhl[1:])
        t = 285.0 - 0.006 * (z - terrain)
        p = 95_000.0 * np.exp(-(z - terrain) / 8200.0)
        qv = np.linspace(0.008, 0.001, z.size)
        return hhl, t, p, qv

    def test_saturation_uses_icon_specific_humidity_not_mixing_ratio(self) -> None:
        temperature = np.array([300.0])
        pressure = np.array([100_000.0])
        vapor_pressure = 610.94 * np.exp(17.625 * (300.0 - 273.15) / (300.0 - 273.15 + 243.04))
        expected = 0.622 * vapor_pressure / (100_000.0 - 0.378 * vapor_pressure)
        np.testing.assert_allclose(
            saturation_specific_humidity(temperature, pressure), expected, rtol=1.0e-14
        )

    def test_target_valley_and_mountain_are_hydrostatically_balanced(self) -> None:
        source_hhl, t, p, qv = self._source()
        for target_surface in (-300.0, 1100.0):
            target_hhl = target_surface + np.array([0.0, 100.0, 350.0, 900.0, 1800.0, 3500.0])
            state, diagnostics = reconstruct_column_state(
                source_hhl_m=source_hhl,
                target_hhl_m=target_hhl,
                temperature_k=t,
                pressure_pa=p,
                qv=qv,
                u_ms=np.linspace(2.0, 10.0, t.size),
                v_ms=np.linspace(-1.0, 3.0, t.size),
                hydrometeors={"QC": np.linspace(0.0, 1.0e-4, t.size)},
            )
            target_z = 0.5 * (target_hhl[:-1] + target_hhl[1:])
            np.testing.assert_allclose(
                hydrostatic_residual(
                    target_z,
                    state["P"],
                    state["T"],
                    state["QV"],
                    condensate=state["QC"],
                ),
                0.0,
                atol=2.0e-12,
            )
            self.assertTrue(np.all(state["QV"] >= 0.0))
            self.assertTrue(
                np.all(
                    state["QV"] <= saturation_specific_humidity(state["T"], state["P"]) + 1.0e-10
                )
            )
            self.assertTrue(np.all(state["QC"] >= 0.0))
            self.assertEqual(
                diagnostics.terrain_case, "lower" if target_surface < 500 else "higher"
            )

    def test_generalized_rh_splits_cloud_water_after_pressure_reconstruction(self) -> None:
        source_hhl, t, p, _ = self._source()
        qsat = saturation_specific_humidity(t, p)
        qv = 0.98 * qsat
        qc = 0.12 * qsat
        target_hhl = 200.0 + np.array([0.0, 150.0, 500.0, 1200.0, 2600.0, 5000.0])
        state, _ = reconstruct_column_state(
            source_hhl_m=source_hhl,
            target_hhl_m=target_hhl,
            temperature_k=t,
            pressure_pa=p,
            qv=qv,
            u_ms=np.ones_like(t),
            v_ms=np.ones_like(t),
            hydrometeors={"QC": qc},
        )
        target_qsat = saturation_specific_humidity(state["T"], state["P"])
        self.assertTrue(np.all(state["QV"] <= target_qsat + 1.0e-12))
        self.assertTrue(np.any(state["QC"] > 0.0))
        np.testing.assert_allclose(
            (state["QV"] + state["QC"]) / target_qsat,
            1.10,
            atol=2.0e-10,
        )

    def test_w_uses_terrain_condition_lower_blend_and_quiet_top(self) -> None:
        x = np.array([0.0, 200.0, 400.0])
        y = np.array([0.0, 200.0, 400.0])
        terrain = 0.1 * np.meshgrid(x, y)[0]
        hhl = np.stack([terrain + height for height in (0.0, 500.0, 2000.0, 4500.0, 7000.0)])
        u = np.full((4, 3, 3), 10.0)
        v = np.zeros_like(u)
        adjusted = adjust_vertical_velocity(
            target_hhl_m=hhl,
            interpolated_w_ms=np.full_like(hhl, 2.0),
            u_ms=u,
            v_ms=v,
            x_m=x,
            y_m=y,
        )
        np.testing.assert_allclose(adjusted[0], 1.0, atol=1.0e-12)
        self.assertTrue(np.all(adjusted[1] > 1.0))
        np.testing.assert_allclose(adjusted[-1], 0.0)

    def test_above_source_top_is_rejected(self) -> None:
        source_hhl, t, p, qv = self._source()
        with self.assertRaisesRegex(ValueError, "top"):
            reconstruct_column_state(
                source_hhl_m=source_hhl,
                target_hhl_m=np.array([500.0, 2000.0, 5000.0, 9000.0]),
                temperature_k=t,
                pressure_pa=p,
                qv=qv,
                u_ms=np.ones_like(t),
                v_ms=np.ones_like(t),
            )


class ProductPipelineTests(unittest.TestCase):
    def test_balance_certificate_is_bound_to_exact_state_and_hicar_operators(self) -> None:
        state = {"T": np.array([[[280.0]]]), "P": np.array([[[90_000.0]]])}
        certificate = BalanceCertificate(
            state_fingerprint=state_fingerprint(state),
            pressure_operator=PRESSURE_OPERATOR,
            wind_operator=WIND_OPERATOR,
            staggering=STAGGERING,
            maximum_discrete_hydrostatic_residual=1.0e-8,
            hydrostatic_residual_tolerance=1.0e-6,
            maximum_wind_matrix_relative_residual=1.0e-9,
            maximum_mass_continuity_residual=1.0e-9,
            valid_time="2020-01-01T00:00:00Z",
            producer_commit="deadbeef",
        )
        certificate.validate(state)
        changed = {**state, "T": state["T"] + 1.0}
        with self.assertRaisesRegex(ValueError, "does not belong"):
            certificate.validate(changed)

    def test_hicar_native_state_is_transposed_staggered_and_certified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "initialized.nc"
            diagnostics_path = root / "diagnostics.json"
            certificate_path = root / "certificate.json"
            levels, ny, nx = 3, 2, 2
            hfl = np.broadcast_to(
                np.array([100.0, 300.0, 600.0])[:, None, None], (levels, ny, nx)
            ).copy()
            hhl = np.broadcast_to(
                np.array([0.0, 200.0, 450.0, 750.0])[:, None, None],
                (levels + 1, ny, nx),
            ).copy()
            temperature = np.full((levels, ny, nx), 280.0)
            qv = np.full((levels, ny, nx), 0.005)
            qc = np.full((levels, ny, nx), 0.001)
            qi = np.full((levels, ny, nx), 0.002)
            epsilon = 287.05 / 461.5
            total_water = 0.005 + 0.001 + 0.002
            tv = 280.0 * (1.0 + 0.005 / epsilon) / (1.0 + total_water)
            pressure = 100_000.0 * np.exp(-9.80665 * hfl / (287.05 * tv))
            theta = temperature / (pressure / 100_000.0) ** 0.2857
            density = (
                pressure
                / (287.05 * temperature)
                * (1.0 + total_water)
                / (1.0 + qv / epsilon)
            )
            lat = np.array([[46.0, 46.0], [46.01, 46.01]])
            lon = np.array([[7.0, 7.01], [7.0, 7.01]])

            def write_3d(dataset, name, canonical, dimensions):
                variable = dataset.createVariable(name, "f8", dimensions)
                payload = np.transpose(canonical, (2, 0, 1))
                variable[:] = payload[..., None] if "time" in dimensions else payload

            with netCDF4.Dataset(state_path, "w") as dataset:
                for name, size in {
                    "lon_x": nx,
                    "lon_u": nx + 1,
                    "level": levels,
                    "level_i": levels + 1,
                    "lat_y": ny,
                    "lat_v": ny + 1,
                    "time": 1,
                }.items():
                    dataset.createDimension(name, size)
                for name, values in {
                    "temperature": temperature,
                    "pressure": pressure,
                    "qv": qv,
                    "potential_temperature": theta,
                    "density": density,
                    "w_grid": np.zeros_like(temperature),
                    "qc": qc,
                    "qi": qi,
                }.items():
                    write_3d(dataset, name, values, ("lon_x", "level", "lat_y", "time"))
                write_3d(
                    dataset,
                    "u",
                    np.zeros((levels, ny, nx + 1)),
                    ("lon_u", "level", "lat_y", "time"),
                )
                write_3d(
                    dataset,
                    "v",
                    np.zeros((levels, ny + 1, nx)),
                    ("lon_x", "level", "lat_v", "time"),
                )
                write_3d(dataset, "z", hfl, ("lon_x", "level", "lat_y"))
                write_3d(dataset, "z_i", hhl, ("lon_x", "level_i", "lat_y"))
                dataset.createVariable("lat", "f8", ("lon_x", "lat_y"))[:] = lat.T
                dataset.createVariable("lon", "f8", ("lon_x", "lat_y"))[:] = lon.T
                time = dataset.createVariable("time", "f8", ("time",))
                time.units = "seconds since 2020-01-01 00:00:00"
                time[:] = 0.432
                dataset.git = "qualification/test-0-gdeadbeef"
                dataset.git_tag = "deadbeef"

            diagnostics_path.write_text(
                json.dumps(
                    {
                        "schema": "hicar-initialization-diagnostics-v1",
                        "pressure_operator": PRESSURE_OPERATOR,
                        "wind_operator": WIND_OPERATOR,
                        "staggering": STAGGERING,
                        "wind_solver_status": 0,
                        "wind_solver_iterations": 4,
                        "wind_matrix_initial_residual": 1.0,
                        "wind_matrix_final_residual": 1.0e-7,
                        "wind_matrix_relative_residual": 1.0e-7,
                        "mass_continuity_initial_norm2": 1.0,
                        "mass_continuity_final_norm2": 4.0e-14,
                        "mass_continuity_relative_residual": 2.0e-7,
                        "passed": True,
                        "producer_commit": "deadbeef",
                    }
                )
            )
            state, metadata = load_hicar_initialized_state(state_path)
            self.assertEqual(state["U"].shape, (levels, ny, nx + 1))
            self.assertEqual(state["V"].shape, (levels, ny + 1, nx))
            self.assertEqual(state["W"].shape, (levels, ny, nx))
            self.assertEqual(metadata["valid_time"], "2020-01-01T00:00:00Z")
            certificate = issue_balance_certificate(
                state_path, diagnostics_path, maximum_hydrostatic_residual=1.0e-10
            )
            certificate.to_json(certificate_path)
            BalanceCertificate.from_json(certificate_path).validate(state)

            static_path = root / "static.nc"
            initial_path = root / "initial.nc"
            boundary_path = root / "boundary.nc"
            with netCDF4.Dataset(static_path, "w") as dataset:
                dataset.createDimension("x", nx)
                dataset.createDimension("y", ny)
                dataset.createVariable("x", "f8", ("x",))[:] = [0.0, 200.0]
                dataset.createVariable("y", "f8", ("y",))[:] = [0.0, 200.0]
                dataset.createVariable("lat", "f8", ("y", "x"))[:] = lat
                dataset.createVariable("lon", "f8", ("y", "x"))[:] = lon
            weights = RBFWeights(
                donor_index=np.zeros((ny * nx, 1), dtype=np.int64),
                weight=np.ones((ny * nx, 1)),
                target_shape=(ny, nx),
                source_fingerprint="source",
                target_fingerprint=grid_fingerprint(lat, lon),
            )
            weights_path = root / "weights.nc"
            weights.write(weights_path)
            write_initial_condition(
                initial_path,
                state,
                {"valid_time": metadata["valid_time"]},
                static_path=static_path,
                weights=weights,
                balance_certificate=certificate,
                water_representation="dry-air mixing ratio",
            )
            write_boundary_condition(
                boundary_path,
                state,
                x=np.array([0.0, 200.0]),
                y=np.array([0.0, 200.0]),
                boundary_width_m=1.0,
                initial_condition_path=initial_path,
                valid_time=metadata["valid_time"],
                include_lateral_w=True,
                balance_certificate=certificate,
                water_representation="dry-air mixing ratio",
            )
            rows, cols = boundary_point_indices(
                np.array([0.0, 200.0]), np.array([0.0, 200.0]), 1.0
            )
            with (
                netCDF4.Dataset(initial_path) as initial,
                netCDF4.Dataset(boundary_path) as boundary,
            ):
                self.assertEqual(initial["U"].dimensions, ("level", "y", "x_u"))
                self.assertEqual(initial["V"].dimensions, ("level", "y_v", "x"))
                self.assertEqual(initial["W"].dimensions, ("level", "y", "x"))
                for name in ("T", "P", "QV", "QC", "QI", "W", "THETA", "RHO", "HFL"):
                    np.testing.assert_array_equal(boundary[name][:], state[name][:, rows, cols])
                np.testing.assert_array_equal(boundary["HHL"][:], state["HHL"][:, rows, cols])
                u_rows = np.asarray(boundary["u_row"][:], dtype=np.int64)
                u_cols = np.asarray(boundary["u_column"][:], dtype=np.int64)
                v_rows = np.asarray(boundary["v_row"][:], dtype=np.int64)
                v_cols = np.asarray(boundary["v_column"][:], dtype=np.int64)
                self.assertEqual(boundary["U"].dimensions, ("level", "u_boundary_point"))
                self.assertEqual(boundary["V"].dimensions, ("level", "v_boundary_point"))
                np.testing.assert_array_equal(boundary["U"][:], state["U"][:, u_rows, u_cols])
                np.testing.assert_array_equal(boundary["V"][:], state["V"][:, v_rows, v_cols])

            published_initial = root / "published_initial.nc"
            published_boundary = root / "published_boundary.nc"
            publication_manifest = root / "publication.json"
            arguments = hicarprep_parser().parse_args(
                [
                    "publish-certified-initialization",
                    "--initialized-state",
                    str(state_path),
                    "--balance-certificate",
                    str(certificate_path),
                    "--static",
                    str(static_path),
                    "--weights",
                    str(weights_path),
                    "--initial",
                    str(published_initial),
                    "--boundary",
                    str(published_boundary),
                    "--manifest",
                    str(publication_manifest),
                    "--boundary-width-m",
                    "1",
                    "--lbc-w-policy",
                    "relax",
                ]
            )
            self.assertEqual(arguments.func(arguments), 0)
            self.assertTrue(publication_manifest.is_file())
            with netCDF4.Dataset(published_boundary) as published:
                self.assertEqual(published["U"].dimensions, ("level", "u_boundary_point"))
                self.assertEqual(published["V"].dimensions, ("level", "v_boundary_point"))
                self.assertIn("W", published.variables)

            changed_diagnostics = json.loads(diagnostics_path.read_text())
            changed_diagnostics["mass_continuity_relative_residual"] = 3.0e-5
            changed_diagnostics["mass_continuity_final_norm2"] = (3.0e-5) ** 2
            diagnostics_path.write_text(json.dumps(changed_diagnostics))
            with self.assertRaisesRegex(ValueError, "mass-continuity"):
                issue_balance_certificate(
                    state_path, diagnostics_path, maximum_hydrostatic_residual=1.0e-10
                )

    def test_boundary_sequence_requires_strict_bracketing_and_fixed_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def write(path: Path, valid_time: str) -> None:
                with netCDF4.Dataset(path, "w") as dataset:
                    dataset.createDimension("boundary_point", 2)
                    dataset.createDimension("u_boundary_point", 2)
                    dataset.createDimension("v_boundary_point", 2)
                    dataset.createDimension("level", 1)
                    dataset.createDimension("half_level", 2)
                    dataset.createVariable("row", "i4", ("boundary_point",))[:] = [0, 0]
                    dataset.createVariable("column", "i4", ("boundary_point",))[:] = [0, 1]
                    dataset.createVariable("u_row", "i4", ("u_boundary_point",))[:] = [0, 0]
                    dataset.createVariable("u_column", "i4", ("u_boundary_point",))[:] = [0, 2]
                    dataset.createVariable("v_row", "i4", ("v_boundary_point",))[:] = [0, 1]
                    dataset.createVariable("v_column", "i4", ("v_boundary_point",))[:] = [0, 0]
                    dataset.createVariable(
                        "relaxation_weight", "f8", ("boundary_point",)
                    )[:] = [1.0, 0.5]
                    dataset.createVariable(
                        "u_relaxation_weight", "f8", ("u_boundary_point",)
                    )[:] = [1.0, 1.0]
                    dataset.createVariable(
                        "v_relaxation_weight", "f8", ("v_boundary_point",)
                    )[:] = [1.0, 1.0]
                    for name in ("T", "P", "QV", "QC", "QI", "HFL"):
                        dataset.createVariable(name, "f8", ("level", "boundary_point"))[:] = 1.0
                    dataset.createVariable("HHL", "f8", ("half_level", "boundary_point"))[:] = 1.0
                    dataset.createVariable("U", "f8", ("level", "u_boundary_point"))[:] = 1.0
                    dataset.createVariable("V", "f8", ("level", "v_boundary_point"))[:] = 1.0
                    dataset.product_type = "hicar_lateral_boundary_state"
                    dataset.valid_time = valid_time
                    dataset.domain_nx = 2
                    dataset.domain_ny = 1
                    dataset.hicar_water_conversion = "APPLIED_JOINT_ALL_WATER_SPECIES"
                    dataset.hicar_pressure_adjustment = "APPLIED_HICAR_NATIVE"
                    dataset.wind_balance = "APPLIED_HICAR_ADJOINT_VARIATIONAL_PROJECTION"
                    dataset.lateral_w_policy = "diagnose_in_hicar"
                    dataset.target_grid_fingerprint = "target"
                    dataset.static_sha256 = "static"
                    dataset.relaxation_profile = "cosine_squared"
                    dataset.relaxation_update = "stable"
                    dataset.relaxation_timescale_seconds = 3600.0

            first = root / "first.nc"
            second = root / "second.nc"
            write(first, "2020-01-01T00:00:00Z")
            write(second, "2020-01-01T01:00:00Z")
            sequence = validate_boundary_sequence([first, second], maximum_interval_seconds=3600.0)
            self.assertEqual(sequence["state_count"], 2)
            self.assertEqual(sequence["maximum_interval_seconds"], 3600.0)
            with self.assertRaisesRegex(ValueError, "strictly increasing"):
                validate_boundary_sequence([second, first])

    def test_boundary_relaxation_weights_use_physical_cosine_shoulder(self) -> None:
        x = np.arange(8, dtype=np.float64) * 200.0
        y = np.arange(7, dtype=np.float64) * 200.0
        rows, cols, weights = boundary_relaxation_weights(x, y, 400.0)
        lookup = {(int(row), int(col)): float(weight) for row, col, weight in zip(rows, cols, weights)}
        self.assertEqual(lookup[(0, 3)], 1.0)
        self.assertAlmostEqual(lookup[(1, 3)], 0.5)
        self.assertEqual(lookup[(2, 2)], 0.0)
        self.assertNotIn((3, 3), lookup)

    def test_all_icon_water_species_are_jointly_converted_to_dry_air_basis(self) -> None:
        shape = (2, 1, 1)
        state = {
            "T": np.full(shape, 280.0),
            "P": np.full(shape, 90_000.0),
            "QV": np.full(shape, 0.010),
            "QC": np.full(shape, 0.001),
            "QI": np.full(shape, 0.002),
        }
        dry = 1.0 - state["QV"] - state["QC"] - state["QI"]
        state["RHO"] = state["P"] / (
            287.05 * state["T"] * (1.0 + 0.608 * state["QV"] - state["QC"] - state["QI"])
        )
        converted = convert_water_to_hicar_mixing_ratios(state)
        for name in ("QV", "QC", "QI"):
            np.testing.assert_allclose(converted[name], state[name] / dry)
        np.testing.assert_allclose(converted["RHO"], state["RHO"], rtol=1.0e-14)

    def test_valid_time_surface_merge_rejects_a_different_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            surface = Path(directory) / "surface.nc"
            with netCDF4.Dataset(surface, "w") as dataset:
                dataset.createDimension("y", 1)
                dataset.createDimension("x", 1)
                dataset.product_type = "initial_surface_state"
                dataset.valid_time = "2020-01-01T00:00:00Z"
                dataset.createVariable("skin_temperature", "f8", ("y", "x"))[:] = 280.0
            with self.assertRaisesRegex(ValueError, "valid_time"):
                load_valid_time_inputs(
                    valid_time="2020-01-01T01:00:00Z",
                    target_shape=(1, 1),
                    surface_path=surface,
                )

    def test_ic_lbc_use_identical_transformed_boundary_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            static_path = root / "static.nc"
            source_path = root / "icon.nc"
            weight_path = root / "weights.nc"
            initial_path = root / "input.nc"
            boundary_path = root / "boundary.nc"
            x = np.arange(3) * 200.0
            y = np.arange(3) * 200.0
            lat, lon = np.meshgrid(46.0 + y / 111_000.0, 8.0 + x / 75_000.0, indexing="ij")
            with netCDF4.Dataset(static_path, "w") as dataset:
                dataset.createDimension("x", 3)
                dataset.createDimension("y", 3)
                dataset.createVariable("x", "f8", ("x",))[:] = x
                dataset.createVariable("y", "f8", ("y",))[:] = y
                dataset.createVariable("lat", "f8", ("y", "x"))[:] = lat
                dataset.createVariable("lon", "f8", ("y", "x"))[:] = lon
                dataset.createVariable("topo", "f8", ("y", "x"))[:] = 500.0
            append_sleve_geometry(
                static_path,
                config=SleveConfig(
                    nz=3,
                    model_top_m=2500.0,
                    lowest_layer_m=200.0,
                    smooth_cycles=0,
                ),
            )

            source_lat, source_lon = np.meshgrid(
                np.linspace(45.98, 46.03, 4), np.linspace(7.98, 8.04, 4), indexing="ij"
            )
            cells = source_lat.size
            hhl_1d = np.array([300.0, 700.0, 1300.0, 2300.0, 3800.0, 6000.0])
            z = 0.5 * (hhl_1d[:-1] + hhl_1d[1:])
            with netCDF4.Dataset(source_path, "w") as dataset:
                dataset.createDimension("cell", cells)
                dataset.createDimension("level", 5)
                dataset.createDimension("half_level", 6)
                dataset.createVariable("clat", "f8", ("cell",))[:] = source_lat.ravel()
                dataset.createVariable("clon", "f8", ("cell",))[:] = source_lon.ravel()
                dataset["clat"].units = "degrees_north"
                dataset["clon"].units = "degrees_east"
                dataset.createVariable("HHL", "f8", ("half_level", "cell"))[:] = hhl_1d[::-1, None]
                dataset["HHL"].units = "m"
                dataset["HHL"].level_order = "top_to_bottom"
                profiles = {
                    "T": 285.0 - 0.006 * (z - 300.0),
                    "P": 97_000.0 * np.exp(-(z - 300.0) / 8200.0),
                    "QV": np.linspace(0.008, 0.001, 5),
                    "U": np.linspace(3.0, 8.0, 5),
                    "V": np.linspace(-2.0, 2.0, 5),
                }
                for name, profile in profiles.items():
                    dataset.createVariable(name, "f8", ("level", "cell"))[:] = profile[::-1, None]
                dataset["T"].units = "K"
                dataset["P"].units = "Pa"
                dataset["QV"].units = "kg kg-1"
                dataset["U"].units = "m s-1"
                dataset["V"].units = "m s-1"
                for name in ("QC", "QI"):
                    dataset.createVariable(name, "f8", ("level", "cell"))[:] = 0.0
                    dataset[name].units = "kg kg-1"
                dataset.createVariable("W", "f8", ("half_level", "cell"))[:] = 0.0
                dataset["W"].units = "m s-1"
                dataset.valid_time = "2020-01-01T00:00:00Z"
                dataset.horizontal_grid_uuid = "synthetic-icon-grid"

            weights = build_rbf_weights(source_lat, source_lon, lat, lon, donors=10)
            weights.write(weight_path)
            state, diagnostics = transform_icon_state(source_path, static_path, weights)
            self.assertEqual(diagnostics["source_vertical_order"], "top_to_bottom")
            with self.assertRaisesRegex(RuntimeError, "wind projection"):
                write_initial_condition(
                    initial_path,
                    state,
                    diagnostics,
                    static_path=static_path,
                    weights=weights,
                )
            write_initial_condition(
                initial_path,
                state,
                diagnostics,
                static_path=static_path,
                weights=weights,
                allow_unprojected_wind=True,
            )
            with self.assertRaisesRegex(RuntimeError, "boundary publication is blocked"):
                write_boundary_condition(
                    boundary_path,
                    state,
                    x=x,
                    y=y,
                    boundary_width_m=100.0,
                    initial_condition_path=initial_path,
                    valid_time=str(diagnostics["valid_time"]),
                )
            write_boundary_condition(
                boundary_path,
                state,
                x=x,
                y=y,
                boundary_width_m=100.0,
                initial_condition_path=initial_path,
                valid_time=str(diagnostics["valid_time"]),
                allow_unbalanced_state=True,
            )
            rows, cols = boundary_point_indices(x, y, 100.0)
            with (
                netCDF4.Dataset(initial_path) as initial,
                netCDF4.Dataset(boundary_path) as boundary,
            ):
                initial_t = np.asarray(initial["T"][:])
                np.testing.assert_allclose(boundary["T"][:], initial_t[:, rows, cols])
                self.assertEqual(boundary.dimensions["boundary_point"].size, 8)
                self.assertEqual(boundary.valid_time, "2020-01-01T00:00:00Z")
                self.assertEqual(initial.wind_balance, "NOT_APPLIED_RESEARCH_PRODUCT")
                self.assertEqual(initial.hicar_pressure_adjustment, "NOT_APPLIED_RESEARCH_PRODUCT")
                self.assertIn("specific humidity", initial.water_representation)


class SurfaceStateTests(unittest.TestCase):
    def test_same_surface_fallback_never_crosses_land_water_boundary(self) -> None:
        weights = RBFWeights(
            donor_index=np.array([[0, 1]]),
            weight=np.array([[0.75, 0.25]]),
            target_shape=(1, 1),
            source_fingerprint="source",
            target_fingerprint="target",
        )
        mapped, fallback_count, global_fallback_count = _supported_remap(
            weights,
            np.array([280.0, 282.0, 279.0]),
            np.array([True, True, False]),
            np.array([[False]]),
            source_lat=np.array([46.0, 46.01, 46.02]),
            source_lon=np.array([8.0, 8.01, 8.02]),
            target_lat=np.array([[46.001]]),
            target_lon=np.array([[8.001]]),
        )
        np.testing.assert_allclose(mapped, [[279.0]])
        self.assertEqual(fallback_count, 1)
        self.assertEqual(global_fallback_count, 1)

    def test_explicit_cross_surface_fallback_stays_inside_local_stencil(self) -> None:
        weights = RBFWeights(
            donor_index=np.array([[0, 1]]),
            weight=np.array([[0.75, 0.25]]),
            target_shape=(1, 1),
            source_fingerprint="source",
            target_fingerprint="target",
        )
        counts: list[int] = []
        distances: list[float] = []
        mapped, fallback_count, global_fallback_count = _supported_remap(
            weights,
            np.array([280.0, 282.0, 279.0]),
            np.array([True, True, False]),
            np.array([[False]]),
            source_lat=np.array([46.0, 46.01, 46.5]),
            source_lon=np.array([8.0, 8.01, 8.5]),
            target_lat=np.array([[46.001]]),
            target_lon=np.array([[8.001]]),
            allow_cross_surface_in_stencil=True,
            cross_surface_fallback_counts=counts,
            fallback_distances_km=distances,
        )
        np.testing.assert_allclose(mapped, [[280.0]])
        self.assertEqual(fallback_count, 1)
        self.assertEqual(global_fallback_count, 0)
        self.assertEqual(counts, [1])
        self.assertLess(max(distances), 1.0)

    def test_required_land_uses_nearest_global_finite_soil_fallback(self) -> None:
        weights = RBFWeights(
            donor_index=np.array([[0]]),
            weight=np.array([[1.0]]),
            target_shape=(1, 1),
            source_fingerprint="source",
            target_fingerprint="target",
        )
        mapped, fallback_count, global_fallback_count = _supported_remap(
            weights,
            np.array([np.nan, 0.6]),
            np.array([False, True]),
            np.array([[True]]),
            source_lat=np.array([46.0, 46.01]),
            source_lon=np.array([8.0, 8.01]),
            target_lat=np.array([[46.001]]),
            target_lon=np.array([[8.001]]),
            required_target=np.array([[True]]),
        )
        np.testing.assert_allclose(mapped, [[0.6]])
        self.assertEqual(fallback_count, 1)
        self.assertEqual(global_fallback_count, 1)

    def test_supported_rbf_is_limited_to_finite_stencil_extrema(self) -> None:
        weights = RBFWeights(
            donor_index=np.array([[0, 1]]),
            weight=np.array([[2.0, -1.0]]),
            target_shape=(1, 1),
            source_fingerprint="source",
            target_fingerprint="target",
        )
        mapped, fallback_count, global_fallback_count = _supported_remap(
            weights,
            np.array([280.0, 282.0]),
            np.array([True, True]),
            np.array([[True]]),
        )
        np.testing.assert_allclose(mapped, [[280.0]])
        self.assertEqual(fallback_count, 0)
        self.assertEqual(global_fallback_count, 0)

    def test_nonnegative_kernel_option_preserves_positive_paired_states(self) -> None:
        weights = RBFWeights(
            donor_index=np.array([[0, 1, 2]]),
            weight=np.array([[1.2, 0.2, -0.4]]),
            target_shape=(1, 1),
            source_fingerprint="source",
            target_fingerprint="target",
        )
        mapped, _, _ = _supported_remap(
            weights,
            np.array([1.0, 3.0, 2.0]),
            np.array([True, True, True]),
            np.array([[True]]),
            nonnegative_weights=True,
        )
        np.testing.assert_allclose(mapped, [[9.0 / 7.0]])

    def test_int2lm_smi_has_zero_lower_bound_but_allows_above_field_capacity(self) -> None:
        thickness = np.diff(ICON_W_SO_BOUNDS_M)
        theta = np.stack(
            [
                np.full(thickness.size, ICON_TERRA_WILTING_POINT[4] - 0.02),
                np.full(thickness.size, ICON_TERRA_FIELD_CAPACITY[4] + 0.05),
            ],
            axis=1,
        )
        mass = theta * (1000.0 * thickness[:, None])
        smi = icon_soil_water_to_smi(mass, np.array([5, 5]))
        np.testing.assert_allclose(smi[:, 0], 0.0)
        self.assertTrue(np.all(smi[:, 1] > 1.0))

    def test_relative_saturation_transfers_fraction_of_target_pore_volume(self) -> None:
        thickness = np.diff(ICON_W_SO_BOUNDS_M)
        source_fraction = 0.6
        source_theta = source_fraction * ICON_TERRA_POROSITY[4]
        mass = np.full((thickness.size, 1), source_theta) * (1000.0 * thickness[:, None])
        relative = icon_soil_water_to_relative_saturation(mass, np.array([5]))
        np.testing.assert_allclose(relative, source_fraction)
        table = Path(__file__).resolve().parents[1] / "HICAR" / "run" / "NoahmpTable.TBL"
        hydraulics = parse_noahmp_stas_hydraulics(table)
        target = noahmp_relative_saturation_to_vwc(
            np.full((4, 1, 1), source_fraction), np.array([[6]]), hydraulics
        )
        np.testing.assert_allclose(target, source_fraction * hydraulics["MAXSMC"][5])

    def test_smi_is_selectable_and_reconstructed_with_target_noahmp_class(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            static = root / "static.nc"
            source = root / "surface_source.nc"
            absolute = root / "absolute.nc"
            smi = root / "smi.nc"
            relative = root / "relative.nc"
            smi_external = root / "smi_external.nc"
            runtime = root / "hicar_domain.nc"
            runtime_external = root / "hicar_domain_external.nc"
            external = root / "external.nc"
            y = np.array([0.0, 200.0])
            x = np.array([0.0, 200.0])
            target_lat, target_lon = np.meshgrid(
                46.0 + y / 111_000.0, 8.0 + x / 75_000.0, indexing="ij"
            )
            with netCDF4.Dataset(static, "w") as dataset:
                dataset.createDimension("y", 2)
                dataset.createDimension("x", 2)
                dataset.createDimension("soil_layer", 4)
                dataset.createVariable("lat", "f8", ("y", "x"))[:] = target_lat
                dataset.createVariable("lon", "f8", ("y", "x"))[:] = target_lon
                dataset.createVariable("topo", "f8", ("y", "x"))[:] = 800.0
                dataset.createVariable("landmask", "f8", ("y", "x"))[:] = np.array(
                    [[1.0, 1.0], [1.0, 0.0]]
                )
                dataset.createVariable("landuse", "i2", ("y", "x"))[:] = np.array(
                    [[7, 7], [7, 16]]
                )
                dataset.createVariable("soil_type", "i2", ("y", "x"))[:] = 6
                dataset.createVariable(
                    "soil_type_layer", "i2", ("soil_layer", "y", "x")
                )[:] = np.array([6, 7, 8, 9])[:, None, None]

            source_lat, source_lon = np.meshgrid(
                np.linspace(45.98, 46.02, 4), np.linspace(7.98, 8.03, 4), indexing="ij"
            )
            cell_count = source_lat.size
            terra_soil_type = 5
            terra_smi = 0.5
            source_vwc = ICON_TERRA_WILTING_POINT[terra_soil_type - 1] + terra_smi * (
                ICON_TERRA_FIELD_CAPACITY[terra_soil_type - 1]
                - ICON_TERRA_WILTING_POINT[terra_soil_type - 1]
            )
            w_so = source_vwc * 1000.0 * np.diff(ICON_W_SO_BOUNDS_M)[:, None]
            with netCDF4.Dataset(source, "w") as dataset:
                dataset.createDimension("cell", cell_count)
                dataset.createDimension("t_so_level", ICON_T_SO_DEPTHS_M.size)
                dataset.createDimension("w_so_layer", ICON_W_SO_BOUNDS_M.size - 1)
                dataset.createVariable("clat", "f8", ("cell",))[:] = source_lat.ravel()
                dataset.createVariable("clon", "f8", ("cell",))[:] = source_lon.ravel()
                dataset["clat"].units = "degrees_north"
                dataset["clon"].units = "degrees_east"
                dataset.createVariable("T_SO", "f8", ("t_so_level", "cell"))[:] = 280.0
                dataset.createVariable("W_SO", "f8", ("w_so_layer", "cell"))[:] = np.broadcast_to(
                    w_so, (w_so.shape[0], cell_count)
                )
                for name, value in (
                    ("SKT", 281.0),
                    ("W_SNOW", 10.0),
                    ("RHO_SNOW", 200.0),
                    ("SOILTYP", terra_soil_type),
                    ("FR_LAND", 1.0),
                    ("HSURF", 800.0),
                ):
                    dataset.createVariable(name, "f8", ("cell",))[:] = value
                dataset.valid_time = "2020-01-01T00:00:00Z"

            weights = build_rbf_weights(source_lat, source_lon, target_lat, target_lon, donors=10)
            table = Path(__file__).resolve().parents[1] / "HICAR" / "run" / "NoahmpTable.TBL"
            prepare_surface_state(
                source,
                static,
                absolute,
                weights=weights,
                noahmp_table=table,
                soil_water_method="absolute_w_so",
            )
            prepare_surface_state(
                source,
                static,
                smi,
                weights=weights,
                noahmp_table=table,
                soil_water_method="smi",
            )
            prepare_surface_state(
                source,
                static,
                relative,
                weights=weights,
                noahmp_table=table,
                soil_water_method="relative_saturation",
            )
            validate_product_lifetimes(absolute)
            validate_product_lifetimes(smi)
            validate_product_lifetimes(relative)
            report = root / "surface_validation.json"
            validation = validate_surface_case(
                source,
                static,
                {
                    "absolute_w_so": absolute,
                    "smi": smi,
                    "relative_saturation": relative,
                },
                noahmp_table=table,
                report_path=report,
            )
            self.assertEqual(validation["status"], "PASS_INPUT_PLAUSIBILITY")
            self.assertEqual(validation["policy_decision"], "NOT_DETERMINED_BY_PLAUSIBILITY_TESTS")
            self.assertTrue(report.is_file())
            self.assertIn("relative_saturation_minus_smi", validation["pairwise_vwc"])
            hydraulics = parse_noahmp_stas_hydraulics(table)
            target_indices = np.array([5, 6, 7, 8])
            expected_smi_vwc = hydraulics["WLTSMC"][target_indices] + terra_smi * (
                hydraulics["REFSMC"][target_indices]
                - hydraulics["WLTSMC"][target_indices]
            )
            with (
                netCDF4.Dataset(absolute) as absolute_data,
                netCDF4.Dataset(smi) as smi_data,
                netCDF4.Dataset(relative) as relative_data,
            ):
                self.assertEqual(absolute_data.soil_water_method, "absolute_w_so")
                self.assertEqual(smi_data.soil_water_method, "smi")
                self.assertEqual(relative_data.soil_water_method, "relative_saturation")
                self.assertEqual(smi_data.soil_water_default, "smi")
                expected_absolute = np.full((4, 2, 2), source_vwc)
                expected_absolute[:, 1, 1] = 0.0
                np.testing.assert_allclose(absolute_data["soil_vwc"][:], expected_absolute)
                expected_smi_grid = np.broadcast_to(
                    expected_smi_vwc[:, None, None], (4, 2, 2)
                ).copy()
                expected_smi_grid[:, 1, 1] = 0.0
                np.testing.assert_allclose(
                    smi_data["soil_vwc"][:],
                    expected_smi_grid,
                )
                expected_relative = (
                    source_vwc / ICON_TERRA_POROSITY[terra_soil_type - 1]
                ) * hydraulics["MAXSMC"][target_indices]
                expected_relative_grid = np.broadcast_to(
                    expected_relative[:, None, None], (4, 2, 2)
                ).copy()
                expected_relative_grid[:, 1, 1] = 0.0
                np.testing.assert_allclose(
                    relative_data["soil_vwc"][:],
                    expected_relative_grid,
                )
                expected_snow_depth = np.full((2, 2), 0.05)
                expected_snow_depth[1, 1] = 0.0
                np.testing.assert_allclose(smi_data["snow_depth"][:], expected_snow_depth)
                np.testing.assert_allclose(smi_data["soil_temperature"][:, 1, 1], 281.0)
                self.assertEqual(
                    smi_data.nonland_soil_temperature_policy,
                    "inactive HICAR soil columns filled with valid-time remapped skin temperature",
                )
                self.assertEqual(smi_data["soil_vwc"].shape[0], HICAR_SOIL_BOUNDS_M.size - 1)
                self.assertEqual(smi_data.target_soil_type_source, "soil_type_layer")

            with mock.patch(
                "preprocessing.hicarprep.products.shutil.copy2",
                side_effect=OSError("simulated GPFS sendfile failure"),
            ):
                assemble_hicar_runtime_domain(static, smi, runtime)
            validate_hicar_runtime_domain(runtime)
            self.assertTrue(Path(f"{runtime}.ready").is_file())
            with netCDF4.Dataset(runtime) as dataset:
                self.assertEqual(dataset.product_type, "hicar_runtime_domain_initial_conditions")
                self.assertEqual(dataset.land_state_soil_water_method, "smi")
                np.testing.assert_allclose(
                    dataset["surface_temperature"][:], 281.0
                )
                np.testing.assert_allclose(
                    dataset["soil_vwc"][:],
                    expected_smi_grid,
                )

            with netCDF4.Dataset(external, "w") as dataset:
                dataset.createDimension("epoch", None)
                dataset.createDimension("month", 12)
                dataset.createDimension("y", 2)
                dataset.createDimension("x", 2)
                epoch = dataset.createVariable("epoch_time", "f8", ("epoch",))
                epoch[:] = [dt.datetime(2019, 1, 1, tzinfo=dt.timezone.utc).timestamp()]
                epoch.units = "seconds since 1970-01-01 00:00:00 UTC"
                landuse = dataset.createVariable("landuse", "i2", ("epoch", "y", "x"))
                landuse[:] = 15
                landuse.hicar_lifetime = "epoch"
                vegfrac = dataset.createVariable("VEGFRA", "f4", ("month", "y", "x"))
                vegfrac[:] = np.arange(12)[:, None, None]
                vegfrac.hicar_lifetime = "climatology"
            prepare_surface_state(
                source,
                static,
                smi_external,
                weights=weights,
                noahmp_table=table,
                soil_water_method="smi",
                external_path=external,
            )
            with self.assertRaisesRegex(
                ValueError, "not prepared with the supplied external parameters"
            ):
                assemble_hicar_runtime_domain(
                    static, smi, root / "runtime_external_mismatch.nc", external_path=external
                )
            assemble_hicar_runtime_domain(
                static, smi_external, runtime_external, external_path=external
            )
            with netCDF4.Dataset(runtime_external) as dataset:
                np.testing.assert_array_equal(dataset["landuse"][:], 15)
                self.assertEqual(dataset["VEGFRA"].shape, (12, 2, 2))
                self.assertEqual(
                    dataset["VEGFRA"].materialization_policy,
                    "preserved_12_month_climatology_for_hicar_monthly_vegfrac",
                )
                self.assertEqual(
                    dataset.external_parameters_valid_time,
                    "2020-01-01T00:00:00+00:00",
                )

            # Raw public static files carry a dated land-cover epoch.  The
            # operational lifetime product rejects requests before that epoch;
            # direct research use of the raw file must be equally explicit.
            with netCDF4.Dataset(static, "r+") as dataset:
                dataset["landuse"].hicar_lifetime = "epoch"
                dataset["landuse"].epoch_valid_from = "2021-01-01T00:00:00Z"
            epoch_override = root / "smi_epoch_override.nc"
            with self.assertRaisesRegex(ValueError, "static landuse epoch begins"):
                prepare_surface_state(
                    source,
                    static,
                    epoch_override,
                    weights=weights,
                    noahmp_table=table,
                    soil_water_method="smi",
                )
            prepare_surface_state(
                source,
                static,
                epoch_override,
                weights=weights,
                noahmp_table=table,
                soil_water_method="smi",
                allow_static_epoch_back_extrapolation=True,
            )
            with netCDF4.Dataset(epoch_override) as dataset:
                self.assertEqual(
                    dataset.static_epoch_back_extrapolation,
                    "explicit_research_override",
                )
                self.assertEqual(
                    dataset.static_landuse_epoch_valid_from,
                    "2021-01-01T00:00:00Z",
                )


if __name__ == "__main__":
    unittest.main()
