from case_studies.swiss_200m.wind_climatology.assess_repeated_day_equilibration import (
    assess_final_monotonic_trends,
    history_index,
    select_equilibrium,
    transition_passes,
)

import netCDF4
from datetime import datetime
import json
from pathlib import Path

from case_studies.swiss_200m.wind_climatology.prepare_repeated_day_assessment import (
    prepare,
)


def test_equilibrium_requires_consecutive_transitions() -> None:
    transitions = [
        {"to_cycle": 2, "passes": True},
        {"to_cycle": 3, "passes": False},
        {"to_cycle": 4, "passes": True},
        {"to_cycle": 5, "passes": True},
    ]
    assert select_equilibrium(transitions, consecutive_required=2) == 5


def test_equilibrium_can_remain_unbracketed() -> None:
    transitions = [
        {"to_cycle": 2, "passes": False},
        {"to_cycle": 3, "passes": True},
        {"to_cycle": 4, "passes": False},
    ]
    assert select_equilibrium(transitions, consecutive_required=2) is None


def _transition(soil: float, canopy: float, snow: float, total: float) -> dict:
    return {
        "metrics": {
            "slow_water_stores": {
                "soil_column_total_water": {"mean_bias": soil},
                "canopy_water": {"mean_bias": canopy},
                "snow_water_equivalent": {"mean_bias": snow},
                "combined_water_store": {"mean_bias": total},
            }
        }
    }


def test_material_same_sign_slow_store_drift_fails() -> None:
    transitions = [
        _transition(0.4, 0.0, 0.0, 0.4),
        _transition(0.4, 0.0, 0.0, 0.4),
        _transition(0.4, 0.0, 0.0, 0.4),
    ]
    result = assess_final_monotonic_trends(transitions, 3, 1.0)
    assert result["passes"] is False
    assert result["stores"]["soil_column_total_water"][
        "material_monotonic_drift"
    ]


def test_small_or_reversing_slow_store_changes_pass() -> None:
    transitions = [
        _transition(0.2, 0.1, 0.0, 0.3),
        _transition(-0.1, -0.1, 0.0, -0.2),
        _transition(0.2, 0.0, 0.0, 0.2),
    ]
    result = assess_final_monotonic_trends(transitions, 3, 1.0)
    assert result["passes"] is True


def test_history_index_snaps_subsecond_model_clock_offset(tmp_path: Path) -> None:
    path = tmp_path / "history.nc"
    start = datetime(2020, 7, 1, 1)
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("time", 3)
        time = dataset.createVariable("time", "f8", ("time",))
        time.units = "seconds since 2020-07-01 01:00:00"
        time.calendar = "proleptic_gregorian"
        time[:] = [0.432, 1800.432, 3600.432]
    indexed = history_index([path], start, datetime(2020, 7, 1, 2), 1800)
    assert sorted(indexed) == [
        datetime(2020, 7, 1, 1, 30),
        datetime(2020, 7, 1, 2),
    ]


def test_transition_rejects_a_frozen_fixed_height_wind() -> None:
    wind = {
        height: {
            "candidate_temporally_constant": height == "100m",
            "reference_temporally_constant": False,
            "phase_mean_vector_rmse_m_s": 0.05,
            "mean_speed_change_m_s": 0.02,
        }
        for height in ("10m", "50m", "100m")
    }
    metrics = {
        "wind": wind,
        "restart": {
            "soil_state": {
                "soil_temperature": {"rmse": 0.01},
                "soil_water_content": {"rmse": 0.0001},
            }
        },
        "slow_water_stores": {
            "soil_column_total_water": {"mean_bias": 0.1}
        },
    }
    thresholds = {
        "wind_phase_mean_vector_rmse_m_s": 0.2,
        "wind_daily_mean_speed_change_m_s": 0.1,
        "soil_temperature_boundary_rmse_K": 0.1,
        "soil_water_boundary_rmse_m3_m3": 0.002,
        "soil_column_water_mean_change_kg_m2": 1.0,
    }
    assert transition_passes(metrics, thresholds) is False


def test_manifest_can_use_internal_cycle_one_completion(tmp_path: Path) -> None:
    history = tmp_path / "history.nc"
    history.touch()
    completion_path = tmp_path / "completion.json"
    completion_path.write_text(
        json.dumps(
            {
                "status": "PASS",
                "output_interval_seconds": 1800,
                "output": {"files": [{"path": str(history)}]},
            }
        )
    )
    Path(f"{completion_path}.ready").touch()
    experiment = tmp_path / "experiment.json"
    experiment.write_text(
        json.dumps(
            {
                "status": "RUNNING",
                "model_interval": {
                    "start": "2020-07-01T01:00:00",
                    "end": "2020-07-02T01:00:00",
                },
                "equilibration_rule": {"consecutive_passing_transitions": 2},
            }
        )
    )
    output = tmp_path / "manifest.json"
    payload = prepare(
        tmp_path,
        1,
        completion_path,
        None,
        None,
        experiment,
        output,
    )
    assert payload["cycles"][0]["history_files"] == [str(history)]
    assert Path(f"{output}.ready").is_file()
