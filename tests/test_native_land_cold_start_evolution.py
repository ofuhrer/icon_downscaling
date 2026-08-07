from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT / "case_studies" / "swiss_200m" / "wind_climatology"
    / "assess_native_land_cold_start_evolution.py"
)
SPEC = importlib.util.spec_from_file_location("cold_start_evolution", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def thresholds():
    return {
        "wind": {
            "vector_rmse_m_s": 0.2, "relative_vector_rmse": 0.03,
            "absolute_speed_bias_m_s": 0.1, "direction_mae_degrees": 5.0,
            "vector_error_p99_m_s": 0.75,
        },
        "pbl": {"relative_hpbl_rmse": 0.15, "absolute_hpbl_mean_bias_m": 100.0},
        "surface_reset_materiality": {
            "tsfe_rmse_k": 0.5, "hfss_absolute_mean_bias_w_m2": 20.0,
            "hfls_absolute_mean_bias_w_m2": 20.0,
        },
        "slow_state_reset_materiality": {
            "soil_temperature_rmse_k": 0.1, "soil_water_rmse_m3_m3": 0.002,
            "soil_column_water_absolute_mean_bias_kg_m2": 1.0,
        },
    }


def metrics(scale: float = 1.0):
    wind = {
        "10": {
            "vector_rmse_m_s": 0.1 * scale, "relative_vector_rmse": 0.01 * scale,
            "speed_bias_m_s": 0.01 * scale, "direction_mae_degrees": 1.0 * scale,
            "max_time_vector_error_p99_m_s": 0.2 * scale,
        }
    }
    scalar = {
        "hpbl": {"rmse": 50.0 * scale, "relative_rmse": 0.05 * scale, "mean_bias": 10.0 * scale},
        "tsfe": {"rmse": 0.2 * scale}, "hfss": {"mean_bias": 5.0 * scale},
        "hfls": {"mean_bias": -5.0 * scale},
        "soil_temperature": {"rmse": 0.05 * scale},
        "soil_water_content": {"rmse": 0.001 * scale},
        "soil_column_total_water": {"mean_bias": -0.5 * scale},
        "snow_height": {"rmse": 0.01 * scale},
    }
    return {"wind": wind, "scalars": scalar}


def test_family_passes_are_separate():
    result = MODULE.family_passes(metrics(), thresholds())
    assert result == {"wind": True, "pbl": True, "surface": True, "soil_snow": True, "all": True}
    wet = metrics()
    wet["scalars"]["soil_column_total_water"]["mean_bias"] = 2.0
    result = MODULE.family_passes(wet, thresholds())
    assert result["wind"] and result["pbl"] and result["surface"]
    assert not result["soil_snow"] and not result["all"]


def test_earliest_sustained_requires_both_origins_and_all_later_bins():
    def rows(first_pass: int):
        return [
            {"threshold_characterization": {family: i >= first_pass for family in ("wind", "pbl", "surface", "soil_snow", "all")}}
            for i in range(12)
        ]
    result = MODULE.earliest_sustained({"20200702": rows(2), "20200703": rows(3)})
    assert result["all"]["earliest_bin_end_hours"] == 24


def test_earliest_core_pass_requires_both_origins():
    def cores(first_pass: int):
        return [
            {
                "warmup_hours": warmup,
                "threshold_characterization": {
                    family: warmup >= first_pass
                    for family in ("wind", "pbl", "surface", "soil_snow", "all")
                },
            }
            for warmup in range(0, 49, 6)
        ]
    result = MODULE.earliest_core_pass({"20200702": cores(12), "20200703": cores(18)})
    assert result["all"]["earliest_warmup_hours"] == 18


def test_error_delta_reports_direction_and_ratio():
    result = MODULE.error_delta(metrics(0.5), metrics(1.0))
    assert result["tsfe_rmse_k"]["candidate_over_legacy"] == 0.5
    assert result["tsfe_rmse_k"]["candidate_improved"] is True
