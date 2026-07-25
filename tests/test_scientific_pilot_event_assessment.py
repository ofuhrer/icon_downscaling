import importlib.util
from datetime import datetime, timedelta
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
ASSESSOR = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "validation"
    / "assess_scientific_pilot_events.py"
)
PLAN = ROOT / "case_studies" / "swiss_200m" / "config" / "scientific_pilot_plan.json"
EXPECTED_HICAR_COMMIT = json.loads(PLAN.read_text())["configuration"][
    "event_expected_hicar_commit"
]
SPEC = importlib.util.spec_from_file_location("event_assessor", ASSESSOR)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def publish(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    Path(f"{path}.ready").touch()


def make_event(
    run_dir: Path,
    hicar_temperature_rmse: float = 2.0,
    source_commit: str = EXPECTED_HICAR_COMMIT,
    ogd_counts: tuple[int, int, int] = (2, 3, 24),
    ogd_hicar_temperature_rmse: float = 2.0,
    ogd_hicar_precipitation_rmse: float = 1.0,
    start: str = "2020-07-01T00:00:00",
    time_offset_hours: int = 0,
    chunk_id: str = "science_summer_20200701_00_20200704_00",
    validation_event_name: str | None = None,
    restart_hours: tuple[int, ...] = (24, 48, 72),
    duplicate_ogd_axis: bool = False,
) -> None:
    validation_event_name = validation_event_name or chunk_id
    first_time = datetime.fromisoformat(start) + timedelta(hours=time_offset_hours)
    model_times = [
        (first_time + timedelta(hours=3 * index)).isoformat()
        for index in range(25)
    ]
    event_day = first_time.replace(hour=0, minute=0, second=0, microsecond=0)
    matched_daily_windows = [
        {
            "rhires_day": (event_day + timedelta(days=index)).date().isoformat(),
            "window_start": (
                event_day + timedelta(days=index, hours=6)
            ).isoformat(),
            "window_end": (
                event_day + timedelta(days=index + 1, hours=6)
            ).isoformat(),
        }
        for index in range(ogd_counts[0])
    ]
    matched_temperature_days = [
        (event_day + timedelta(days=index)).date().isoformat()
        for index in range(ogd_counts[1])
    ]
    matched_radiation_times = model_times[1 : 1 + ogd_counts[2]]
    if duplicate_ogd_axis:
        for values in (
            matched_daily_windows,
            matched_temperature_days,
            matched_radiation_times,
        ):
            if len(values) >= 2:
                values[-1] = values[0]
    publish(
        run_dir / "model_chunk_completion.json",
        {
            "status": "PASS",
            "chunk_id": chunk_id,
            "output": {"times": model_times},
            "provenance": {
                "status": "PASS",
                "source_commit": source_commit,
            },
        },
    )
    validation = run_dir / "scientific_validation"
    publish(
        validation / "restart_checkpoint_diagnostics.json",
        {
            "status": "PASS",
            "event_name": validation_event_name,
            "checkpoint_count": len(restart_hours),
            "checkpoints": [
                {"elapsed_hours": elapsed, "status": "PASS"}
                for elapsed in restart_hours
            ],
        },
    )
    publish(validation / "solver_log_diagnostics.json", {"status": "PASS"})
    publish(
        validation / "scientific_event_diagnostics.json",
        {
            "status": "PASS",
            "event_name": validation_event_name,
            "classes": {
                "active_soil_interior": {
                    "surface_energy_diagnostic": {"mean_absolute_residual_w_m2": 2.0}
                }
            },
        },
    )
    publish(
        validation / "rea_l_source_comparison.json",
        {"status": "PASS", "event_name": validation_event_name},
    )
    publish(
        validation / "ogd_grid_comparison.json",
        {
            "status": "PASS",
            "event_name": validation_event_name,
            "matched_daily_windows": matched_daily_windows,
            "matched_temperature_days": matched_temperature_days,
            "matched_radiation_times": matched_radiation_times,
            "metrics": {
                "rhiresd": {
                    "hicar": {
                        "interior_ge_10km": {
                            "root_mean_squared_error": ogd_hicar_precipitation_rmse
                        }
                    },
                    "rea_l": {
                        "interior_ge_10km": {"root_mean_squared_error": 1.0}
                    },
                },
                "tabsd": {
                    "hicar": {
                        "interior_ge_10km": {
                            "root_mean_squared_error": ogd_hicar_temperature_rmse
                        }
                    },
                    "rea_l": {
                        "interior_ge_10km": {"root_mean_squared_error": 1.0}
                    },
                },
            },
        },
    )
    scalar_metrics = {
        "temperature_2m_height_adjusted_k": {
            "count": 500,
            "root_mean_squared_error": hicar_temperature_rmse,
        },
        "relative_humidity_2m_percent": {
            "count": 500,
            "root_mean_squared_error": 8.0,
        },
        "surface_pressure_height_adjusted_pa": {
            "count": 500,
            "root_mean_squared_error": 100.0,
        },
        "wind_speed_10m_m_s": {
            "count": 500,
            "root_mean_squared_error": 1.0,
        },
        "precipitation_interval_kg_m2": {
            "count": 500,
            "root_mean_squared_error": 1.0,
        },
        "wind_vector": {
            "count": 500,
            "vector_root_mean_squared_error_m_s": 1.5,
        },
    }
    reference_metrics = json.loads(json.dumps(scalar_metrics))
    reference_metrics["temperature_2m_height_adjusted_k"]["root_mean_squared_error"] = (
        1.0
    )
    publish(
        validation / "swissmetnet_comparison.json",
        {
            "status": "PASS",
            "event_name": validation_event_name,
            "matched_model_times": model_times,
            "metrics": {
                "hicar": {"all_sites": scalar_metrics},
                "rea_l": {"all_sites": reference_metrics},
            },
        },
    )


def run_assessment(
    tmp_path: Path,
    first_rmse=2.0,
    second_rmse=2.0,
    first_commit=EXPECTED_HICAR_COMMIT,
    second_commit=EXPECTED_HICAR_COMMIT,
    first_ogd_counts=(2, 3, 24),
    second_ogd_counts=(2, 3, 24),
    first_ogd_temperature_rmse=2.0,
    second_ogd_temperature_rmse=2.0,
    first_ogd_precipitation_rmse=1.0,
    second_ogd_precipitation_rmse=1.0,
    first_time_offset_hours=0,
    second_time_offset_hours=0,
    first_name="summer",
    second_name="winter",
    first_validation_event_name=None,
    second_validation_event_name=None,
    first_restart_hours=(24, 48, 72),
    second_restart_hours=(24, 48, 72),
    trajectory_status="PASS",
    trajectory_failures=None,
    first_duplicate_ogd_axis=False,
    second_duplicate_ogd_axis=False,
):
    summer = tmp_path / "summer"
    winter = tmp_path / "winter"
    report = tmp_path / "assessment.json"
    trajectory = tmp_path / "restart_trajectory.json"
    make_event(
        summer,
        first_rmse,
        first_commit,
        first_ogd_counts,
        first_ogd_temperature_rmse,
        first_ogd_precipitation_rmse,
        "2020-07-01T00:00:00",
        first_time_offset_hours,
        "science_summer_20200701_00_20200704_00",
        first_validation_event_name,
        first_restart_hours,
        first_duplicate_ogd_axis,
    )
    make_event(
        winter,
        second_rmse,
        second_commit,
        second_ogd_counts,
        second_ogd_temperature_rmse,
        second_ogd_precipitation_rmse,
        "2020-01-15T00:00:00",
        second_time_offset_hours,
        "science_winter_20200115_00_20200118_00",
        second_validation_event_name,
        second_restart_hours,
        second_duplicate_ogd_axis,
    )
    trajectory_start = datetime.fromisoformat("2020-07-03T00:00:00")
    publish(
        trajectory,
        {
            "status": trajectory_status,
            "expected_times": [
                (trajectory_start + timedelta(hours=3 * index)).isoformat()
                for index in range(1, 9)
            ],
            "failures": trajectory_failures or [],
        },
    )
    result = subprocess.run(
        [
            sys.executable,
            str(ASSESSOR),
            "--plan",
            str(PLAN),
            "--event",
            f"{first_name}={summer}",
            "--event",
            f"{second_name}={winter}",
            "--restart-trajectory-report",
            str(trajectory),
            "--report",
            str(report),
        ],
        text=True,
        capture_output=True,
    )
    return result, json.loads(report.read_text()), report


def test_paired_events_authorize_only_next_engineering_stages(tmp_path):
    result, report, path = run_assessment(tmp_path)
    assert result.returncode == 0, result.stderr + result.stdout
    assert report["decision"] == "GO_MONTH_AND_100M_CAPACITY_GATE"
    assert report["authorization"]["month_pilot"] is True
    assert report["authorization"]["100m_engineering_capacity_gate"] is True
    assert report["authorization"]["annual_cycle"] is False
    assert report["authorization"]["twenty_year_200m_production"] is False
    assert Path(f"{path}.ready").is_file()


def test_same_catastrophic_failure_in_both_events_stops_escalation(tmp_path):
    result, report, _ = run_assessment(tmp_path, first_rmse=4.0, second_rmse=4.0)
    assert result.returncode == 0, result.stderr + result.stdout
    assert report["decision"] == "STOP_AND_REDESIGN"
    assert (
        "catastrophic_degradation_temperature_2m_height_adjusted_k"
        in report["systematic_failed_screens"]
    )
    assert report["authorization"]["month_pilot"] is False


def test_one_event_failure_holds_for_diagnosis(tmp_path):
    result, report, _ = run_assessment(tmp_path, first_rmse=4.0, second_rmse=2.0)
    assert result.returncode == 0, result.stderr + result.stdout
    assert report["decision"] == "HOLD_AND_DIAGNOSE"


def test_event_source_commit_must_match_frozen_plan(tmp_path):
    result, report, _ = run_assessment(
        tmp_path,
        first_commit="b" * 40,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert report["decision"] == "HOLD_AND_DIAGNOSE"
    summer = next(event for event in report["events"] if event["event"] == "summer")
    assert "expected_hicar_source_commit" in summer["failed_screens"]
    assert report["authorization"]["month_pilot"] is False
    assert report["authorization"]["100m_engineering_capacity_gate"] is False


def test_event_ogd_report_requires_full_72_hour_coverage(tmp_path):
    result, report, _ = run_assessment(
        tmp_path,
        first_ogd_counts=(1, 1, 1),
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert report["decision"] == "HOLD_AND_DIAGNOSE"
    summer = next(event for event in report["events"] if event["event"] == "summer")
    assert {
        "complete_rhiresd_windows",
        "complete_tabsd_days",
        "matched_sis_times",
    } <= set(summer["failed_screens"])
    assert report["authorization"]["month_pilot"] is False


def test_event_ogd_duplicate_axes_cannot_inflate_coverage(tmp_path):
    result, report, _ = run_assessment(
        tmp_path,
        first_duplicate_ogd_axis=True,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert report["decision"] == "HOLD_AND_DIAGNOSE"
    summer = next(event for event in report["events"] if event["event"] == "summer")
    assert {
        "complete_rhiresd_windows",
        "complete_tabsd_days",
        "matched_sis_times",
    } <= set(summer["failed_screens"])
    assert report["authorization"]["month_pilot"] is False


def test_event_ogd_catastrophic_degradation_blocks_promotion(tmp_path):
    result, report, _ = run_assessment(
        tmp_path,
        first_ogd_temperature_rmse=10.0,
        first_ogd_precipitation_rmse=10.0,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert report["decision"] == "HOLD_AND_DIAGNOSE"
    summer = next(event for event in report["events"] if event["event"] == "summer")
    assert {
        "catastrophic_degradation_tabsd_temperature",
        "catastrophic_degradation_rhiresd_precipitation",
    } <= set(summer["failed_screens"])
    assert report["authorization"]["month_pilot"] is False


def test_event_time_axis_must_match_frozen_seasonal_period(tmp_path):
    result, report, _ = run_assessment(
        tmp_path,
        first_time_offset_hours=3,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert report["decision"] == "HOLD_AND_DIAGNOSE"
    summer = next(event for event in report["events"] if event["event"] == "summer")
    assert {
        "model_time_axis",
        "station_time_axis",
    } <= set(summer["failed_screens"])
    assert report["authorization"]["month_pilot"] is False


def test_event_names_must_be_exactly_summer_and_winter(tmp_path):
    result, report, path = run_assessment(
        tmp_path,
        second_name="summer",
    )

    assert result.returncode == 1, result.stderr + result.stdout
    assert report["decision"] == "INCOMPLETE"
    assert any(
        "exactly one summer and one winter" in reason
        for reason in report["incomplete_reasons"]
    )
    assert report["authorization"]["month_pilot"] is False
    assert not Path(f"{path}.ready").exists()


def test_validation_reports_must_match_model_chunk_identity(tmp_path):
    result, report, _ = run_assessment(
        tmp_path,
        first_validation_event_name="misrouted_winter_validation",
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert report["decision"] == "HOLD_AND_DIAGNOSE"
    summer = next(event for event in report["events"] if event["event"] == "summer")
    assert "cross_report_event_identity" in summer["failed_screens"]
    assert report["authorization"]["month_pilot"] is False


def test_all_three_event_restart_boundaries_are_required(tmp_path):
    result, report, _ = run_assessment(
        tmp_path,
        first_restart_hours=(24, 72),
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert report["decision"] == "HOLD_AND_DIAGNOSE"
    summer = next(event for event in report["events"] if event["event"] == "summer")
    assert "restart_checkpoint_boundaries" in summer["failed_screens"]
    assert report["authorization"]["month_pilot"] is False


def test_failed_restart_trajectory_blocks_event_promotion(tmp_path):
    result, report, _ = run_assessment(
        tmp_path,
        trajectory_status="FAIL",
        trajectory_failures=["synthetic mismatch"],
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert report["decision"] == "HOLD_AND_DIAGNOSE"
    assert report["restart_trajectory"]["passed"] is False
    assert report["authorization"]["month_pilot"] is False
