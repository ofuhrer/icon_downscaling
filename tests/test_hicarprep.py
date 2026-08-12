from __future__ import annotations

import datetime as dt
import json
import multiprocessing as mp
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import netCDF4
import numpy as np
from preprocessing.hicarprep.boundary import validate_boundary_sequence
from preprocessing.hicarprep.cli import parser as hicarprep_parser
from preprocessing.hicarprep.geometry import SleveConfig, build_sleve_geometry
from preprocessing.hicarprep.external import append_epoch, evaluate_external_fields
from preprocessing.hicarprep.pipeline import (
    _face_grid_wind,
    _remap_vertical_interfaces,
    _source_wind,
    boundary_relaxation_weights,
    boundary_point_indices,
    convert_water_to_hicar_mixing_ratios,
    load_valid_time_inputs,
    transform_icon_state,
    write_boundary_condition,
    write_hicar_forcing_record,
    write_initial_condition,
)
from preprocessing.hicarprep.products import (
    assemble_hicar_runtime_domain,
    append_sleve_geometry,
    partition_domain_inputs,
    validate_hicar_runtime_domain,
    validate_product_lifetimes,
    validate_product_set,
    sha256,
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
from preprocessing.hicarprep.rotation import (
    earth_to_grid_wind,
    grid_to_earth_wind,
    hicar_grid_rotation,
)
from preprocessing.hicarprep.sst import SST_POLICY_VERSION, SST_REMAP_POLICY
from preprocessing.hicarprep.vertical import (
    adjust_vertical_velocity,
    interpolate_interface_w_to_hfl,
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
                self.assertEqual(dataset.sleve_lowest_layer_m, 40.0)
                self.assertEqual(dataset.required_minimum_sleve_layer_thickness_m, 12.0)
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

    def test_default_sleve_configuration_uses_twenty_metre_base_and_twelve_metre_floor(
        self,
    ) -> None:
        terrain = np.array([[400.0, 600.0], [900.0, 1200.0]])
        geometry = build_sleve_geometry(terrain)
        self.assertEqual(float(geometry["reference_layer_thickness"][0]), 20.0)
        self.assertGreaterEqual(float(np.min(geometry["LAYER_THICKNESS"])), 12.0)

        steep_terrain = np.zeros((21, 21), dtype=np.float64)
        steep_terrain[10, 10] = 2375.0
        with self.assertRaisesRegex(ValueError, "below 12 m"):
            build_sleve_geometry(steep_terrain)

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
            epoch_path = Path(directory) / "epoch.nc"
            with netCDF4.Dataset(epoch_path, "w") as dataset:
                dataset.createDimension("epoch", None)
                dataset.createDimension("y", 1)
                dataset.createDimension("x", 1)
                dataset.product_type = "time_varying_external_parameters"
                dataset.createVariable("epoch_time", "f8", ("epoch",))[:] = [86_400.0]
                landuse = dataset.createVariable("landuse", "i2", ("epoch", "y", "x"))
                landuse[:] = 15
                landuse.hicar_lifetime = "epoch"
            before_epoch = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
            with self.assertRaisesRegex(ValueError, "no epoch is valid"):
                evaluate_external_fields(epoch_path, before_epoch)
            extrapolated = evaluate_external_fields(
                epoch_path,
                before_epoch,
                allow_epoch_back_extrapolation=True,
            )
            self.assertEqual(int(extrapolated["landuse"][0, 0]), 15)

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
        self.assertLessEqual(float(np.max(np.sum(np.abs(operator.weight), axis=1))), 10.0)
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

    def test_scalar_rbf_wind_remap_cannot_create_new_component_extrema(self) -> None:
        operator = RBFWeights(
            donor_index=np.array([[0, 1]]),
            weight=np.array([[2.0, -1.0]]),
            target_shape=(1, 1),
            source_fingerprint="source",
            target_fingerprint="target",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.nc"
            with netCDF4.Dataset(path, "w") as dataset:
                dataset.createDimension("level", 1)
                dataset.createDimension("cell", 2)
                for name, values in (
                    ("U", [[20.0, -10.0]]),
                    ("V", [[-15.0, 5.0]]),
                ):
                    variable = dataset.createVariable(name, "f8", ("level", "cell"))
                    variable[:] = values
                    variable.units = "m s-1"
            with netCDF4.Dataset(path) as dataset:
                u, v = _source_wind(
                    dataset,
                    operator,
                    np.array([[46.0]]),
                    np.array([[8.0]]),
                    None,
                )
        np.testing.assert_allclose(u, 20.0)
        np.testing.assert_allclose(v, -15.0)

    def test_cached_rbf_operator_rejects_unbounded_amplification(self) -> None:
        with self.assertRaisesRegex(ValueError, "bounded interpolation amplification"):
            RBFWeights(
                donor_index=np.array([[0, 1]]),
                weight=np.array([[20.0, -19.0]]),
                target_shape=(1, 1),
                source_fingerprint="source",
                target_fingerprint="target",
            )

    def test_vertical_interface_remap_reconstructs_positive_layers_after_rbf_overshoot(
        self,
    ) -> None:
        operator = RBFWeights(
            donor_index=np.array([[0, 1]]),
            weight=np.array([[2.0, -1.0]]),
            target_shape=(1, 1),
            source_fingerprint="source",
            target_fingerprint="target",
        )
        native_hhl = np.array(
            [
                [0.0, 10.0],
                [10.0, 90.0],
                [100.0, 110.0],
            ]
        )
        direct = operator.apply(native_hhl)
        self.assertTrue(np.any(np.diff(direct, axis=0) <= 0.0))

        remapped, diagnostics = _remap_vertical_interfaces(native_hhl, operator)

        self.assertGreaterEqual(float(np.min(np.diff(remapped, axis=0))), 20.0 - 1.0e-10)
        np.testing.assert_allclose(remapped[0], operator.apply(native_hhl[0], monotone=True))
        np.testing.assert_allclose(remapped[-1], operator.apply(native_hhl[-1], monotone=True))
        self.assertEqual(
            diagnostics["source_geometry_remap"],
            "minimum_thickness_rescaled_to_rbf_endpoints",
        )
        self.assertEqual(diagnostics["source_geometry_minimum_layer_thickness_m"], 20.0)

    def test_vertical_interface_remap_rejects_an_impossible_minimum_thickness(self) -> None:
        operator = RBFWeights(
            donor_index=np.array([[0]]),
            weight=np.array([[1.0]]),
            target_shape=(1, 1),
            source_fingerprint="source",
            target_fingerprint="target",
        )
        with self.assertRaisesRegex(ValueError, "too shallow"):
            _remap_vertical_interfaces(
                np.array([[0.0], [10.0], [30.0]]),
                operator,
                minimum_layer_thickness_m=20.0,
            )

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
            grid_sintheta=np.zeros((3, 3)),
            grid_costheta=np.ones((3, 3)),
            x_m=x,
            y_m=y,
        )
        np.testing.assert_allclose(adjusted[0], 1.0, atol=1.0e-12)
        self.assertTrue(np.all(adjusted[1] > 1.0))
        np.testing.assert_allclose(adjusted[-1], 0.0)

    def test_w_slope_uses_grid_relative_not_earth_relative_wind(self) -> None:
        x = np.array([0.0, 200.0, 400.0])
        y = np.array([0.0, 200.0, 400.0])
        terrain = 0.1 * np.meshgrid(x, y)[0]
        hhl = np.stack([terrain, terrain + 500.0, terrain + 5_000.0])
        angle = np.deg2rad(30.0)
        sine = np.full((3, 3), -np.sin(angle))
        cosine = np.full((3, 3), np.cos(angle))
        # This earth-relative vector is exactly 10 m/s along target-grid x.
        u_east, v_north = grid_to_earth_wind(
            np.full((2, 3, 3), 10.0), np.zeros((2, 3, 3)), sine, cosine
        )
        u_before = u_east.copy()
        v_before = v_north.copy()
        adjusted = adjust_vertical_velocity(
            target_hhl_m=hhl,
            interpolated_w_ms=np.zeros_like(hhl),
            u_ms=u_east,
            v_ms=v_north,
            grid_sintheta=sine,
            grid_costheta=cosine,
            x_m=x,
            y_m=y,
        )
        np.testing.assert_allclose(adjusted[0], 1.0, atol=1.0e-12)
        np.testing.assert_array_equal(u_east, u_before)
        np.testing.assert_array_equal(v_north, v_before)

    def test_interface_w_uses_authoritative_hfl_not_arithmetic_midpoints(self) -> None:
        hhl = np.array([0.0, 100.0, 300.0])[:, None, None]
        hfl = np.array([25.0, 150.0])[:, None, None]
        interface_w = np.array([0.0, 10.0, 30.0])[:, None, None]
        mass_w = interpolate_interface_w_to_hfl(
            target_hhl_m=hhl,
            target_hfl_m=hfl,
            interface_w_ms=interface_w,
        )
        np.testing.assert_allclose(mass_w[:, 0, 0], [2.5, 15.0])

    def test_interface_w_vectorization_matches_columnwise_linear_interpolation(self) -> None:
        hhl = np.array(
            [
                [[0.0, 10.0], [20.0, 30.0]],
                [[100.0, 130.0], [160.0, 190.0]],
                [[310.0, 350.0], [390.0, 430.0]],
            ]
        )
        fractions = np.array([[[0.2, 0.4], [0.6, 0.8]], [[0.75, 0.5], [0.25, 0.9]]])
        hfl = hhl[:-1] + fractions * (hhl[1:] - hhl[:-1])
        interface_w = np.arange(12, dtype=np.float64).reshape(3, 2, 2) / 3.0
        actual = interpolate_interface_w_to_hfl(
            target_hhl_m=hhl,
            target_hfl_m=hfl,
            interface_w_ms=interface_w,
        )
        expected = np.empty_like(hfl)
        for row in range(2):
            for column in range(2):
                expected[:, row, column] = np.interp(
                    hfl[:, row, column],
                    hhl[:, row, column],
                    interface_w[:, row, column],
                )
        np.testing.assert_allclose(actual, expected, atol=1.0e-15)

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


class GridRotationTests(unittest.TestCase):
    def test_zero_angle_leaves_winds_unchanged(self) -> None:
        latitude = np.broadcast_to(np.array([46.0, 46.0, 46.0]), (3, 3))
        longitude = np.broadcast_to(np.array([7.0, 7.1, 7.2]), (3, 3))
        sine, cosine = hicar_grid_rotation(latitude, longitude, dx_m=1_000.0)
        np.testing.assert_allclose(sine, 0.0, atol=0.0)
        np.testing.assert_allclose(cosine, 1.0, atol=1.0e-15)
        u = np.arange(18, dtype=np.float64).reshape(2, 3, 3)
        v = -u
        actual_u, actual_v = earth_to_grid_wind(u, v, sine, cosine)
        np.testing.assert_allclose(actual_u, u)
        np.testing.assert_allclose(actual_v, v)

    def test_sign_and_inverse_match_hicar_convention(self) -> None:
        longitude = np.broadcast_to(np.array([7.0, 7.1, 7.2]), (3, 3))
        latitude = 46.0 + 0.02 * np.broadcast_to(np.arange(3), (3, 3))
        sine, cosine = hicar_grid_rotation(
            latitude, longitude, dx_m=2_000.0, smoothing_distance_m=0.0
        )
        self.assertTrue(np.all(sine < 0.0))
        self.assertTrue(np.all(cosine > 0.0))
        u_grid, v_grid = earth_to_grid_wind(np.zeros((3, 3)), np.full((3, 3), 10.0), sine, cosine)
        self.assertTrue(np.all(u_grid > 0.0))
        u_east, v_north = grid_to_earth_wind(u_grid, v_grid, sine, cosine)
        np.testing.assert_allclose(u_east, 0.0, atol=1.0e-14)
        np.testing.assert_allclose(v_north, 10.0, atol=1.0e-14)

    def test_smoothing_matches_hicar_truncated_windows(self) -> None:
        y, x = np.meshgrid(np.arange(6), np.arange(7), indexing="ij")
        latitude = 46.0 + 0.003 * x + 0.0004 * x * y
        longitude = 7.0 + 0.004 * x
        actual_sine, actual_cosine = hicar_grid_rotation(
            latitude,
            longitude,
            dx_m=500.0,
            smoothing_distance_m=1_000.0,
            smoothing_half_width_cells=1,
        )

        left = np.maximum(np.arange(7) - 2, 0)
        right = np.minimum(np.arange(7) + 2, 6)
        dlat = latitude[:, right] - latitude[:, left]
        dlon = (longitude[:, right] - longitude[:, left]) * np.cos(np.deg2rad(latitude))
        distance = np.hypot(dlat, dlon)
        expected_sine = -dlat / distance
        expected_cosine = np.abs(dlon / distance)
        for _ in range(2):
            for field in (expected_sine, expected_cosine):
                source = field.copy()
                for row in range(6):
                    for column in range(7):
                        field[row, column] = np.mean(
                            source[
                                max(row - 1, 0) : min(row + 2, 6),
                                max(column - 1, 0) : min(column + 2, 7),
                            ]
                        )
        np.testing.assert_allclose(actual_sine, expected_sine, atol=1.0e-15)
        np.testing.assert_allclose(actual_cosine, expected_cosine, atol=1.0e-15)


class ProductPipelineTests(unittest.TestCase):
    def test_mass_winds_are_placed_on_distinct_hicar_face_grids(self) -> None:
        u_mass = np.array([[[1.0, 3.0, 7.0], [2.0, 4.0, 8.0]]])
        v_mass = np.array([[[10.0, 20.0, 30.0], [14.0, 24.0, 34.0]]])
        u_face = _face_grid_wind(u_mass, component="U", target_shape=(2, 3))
        v_face = _face_grid_wind(v_mass, component="V", target_shape=(2, 3))
        self.assertEqual(u_face.shape, (1, 2, 4))
        self.assertEqual(v_face.shape, (1, 3, 3))
        np.testing.assert_allclose(u_face[0, 0], [1.0, 2.0, 5.0, 7.0])
        np.testing.assert_allclose(v_face[0, :, 1], [20.0, 22.0, 24.0])

    def test_target_forcing_record_uses_hicarprep_state_and_mass_grid_winds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            static = root / "static.nc"
            source = root / "icon.nc"
            target_sst = root / "target_sst.nc"
            output = root / "forcing.nc"
            levels, ny, nx = 2, 2, 3
            lat = np.broadcast_to(np.linspace(46.0, 46.1, ny)[:, None], (ny, nx)).copy()
            lon = np.broadcast_to(np.linspace(7.0, 7.2, nx)[None, :], (ny, nx)).copy()
            terrain = np.arange(ny * nx, dtype=np.float64).reshape(ny, nx) * 10.0
            with netCDF4.Dataset(static, "w") as dataset:
                dataset.createDimension("y", ny)
                dataset.createDimension("x", nx)
                dataset.createDimension("level", levels)
                dataset.createDimension("half_level", levels + 1)
                dataset.createVariable("lat", "f8", ("y", "x"))[:] = lat
                dataset.createVariable("lon", "f8", ("y", "x"))[:] = lon
                dataset.createVariable("topo", "f8", ("y", "x"))[:] = terrain
                landmask = np.ones((ny, nx), dtype=np.int16)
                landmask[0, 1] = 0
                dataset.createVariable("landmask", "i2", ("y", "x"))[:] = landmask
                hhl = np.stack((terrain, terrain + 100.0, terrain + 300.0))
                hfl = np.stack((terrain + 47.0, terrain + 188.0))
                dataset.createVariable("HHL", "f8", ("half_level", "y", "x"))[:] = hhl
                dataset.createVariable("HFL", "f8", ("level", "y", "x"))[:] = hfl
            source.write_bytes(b"native-icon-provenance")
            with netCDF4.Dataset(target_sst, "w") as dataset:
                dataset.createDimension("y", ny)
                dataset.createDimension("x", nx)
                dataset.createVariable("lat", "f8", ("y", "x"))[:] = lat
                dataset.createVariable("lon", "f8", ("y", "x"))[:] = lon
                variable = dataset.createVariable("SST", "f4", ("y", "x"))
                variable[:] = 277.0
                variable.units = "K"
                dataset.createVariable("water_mask", "i1", ("y", "x"))[:] = landmask < 0.5
                dataset.createVariable("unsupported_water_mask", "i1", ("y", "x"))[:] = 0
                dataset.createVariable(
                    "nearest_same_surface_candidate_distance_km", "f8", ("y", "x")
                )[:] = np.nan
                dataset.product_type = "hicarprep_target_water_temperature"
                dataset.valid_time = "2020-02-10T01:00:00Z"
                dataset.static_sha256 = sha256(static)
                dataset.target_grid_fingerprint = grid_fingerprint(lat, lon)
                dataset.source_sha256 = "synthetic-native-sst"
                dataset.source_variable = "SKT"
                dataset.sst_policy_version = SST_POLICY_VERSION
                dataset.remap_policy = SST_REMAP_POLICY
                dataset.water_cell_count = 1
                dataset.water_compact_fallback_count = 0
                dataset.water_unsupported_count = 0
                dataset.maximum_nearest_same_surface_candidate_distance_km = 0.0
            state = {
                "T": np.full((levels, ny, nx), 280.0),
                "P": np.full((levels, ny, nx), 90_000.0),
                "QV": np.full((levels, ny, nx), 0.005),
                "QC": np.zeros((levels, ny, nx)),
                "QI": np.zeros((levels, ny, nx)),
                "U": np.broadcast_to(
                    np.arange(nx + 1, dtype=np.float64)[None, None, :],
                    (levels, ny, nx + 1),
                ).copy(),
                "V": np.broadcast_to(
                    np.arange(ny + 1, dtype=np.float64)[None, :, None],
                    (levels, ny + 1, nx),
                ).copy(),
                "W": np.full((levels, ny, nx), 0.25),
                "HHL": hhl,
                "HFL": hfl,
                "lat": lat,
                "lon": lon,
            }
            write_hicar_forcing_record(
                output,
                state,
                {
                    "valid_time": "2020-02-10T01:00:00Z",
                    "target_w_vertical_coordinate": "authoritative_static_HFL",
                    "target_w_terrain_wind_basis": "HICAR_grid_relative",
                },
                static_path=static,
                source_path=source,
                target_sst_path=target_sst,
            )
            with netCDF4.Dataset(output) as dataset:
                self.assertEqual(dataset.product_type, "hicarprep_target_forcing_record")
                self.assertEqual(dataset.water_representation, "dry-air mixing ratio")
                self.assertEqual(dataset["P"].dimensions, ("time", "z", "y_1", "x_1"))
                self.assertEqual(dataset["HHL"].dimensions, ("z_hl", "y_1", "x_1"))
                expected_hfl = hfl.astype(np.float32)
                expected_hfl[-1] = np.nextafter(expected_hfl[-1], np.float32(np.inf))
                np.testing.assert_array_equal(dataset["HFL"][:], expected_hfl)
                self.assertEqual(
                    dataset.geometry_serialization,
                    "static_sleve_with_one_ulp_top_cover",
                )
                np.testing.assert_allclose(dataset["U"][0, 0, 0], [0.5, 1.5, 2.5])
                np.testing.assert_allclose(dataset["V"][0, 0, :, 0], [0.5, 1.5])
                self.assertEqual(dataset["W"].dimensions, ("time", "z", "y_1", "x_1"))
                np.testing.assert_allclose(dataset["W"][:], 0.25)
                self.assertEqual(
                    dataset.target_w_vertical_coordinate,
                    "authoritative_static_HFL",
                )
                self.assertEqual(
                    dataset.target_w_terrain_wind_basis,
                    "HICAR_grid_relative",
                )
                self.assertEqual(dataset["SST"].dimensions, ("time", "y_1", "x_1"))
                np.testing.assert_allclose(dataset["SST"][:], 277.0)
                self.assertEqual(dataset.sst_policy_version, SST_POLICY_VERSION)
                self.assertEqual(dataset.sst_remap_policy, SST_REMAP_POLICY)
                self.assertEqual(dataset.sst_water_cell_count, 1)
                self.assertEqual(dataset.sst_water_compact_fallback_count, 0)
                self.assertEqual(dataset.sst_water_unsupported_count, 0)
                self.assertEqual(
                    dataset.sst_maximum_nearest_same_surface_candidate_distance_km,
                    0.0,
                )
                np.testing.assert_array_equal(
                    dataset["SST_unsupported_water_mask"][:],
                    np.zeros((ny, nx), dtype=np.int8),
                )
                self.assertTrue(
                    np.isnan(
                        np.ma.asarray(
                            dataset["SST_nearest_same_surface_candidate_distance_km"][:]
                        ).filled(np.nan)
                    ).all()
                )
                valid = netCDF4.num2date(
                    dataset["time"][0], dataset["time"].units, dataset["time"].calendar
                )
                self.assertEqual((valid.year, valid.month, valid.day, valid.hour), (2020, 2, 10, 1))

    def test_boundary_sequence_requires_strict_bracketing_and_fixed_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def write(path: Path, valid_time: str) -> None:
                with netCDF4.Dataset(path, "w") as dataset:
                    dataset.createDimension("boundary_point", 2)
                    dataset.createDimension("level", 1)
                    dataset.createDimension("half_level", 2)
                    dataset.createVariable("row", "i4", ("boundary_point",))[:] = [0, 0]
                    dataset.createVariable("column", "i4", ("boundary_point",))[:] = [0, 1]
                    dataset.createVariable("relaxation_weight", "f8", ("boundary_point",))[:] = [
                        1.0,
                        0.5,
                    ]
                    for name in ("T", "P", "QV", "QC", "QI", "HFL"):
                        dataset.createVariable(name, "f8", ("level", "boundary_point"))[:] = 1.0
                    dataset.createVariable("HHL", "f8", ("half_level", "boundary_point"))[:] = 1.0
                    dataset.product_type = "hicar_lateral_boundary_state"
                    dataset.valid_time = valid_time
                    dataset.domain_nx = 2
                    dataset.domain_ny = 1
                    dataset.hicar_water_conversion = "APPLIED_JOINT_ALL_WATER_SPECIES"
                    dataset.hicar_pressure_adjustment = "APPLIED_HICAR_NATIVE"
                    dataset.wind_balance = "APPLIED_HICAR_ADJOINT_VARIATIONAL_PROJECTION"
                    dataset.lateral_w_policy = "regular_forcing_initial_guess_then_hicar_projection"
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
        lookup = {
            (int(row), int(col)): float(weight) for row, col, weight in zip(rows, cols, weights)
        }
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
            self.assertEqual(diagnostics["column_workers_effective"], 1)
            self.assertEqual(diagnostics["column_worker_start_method"], "serial")
            for name in (
                "timing_static_read_seconds",
                "timing_horizontal_remap_seconds",
                "timing_column_reconstruction_seconds",
                "timing_vertical_velocity_seconds",
                "timing_transform_total_seconds",
            ):
                self.assertGreaterEqual(diagnostics[name], 0.0)
            if "fork" in mp.get_all_start_methods():
                parallel_state, parallel_diagnostics = transform_icon_state(
                    source_path, static_path, weights, column_workers=2
                )
                for name in state:
                    np.testing.assert_array_equal(parallel_state[name], state[name])
                self.assertEqual(parallel_diagnostics["column_workers_effective"], 2)
                self.assertEqual(parallel_diagnostics["column_worker_start_method"], "fork")
            self.assertEqual(diagnostics["source_vertical_order"], "top_to_bottom")
            self.assertEqual(
                diagnostics["target_w_vertical_coordinate"], "authoritative_static_HFL"
            )
            self.assertEqual(state["W"].shape, state["HFL"].shape)
            with netCDF4.Dataset(static_path) as static:
                np.testing.assert_array_equal(state["HFL"], static["HFL"][:])
                self.assertFalse(
                    np.array_equal(state["HFL"], 0.5 * (state["HHL"][:-1] + state["HHL"][1:]))
                )
            write_initial_condition(
                initial_path,
                state,
                diagnostics,
                static_path=static_path,
                weights=weights,
            )
            write_boundary_condition(
                boundary_path,
                state,
                x=x,
                y=y,
                boundary_width_m=100.0,
                initial_condition_path=initial_path,
                valid_time=str(diagnostics["valid_time"]),
            )
            rows, cols = boundary_point_indices(x, y, 100.0)
            with (
                netCDF4.Dataset(initial_path) as initial,
                netCDF4.Dataset(boundary_path) as boundary,
            ):
                initial_t = np.asarray(initial["T"][:])
                np.testing.assert_allclose(boundary["T"][:], initial_t[:, rows, cols])
                self.assertEqual(boundary.dimensions["boundary_point"].size, 8)
                self.assertEqual(
                    set(boundary.variables),
                    {
                        "row",
                        "column",
                        "relaxation_weight",
                        "T",
                        "P",
                        "QV",
                        "QC",
                        "QI",
                        "HFL",
                        "HHL",
                    },
                )
                self.assertNotIn("U", boundary.variables)
                self.assertNotIn("V", boundary.variables)
                self.assertNotIn("W", boundary.variables)
                self.assertEqual(boundary.valid_time, "2020-01-01T00:00:00Z")
                self.assertEqual(initial.wind_balance, "SOURCE_NATIVE_REMAPPED")
                self.assertEqual(
                    initial.hicar_pressure_adjustment,
                    "HICARPREP_HYDROSTATIC_RECONSTRUCTION",
                )
                self.assertIn("specific humidity", initial.water_representation)


class SurfaceStateTests(unittest.TestCase):
    def test_global_fallback_provenance_is_exact_per_target(self) -> None:
        weights = RBFWeights(
            donor_index=np.array([[0, 2], [1, 0]]),
            weight=np.array([[0.75, 0.25], [0.75, 0.25]]),
            target_shape=(1, 2),
            source_fingerprint="source",
            target_fingerprint="target",
        )
        masks: list[np.ndarray] = []
        distances: list[np.ndarray] = []
        mapped, fallback_count, global_fallback_count = _supported_remap(
            weights,
            np.array([280.0, 279.0, 282.0]),
            np.array([True, False, True]),
            np.array([[False, False]]),
            source_lat=np.array([46.0, 46.02, 46.04]),
            source_lon=np.array([8.0, 8.02, 8.04]),
            target_lat=np.array([[46.001, 46.021]]),
            target_lon=np.array([[8.001, 8.021]]),
            global_fallback_masks=masks,
            global_fallback_distance_fields_km=distances,
        )
        np.testing.assert_allclose(mapped, [[279.0, 279.0]])
        self.assertEqual(fallback_count, 1)
        self.assertEqual(global_fallback_count, 1)
        self.assertEqual(len(masks), 1)
        self.assertEqual(len(distances), 1)
        np.testing.assert_array_equal(masks[0], [[True, False]])
        self.assertTrue(np.isfinite(distances[0][0, 0]))
        self.assertTrue(np.isnan(distances[0][0, 1]))

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
                dataset.createVariable("landuse", "i2", ("y", "x"))[:] = np.array([[7, 7], [7, 16]])
                dataset.createVariable("soil_type", "i2", ("y", "x"))[:] = 6
                dataset.createVariable("soil_type_layer", "i2", ("soil_layer", "y", "x"))[:] = (
                    np.array([6, 7, 8, 9])[:, None, None]
                )
            append_sleve_geometry(
                static,
                config=SleveConfig(
                    nz=3,
                    model_top_m=2500.0,
                    lowest_layer_m=200.0,
                    smooth_cycles=0,
                    minimum_layer_thickness_m=20.0,
                ),
            )

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
                    ("T_SNOW", 270.0),
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
                hydraulics["REFSMC"][target_indices] - hydraulics["WLTSMC"][target_indices]
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
                np.testing.assert_allclose(
                    smi_data["snow_temperature_initial"][:],
                    np.array([[271.0, 271.0], [271.0, 273.15]]),
                )
                self.assertEqual(smi_data.snow_temperature_source, "ICON T_SNOW")
                self.assertEqual(smi_data.snow_temperature_lower_bound_count, 3)
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
                np.testing.assert_allclose(dataset["surface_temperature"][:], 281.0)
                np.testing.assert_allclose(
                    dataset["soil_vwc"][:],
                    expected_smi_grid,
                )
            # Separately rounded float32 skin and snow temperatures can place
            # the serialized snow value one ULP below the recomputed lower
            # bound.  This is not a physical bound violation.
            with netCDF4.Dataset(runtime, "r+") as dataset:
                dataset["surface_temperature"][0, 0] = np.float32(260.1234)
                dataset["snow_temperature_initial"][0, 0] = np.float32(250.1234)
            validate_hicar_runtime_domain(runtime)

            with netCDF4.Dataset(external, "w") as dataset:
                dataset.createDimension("epoch", None)
                dataset.createDimension("month", 12)
                dataset.createDimension("y", 2)
                dataset.createDimension("x", 2)
                epoch = dataset.createVariable("epoch_time", "f8", ("epoch",))
                epoch[:] = [dt.datetime(2021, 1, 1, tzinfo=dt.timezone.utc).timestamp()]
                epoch.units = "seconds since 1970-01-01 00:00:00 UTC"
                landuse = dataset.createVariable("landuse", "i2", ("epoch", "y", "x"))
                landuse[:] = 15
                landuse.hicar_lifetime = "epoch"
                vegfrac = dataset.createVariable("VEGFRA", "f4", ("month", "y", "x"))
                vegfrac[:] = np.linspace(0.2, 0.8, 12)[:, None, None]
                vegfrac.hicar_lifetime = "climatology"
                lai = dataset.createVariable("LAI", "f4", ("month", "y", "x"))
                lai[:] = np.linspace(1.0, 4.0, 12)[:, None, None]
                lai.hicar_lifetime = "climatology"
                albedo = dataset.createVariable("ALBEDO", "f4", ("month", "y", "x"))
                albedo[:] = 20.0
                albedo.hicar_lifetime = "climatology"
            prepare_surface_state(
                source,
                static,
                smi_external,
                weights=weights,
                noahmp_table=table,
                soil_water_method="smi",
                external_path=external,
                allow_external_epoch_back_extrapolation=True,
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
                self.assertGreaterEqual(float(np.min(dataset["VEGFRA"][:])), 20.0)
                self.assertLessEqual(float(np.max(dataset["VEGFRA"][:])), 80.0)
                self.assertEqual(dataset["VEGFRA"].units, "percent")
                self.assertEqual(dataset["VEGFRA"].hicar_unit_conversion, "fraction_to_percent")
                self.assertEqual(dataset["LAI"].shape, (2, 2))
                self.assertEqual(dataset["ALBEDO"].units, "1")
                np.testing.assert_allclose(dataset["ALBEDO"][:], 0.2)
                self.assertEqual(
                    dataset["VEGFRA"].materialization_policy,
                    "preserved_12_month_climatology_for_hicar_monthly_vegfrac",
                )
                self.assertEqual(
                    dataset.external_parameters_valid_time,
                    "2020-01-01T00:00:00+00:00",
                )
                self.assertEqual(
                    dataset.land_state_external_epoch_back_extrapolation,
                    "explicit_research_override",
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
