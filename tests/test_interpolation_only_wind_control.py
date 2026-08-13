from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np


PATH = Path(__file__).parents[1] / "scripts" / "evaluate_interpolation_only_wind_control.py"
SPEC = importlib.util.spec_from_file_location("interpolation_only_control", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_scalar_metrics_and_vector_rmse_are_exact():
    observation = np.array([1.0, 2.0])
    model = np.array([2.0, 4.0])
    values = MODULE.scalar_metrics(model, observation)
    assert values["count"] == 2
    assert values["bias"] == 1.5
    assert values["root_mean_squared_error"] == np.sqrt(2.5)
    assert MODULE.vector_rmse(
        np.array([1.0, 2.0]),
        np.array([0.0, 0.0]),
        np.array([0.0, 0.0]),
        np.array([0.0, 0.0]),
    ) == np.sqrt(2.5)


def test_combined_decision_uses_fixed_four_event_intersection():
    def source(speed: float, vector: float) -> dict:
        return {
            "wind_speed_10m_m_s": {"root_mean_squared_error": speed},
            "wind_vector": {"vector_root_mean_squared_error_m_s": vector},
        }

    seasons = {}
    for label in MODULE.SEASONS:
        seasons[label] = {
            "site_metrics": {
                "A:1": {"control": source(1.0, 1.0), "rea_l": source(2.0, 2.0)},
                "B:2": {"control": source(2.0, 2.0), "rea_l": source(1.0, 1.0)},
            }
        }
    seasons["SON"]["site_metrics"].pop("B:2")
    result = MODULE.combined_decision(seasons)
    assert result["cohort_station_keys"] == ["A:1"]
    assert result["cohort_station_count"] == 1
    assert all(
        event["metrics"]["wind_vector"]["classification"] == "material_improvement"
        for event in result["event_evidence"]
    )
