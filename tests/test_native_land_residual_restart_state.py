from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT / "case_studies" / "swiss_200m" / "wind_climatology"
    / "assess_native_land_residual_restart_state.py"
)
SPEC = importlib.util.spec_from_file_location("residual_restart_state", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_comparison_uses_only_jointly_finite_values():
    result = MODULE.comparison(
        np.array([1.0, 4.0, np.nan]), np.array([0.0, 2.0, 3.0])
    )
    assert result["count"] == 2
    assert result["mean_bias"] == 1.5
    assert result["rmse"] == np.sqrt(2.5)


def test_metric_delta_preserves_missing_variables():
    candidate = {name: {"present": False} for name in MODULE.VARIABLES}
    legacy = {name: {"present": False} for name in MODULE.VARIABLES}
    candidate["swe_0"] = {"present": True, "rmse": 1.0}
    legacy["swe_0"] = {"present": True, "rmse": 2.0}
    result = MODULE.metric_delta(candidate, legacy)
    assert result["swe_0"]["candidate_over_legacy_rmse"] == 0.5
    assert result["swe_0"]["candidate_improved"] is True
    assert result["canopy_water"] == {
        "comparable": False,
        "reason": "missing_or_no_joint_finite_support",
    }
