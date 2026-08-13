from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


PATH = Path(__file__).parents[1] / "scripts" / "compare_wind_sensitivity_reports.py"
SPEC = importlib.util.spec_from_file_location("compare_wind_sensitivity", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def source(speed_rmse: float, vector_rmse: float, model_mean: float) -> dict:
    return {
        "wind_speed_10m_m_s": {
            "count": 1,
            "root_mean_squared_error": speed_rmse,
            "model_mean": model_mean,
        },
        "wind_vector": {
            "count": 1,
            "vector_root_mean_squared_error_m_s": vector_rmse,
        },
    }


def report(hicar_speed: float, hicar_vector: float, rea_speed: float = 2.0) -> dict:
    return {
        "schema_version": 2,
        "matched_model_times": ["2020-01-01T00:00:00+00:00", "2020-01-01T01:00:00+00:00"],
        "site_metrics": {
            "A:1": {
                "hicar": source(hicar_speed, hicar_vector, hicar_speed),
                "rea_l": source(rea_speed, 2.5, rea_speed),
            }
        },
        "station_mapping": {
            "sites": [
                {
                    "key": "A:1",
                    "station_elevation_m": 2000.0,
                    "terrain_relative_elevation_m": 300.0,
                }
            ]
        },
    }


def test_compare_reports_material_speed_recovery_and_strata():
    result = MODULE.compare(report(2.0, 2.0), report(1.0, 2.0), ["A:1"])
    assert result["rea_l_metric_parity"]["passed"] is True
    assert result["strata"]["all_stations"]["station_count"] == 1
    assert result["strata"]["station_elevation_ge_1500m"]["station_count"] == 1
    speed = result["strata"]["all_stations"]["metrics"]["wind_speed_10m_m_s"]
    assert speed["classification"] == "material_improvement"
    assert result["largest_absolute_station_speed_changes"][0][
        "delta_sensitivity_minus_baseline_m_s"
    ] == -1.0


def test_compare_rejects_reference_mismatch():
    with pytest.raises(ValueError, match="REA-L parity failed"):
        MODULE.compare(report(2.0, 2.0), report(1.0, 2.0, rea_speed=2.1), ["A:1"])
