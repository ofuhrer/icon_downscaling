"""Numerical-equivalence tests for opt-in vertical acceleration APIs."""

from __future__ import annotations

import unittest

import numpy as np

from preprocessing.hicarprep.vertical import (
    adjust_vertical_velocity,
    build_column_remap_plan,
    interpolate_interface_w_to_hfl,
    reconstruct_column_state,
    reconstruct_column_state_fast,
    reconstruct_column_state_with_plan,
)
from preprocessing.hicarprep.vertical_accelerated import adjust_vertical_velocity_to_hfl


class PlannedColumnReconstructionTests(unittest.TestCase):
    def test_plan_matches_reference_for_all_terrain_cases(self) -> None:
        source_hhl = 500.0 + np.array([0.0, 200.0, 600.0, 1400.0, 3000.0, 6000.0])
        source_z = 0.5 * (source_hhl[:-1] + source_hhl[1:])
        fields = {
            "temperature_k": 285.0 - 0.006 * (source_z - 500.0),
            "pressure_pa": 95_000.0 * np.exp(-(source_z - 500.0) / 8200.0),
            "qv": np.linspace(0.008, 0.001, source_z.size),
            "u_ms": np.linspace(2.0, 10.0, source_z.size),
            "v_ms": np.linspace(-1.0, 3.0, source_z.size),
            "hydrometeors": {
                "QC": np.linspace(0.0, 1.0e-4, source_z.size),
                "QI": np.linspace(2.0e-5, 0.0, source_z.size),
            },
        }
        target_offset = np.array([0.0, 100.0, 350.0, 900.0, 1800.0, 3500.0])
        for target_surface in (-300.0, 500.0, 1100.0):
            with self.subTest(target_surface=target_surface):
                target_hhl = target_surface + target_offset
                expected, expected_diagnostics = reconstruct_column_state(
                    source_hhl_m=source_hhl,
                    target_hhl_m=target_hhl,
                    **fields,
                )
                plan = build_column_remap_plan(source_hhl_m=source_hhl, target_hhl_m=target_hhl)
                actual, actual_diagnostics = reconstruct_column_state_with_plan(plan=plan, **fields)
                self.assertEqual(actual_diagnostics, expected_diagnostics)
                self.assertEqual(actual.keys(), expected.keys())
                for name in expected:
                    np.testing.assert_allclose(actual[name], expected[name], rtol=2.0e-14)

    def test_bulk_prevalidated_fast_path_has_same_result(self) -> None:
        source_hhl = np.array([0.0, 100.0, 300.0, 700.0, 1500.0, 3000.0])
        target_hhl = np.array([250.0, 400.0, 800.0, 1600.0, 2800.0])
        source_z = 0.5 * (source_hhl[:-1] + source_hhl[1:])
        fields = {
            "temperature_k": 290.0 - 0.005 * source_z,
            "pressure_pa": 100_000.0 * np.exp(-source_z / 8000.0),
            "qv": 0.01 * np.exp(-source_z / 2000.0),
            "u_ms": np.sin(source_z / 1000.0),
            "v_ms": np.cos(source_z / 1000.0),
        }
        plan = build_column_remap_plan(source_hhl_m=source_hhl, target_hhl_m=target_hhl)
        checked, checked_diagnostics = reconstruct_column_state_with_plan(plan=plan, **fields)
        unchecked, unchecked_diagnostics = reconstruct_column_state_fast(
            source_hhl_m=source_hhl,
            target_hhl_m=target_hhl,
            validate_fields=False,
            **fields,
        )
        self.assertEqual(unchecked_diagnostics, checked_diagnostics)
        for name in checked:
            np.testing.assert_array_equal(unchecked[name], checked[name])


class FusedVerticalVelocityTests(unittest.TestCase):
    def test_fused_kernel_matches_existing_two_stage_path(self) -> None:
        rng = np.random.default_rng(20260816)
        level_count, row_count, column_count = 7, 8, 9
        x = np.cumsum(rng.uniform(100.0, 300.0, column_count))
        y = np.cumsum(rng.uniform(100.0, 300.0, row_count))
        terrain = 400.0 + rng.uniform(0.0, 200.0, (row_count, column_count))
        thickness = rng.uniform(150.0, 700.0, (level_count, row_count, column_count))
        hhl = np.empty((level_count + 1, row_count, column_count))
        hhl[0] = terrain
        hhl[1:] = terrain + np.cumsum(thickness, axis=0)
        hfl = hhl[:-1] + rng.uniform(0.1, 0.9, thickness.shape) * thickness
        u = rng.normal(size=thickness.shape)
        v = rng.normal(size=thickness.shape)
        interface_w = rng.normal(size=hhl.shape)
        sine = rng.uniform(-0.3, 0.3, terrain.shape)
        cosine = np.sqrt(1.0 - sine * sine)

        adjusted_interface = adjust_vertical_velocity(
            target_hhl_m=hhl,
            interpolated_w_ms=interface_w,
            u_ms=u,
            v_ms=v,
            grid_sintheta=sine,
            grid_costheta=cosine,
            x_m=x,
            y_m=y,
        )
        expected = interpolate_interface_w_to_hfl(
            target_hhl_m=hhl,
            target_hfl_m=hfl,
            interface_w_ms=adjusted_interface,
        )
        actual = adjust_vertical_velocity_to_hfl(
            target_hhl_m=hhl,
            target_hfl_m=hfl,
            interpolated_w_ms=interface_w,
            u_ms=u,
            v_ms=v,
            grid_sintheta=sine,
            grid_costheta=cosine,
            x_m=x,
            y_m=y,
        )
        np.testing.assert_allclose(actual, expected, rtol=3.0e-13, atol=2.0e-15)

    def test_fused_kernel_supports_two_cell_first_order_edges(self) -> None:
        x = np.array([0.0, 200.0])
        y = np.array([0.0, 300.0])
        terrain = np.array([[0.0, 20.0], [30.0, 50.0]])
        hhl = np.stack((terrain, terrain + 500.0, terrain + 1500.0))
        hfl = 0.5 * (hhl[:-1] + hhl[1:])
        u = np.full(hfl.shape, 5.0)
        v = np.full(hfl.shape, -2.0)
        interface_w = np.ones_like(hhl)
        sine = np.zeros_like(terrain)
        cosine = np.ones_like(terrain)
        expected = interpolate_interface_w_to_hfl(
            target_hhl_m=hhl,
            target_hfl_m=hfl,
            interface_w_ms=adjust_vertical_velocity(
                target_hhl_m=hhl,
                interpolated_w_ms=interface_w,
                u_ms=u,
                v_ms=v,
                grid_sintheta=sine,
                grid_costheta=cosine,
                x_m=x,
                y_m=y,
            ),
        )
        actual = adjust_vertical_velocity_to_hfl(
            target_hhl_m=hhl,
            target_hfl_m=hfl,
            interpolated_w_ms=interface_w,
            u_ms=u,
            v_ms=v,
            grid_sintheta=sine,
            grid_costheta=cosine,
            x_m=x,
            y_m=y,
        )
        np.testing.assert_allclose(actual, expected, rtol=2.0e-14, atol=2.0e-15)


if __name__ == "__main__":
    unittest.main()
