from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


PATH = Path(__file__).parents[1] / "scripts" / "compare_interpolation_control_to_hicar.py"
SPEC = importlib.util.spec_from_file_location("compare_interpolation_control", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def metric(speed: float, vector: float) -> dict:
    return {
        "wind_speed_10m_m_s": {"root_mean_squared_error": speed},
        "wind_vector": {"vector_root_mean_squared_error_m_s": vector},
    }


def fixtures(rea_offset: float = 0.0) -> tuple[dict, dict]:
    control = {"schema_version": 1, "seasons": {}}
    evaluators = {}
    for season in MODULE.SEASONS:
        control["seasons"][season] = {
            "site_metrics": {
                "A:1": {"control": metric(1.0, 1.5), "rea_l": metric(2.0, 2.5)},
                "B:2": {"control": metric(1.0, 1.5), "rea_l": metric(2.0, 2.5)},
            }
        }
        evaluators[season] = {
            "site_metrics": {
                "A:1": {
                    "hicar": metric(0.5, 1.0),
                    "rea_l": metric(2.0 + rea_offset, 2.5),
                },
                "B:2": {"hicar": metric(0.5, 1.0), "rea_l": metric(2.0, 2.5)},
            }
        }
    return control, evaluators


def test_compare_requires_and_reports_rea_l_parity():
    control, evaluators = fixtures()
    result = MODULE.compare(control, evaluators, 1.0e-12)
    assert result["cohort_station_count"] == 2
    assert result["rea_l_metric_parity"]["passed"] is True
    assert result["rea_l_metric_parity"]["maximum_absolute_difference_m_s"] == 0.0
    speed = result["event_evidence"][0]["metrics"]["wind_speed_10m_m_s"]
    assert speed["equal_station_rmse_m_s"] == {
        "hicar": 0.5,
        "control": 1.0,
        "rea_l": 2.0,
    }
    assert (
        speed["comparisons"]["hicar_minus_control"]["classification"]
        == "material_improvement"
    )


def test_compare_rejects_rea_l_metric_mismatch():
    control, evaluators = fixtures(rea_offset=1.0e-6)
    with pytest.raises(ValueError, match="REA-L station RMSE parity failed"):
        MODULE.compare(control, evaluators, 1.0e-12)
