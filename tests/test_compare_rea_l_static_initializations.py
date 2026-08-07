from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "case_studies" / "swiss_200m" / "validation"
SCRIPT = VALIDATION / "compare_rea_l_static_initializations.py"
sys.path.insert(0, str(VALIDATION))
SPEC = importlib.util.spec_from_file_location("compare_land_initialization", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_field_metrics_report_signed_bias_and_rmse():
    result = MODULE.field_metrics(np.array([2.0, 4.0]), np.array([1.0, 2.0]))
    assert result["mean_difference"] == 1.5
    np.testing.assert_allclose(result["rmse_difference"], np.sqrt(2.5))
    assert result["difference_range"] == [1.0, 2.0]
