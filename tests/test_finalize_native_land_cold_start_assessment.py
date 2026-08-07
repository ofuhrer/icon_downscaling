from __future__ import annotations

import importlib.util
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT / "case_studies" / "swiss_200m" / "wind_climatology"
    / "finalize_native_land_cold_start_assessment.py"
)
SPEC = importlib.util.spec_from_file_location("finalize_native_land", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def thresholds():
    return {
        "wind": {
            "vector_rmse_m_s": 0.2,
            "relative_vector_rmse": 0.03,
            "absolute_speed_bias_m_s": 0.1,
            "direction_mae_degrees": 5.0,
            "vector_error_p99_m_s": 0.75,
        },
        "pbl": {"relative_hpbl_rmse": 0.15, "absolute_hpbl_mean_bias_m": 100.0},
        "surface_reset_materiality": {
            "tsfe_rmse_k": 0.5,
            "hfss_absolute_mean_bias_w_m2": 20.0,
            "hfls_absolute_mean_bias_w_m2": 20.0,
        },
        "slow_state_reset_materiality": {
            "soil_temperature_rmse_k": 0.1,
            "soil_water_rmse_m3_m3": 0.002,
            "soil_column_water_absolute_mean_bias_kg_m2": 1.0,
        },
    }


def window(scale: float):
    return {
        "wind": {
            "10": {
                "vector_rmse_m_s": 0.1 * scale,
                "relative_vector_rmse": 0.01 * scale,
                "speed_bias_m_s": 0.02 * scale,
                "direction_mae_degrees": 1.0 * scale,
                "max_time_vector_error_p99_m_s": 0.2 * scale,
            }
        },
        "scalars": {
            "hpbl": {"relative_rmse": 0.05 * scale, "mean_bias": 10.0 * scale, "rmse": 50.0 * scale},
            "tsfe": {"rmse": 0.4 * scale},
            "hfss": {"mean_bias": -10.0 * scale},
            "hfls": {"mean_bias": 10.0 * scale},
            "soil_temperature": {"rmse": 0.08 * scale},
            "soil_water_content": {"rmse": 0.0015 * scale},
            "soil_column_total_water": {"mean_bias": -0.8 * scale},
        },
    }


def reports(candidate_scale: float = 0.5):
    limits = thresholds()
    legacy = {
        "status": "PASS",
        "thresholds": limits,
        "checks": {"same_initial_state_control": False},
        "window_vs_reference": {
            "origin-20200702": window(2.0),
            "origin-20200703": window(2.0),
        },
    }
    candidate = {
        "status": "PASS",
        "thresholds": deepcopy(limits),
        "checks": {
            "seam_excess_nonmaterial": True,
            "native_restart_wind_evolves": True,
            "fixed_height_wind_evolves": True,
        },
        "window_vs_reference": {
            "native-origin-20200702": window(candidate_scale),
            "native-origin-20200703": window(candidate_scale),
        },
    }
    return legacy, candidate


def test_success_is_not_blocked_by_preexisting_control_failure(tmp_path):
    legacy, candidate = reports()
    left = tmp_path / "legacy.json"
    right = tmp_path / "candidate.json"
    left.write_text("legacy")
    right.write_text("candidate")
    report = MODULE.build_report(
        legacy, candidate, legacy_path=left, candidate_path=right
    )
    assert report["decision"] == "PROMOTE_NATIVE_SMI_TO_CONTRASTING_REGIME"
    assert report["method_pass"] is True
    assert report["checks"]["legacy_same_initial_state_control"] is False
    assert report["reset_state_candidate_vs_legacy"]["20200702"][
        "soil_water_rmse_m3_m3"
    ]["candidate_over_legacy"] == 0.25


def test_persistent_slow_state_bias_retains_remap_but_isolates_smi(tmp_path):
    legacy, candidate = reports(candidate_scale=1.5)
    left = tmp_path / "legacy.json"
    right = tmp_path / "candidate.json"
    left.write_text("legacy")
    right.write_text("candidate")
    report = MODULE.build_report(
        legacy, candidate, legacy_path=left, candidate_path=right
    )
    assert report["decision"] == "RETAIN_NATIVE_REMAP_ISOLATE_SMI_OR_RESIDUAL_STATE"
    assert report["checks"]["candidate_retained_day_wind_pbl"] is True
    assert report["checks"]["candidate_retained_day_slow_state"] is False


def test_threshold_mismatch_is_rejected(tmp_path):
    legacy, candidate = reports()
    candidate["thresholds"]["wind"]["vector_rmse_m_s"] = 9.0
    left = tmp_path / "legacy.json"
    right = tmp_path / "candidate.json"
    left.write_text("legacy")
    right.write_text("candidate")
    try:
        MODULE.build_report(legacy, candidate, legacy_path=left, candidate_path=right)
    except ValueError as error:
        assert "frozen thresholds" in str(error)
    else:
        raise AssertionError("threshold mismatch was accepted")
