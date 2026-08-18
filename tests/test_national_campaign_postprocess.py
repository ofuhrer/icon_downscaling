import csv
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "national_campaign_postprocess.py"
SPEC = importlib.util.spec_from_file_location("national_campaign_postprocess", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def metric(
    rmse,
    count=24,
    vector=False,
    model_mean=None,
    observation_mean=None,
    bias=None,
):
    field = "vector_root_mean_squared_error_m_s" if vector else "root_mean_squared_error"
    result = {"count": count, field: rmse}
    if model_mean is not None:
        result.update(
            {
                "model_mean": model_mean,
                "observation_mean": observation_mean,
                "bias": bias,
                "mean_absolute_error": abs(bias),
                "centered_root_mean_squared_error": (max(rmse**2 - bias**2, 0.0) ** 0.5),
                "model_standard_deviation": 1.2,
                "observation_standard_deviation": 1.0,
                "correlation": 0.8,
            }
        )
    return result


def refresh_wind_report_totals(report):
    accounting = {}
    aggregate = {source: {"all_sites": {}} for source in MODULE.SOURCES}
    for metric_name in MODULE.WIND_METRICS:
        accepted = sum(
            values["hicar"][metric_name]["count"] for values in report["site_metrics"].values()
        )
        accounting[metric_name] = {
            "candidate_station_time_count": accepted,
            "accepted_common_triplet_count": accepted,
            "excluded_station_time_count": 0,
            "exclusions": {},
        }
        for source in MODULE.SOURCES:
            aggregate[source]["all_sites"][metric_name] = {"count": accepted}
    report["common_triplet_accounting"] = {"metrics": accounting}
    report["metrics"] = aggregate


def make_report(path, season, site_count=67, common_count=65, mismatch=False):
    sites = []
    site_metrics = {}
    for index in range(site_count):
        key = f"S{index:03d}:{index + 1}"
        elevation = 3100.0 if index == 0 else 1600.0 if index < 15 else 450.0
        relative = 200.0 if index < 12 else -200.0 if index < 24 else 0.0
        sites.append(
            {
                "key": key,
                "abbreviation": f"S{index:03d}",
                "meas_site": str(index + 1),
                "latitude": 46.0 + index * 0.001,
                "longitude": 7.0 + index * 0.001,
                "station_elevation_m": elevation,
                "hicar_elevation_m": elevation + 10.0,
                "nearest_cell_distance_km": 0.1,
                "terrain_relative_elevation_m": relative,
                "hicar_y_index": index,
                "hicar_x_index": index,
            }
        )
        h_count = 23 if mismatch and index == 66 else 24
        site_metrics[key] = {
            "hicar": {
                "temperature_2m_height_adjusted_k": metric(
                    1.0 + index / 100.0,
                    h_count,
                    model_mean=281.0 + index / 100.0,
                    observation_mean=280.0,
                    bias=1.0 + index / 100.0,
                ),
                "wind_vector": metric(3.0 + index / 100.0, vector=True),
                "wind_speed_10m_m_s": metric(2.0 + index / 100.0),
            },
            "rea_l": {
                "temperature_2m_height_adjusted_k": metric(
                    1.5,
                    24,
                    model_mean=281.5,
                    observation_mean=280.0,
                    bias=1.5,
                ),
                "wind_vector": metric(2.5, vector=True),
                "wind_speed_10m_m_s": metric(2.5),
            },
        }
    lead_template = {
            "hicar": {
                "all_sites": {
                    "temperature_2m_height_adjusted_k": metric(
                        1.0,
                        site_count,
                        model_mean=281.0,
                        observation_mean=280.0,
                        bias=1.0,
                    ),
                    "wind_vector": metric(3.0, site_count, vector=True),
                    "wind_speed_10m_m_s": metric(2.0, site_count),
                },
                "terrain_ridge_relative_gt_150m": {
                    "temperature_2m_height_adjusted_k": metric(
                        2.0,
                        2,
                        model_mean=282.0,
                        observation_mean=280.0,
                        bias=2.0,
                    ),
                    "wind_vector": metric(4.0, 2, vector=True),
                    "wind_speed_10m_m_s": metric(2.0, 2),
            },
            },
            "rea_l": {
                "all_sites": {
                    "temperature_2m_height_adjusted_k": metric(
                        1.5,
                        site_count,
                        model_mean=281.5,
                        observation_mean=280.0,
                        bias=1.5,
                    ),
                    "wind_vector": metric(2.5, site_count, vector=True),
                    "wind_speed_10m_m_s": metric(2.5, site_count),
                },
                "terrain_ridge_relative_gt_150m": {
                    "temperature_2m_height_adjusted_k": metric(
                        1.0,
                        2,
                        model_mean=281.0,
                        observation_mean=280.0,
                        bias=1.0,
                    ),
                    "wind_vector": metric(3.0, 2, vector=True),
                    "wind_speed_10m_m_s": metric(2.5, 2),
            },
            },
        }
    lead = {
        str(physical_lead): lead_template for physical_lead in MODULE.REQUIRED_WIND_PHYSICAL_LEADS
    }
    evaluation_start = datetime(2020, 1, 2, tzinfo=timezone.utc)
    report = {
        "schema_version": 1,
        "event_name": f"event-{season}",
        "sampling": {
            "simulation_start": "2020-01-01T00:00:00+00:00",
            "evaluation_start_inclusive": evaluation_start.isoformat(),
            "evaluation_end_inclusive": (evaluation_start + timedelta(hours=24)).isoformat(),
        },
        "matched_model_times": [
            (evaluation_start + timedelta(hours=index)).isoformat()
            for index in range(MODULE.REQUIRED_WIND_MATCHED_ENDPOINT_COUNT)
        ],
        "station_mapping": {"sites": sites},
        "site_metrics": site_metrics,
        "lead_time_metrics": lead,
        "issues": [],
    }
    refresh_wind_report_totals(report)
    path.write_text(json.dumps(report))
    return path


def test_report_requires_common_triplet_accounting(tmp_path):
    path = make_report(tmp_path / "legacy.json", "DJF")
    report = json.loads(path.read_text())
    report.pop("common_triplet_accounting")
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="common_triplet_accounting"):
        MODULE.load_report(path)


def test_station_event_summary_requires_twenty_common_pairs(tmp_path):
    reports = {}
    for season in MODULE.SEASONS:
        path = make_report(tmp_path / f"low-count-{season}.json", season, site_count=1)
        report = json.loads(path.read_text())
        values = report["site_metrics"]["S000:1"]
        for source in MODULE.SOURCES:
            values[source]["temperature_2m_height_adjusted_k"]["count"] = 19
        path.write_text(json.dumps(report))
        reports[season] = (path, MODULE.load_report(path))

    rows, exclusions, _ = MODULE.station_season_rows(
        reports,
        common_keys=None,
        selected_metrics={"temperature_2m_height_adjusted_k"},
    )
    assert rows == []
    assert exclusions == {
        f"insufficient_pair_count:{season}:temperature_2m_height_adjusted_k": 1
        for season in MODULE.SEASONS
    }


def test_national_summary_and_exact_common_65(tmp_path):
    national = {}
    bridge = []
    for season in MODULE.SEASONS:
        national[season] = make_report(
            tmp_path / f"national-{season}.json", season, mismatch=season == "SON"
        )
        bridge.append(make_report(tmp_path / f"bridge-{season}.json", season, 65))
    output_csv = tmp_path / "station-season.csv"
    output_json = tmp_path / "summary.json"
    argv = []
    for season, path in national.items():
        argv.extend(["--report", f"{season}={path}"])
    for path in bridge:
        argv.extend(["--common-65-report", str(path)])
    argv.extend(["--output-csv", str(output_csv), "--output-summary", str(output_json)])

    assert MODULE.main(argv) == 0
    summary = json.loads(output_json.read_text())
    with output_csv.open(newline="") as stream:
        rows = list(csv.DictReader(stream))

    assert summary["coverage"]["station_key_union_count"] == 67
    assert summary["coverage"]["station_key_four_season_intersection_count"] == 67
    assert summary["national_four_season_intersection"]["site_count"] == 67
    assert summary["coverage"]["metric_eligible_four_season_intersection_counts"] == {
        "temperature_2m_height_adjusted_k": 66,
        "wind_speed_10m_m_s": 67,
        "wind_vector": 67,
    }
    assert (
        summary["national_metric_four_season_intersections"]["temperature_2m_height_adjusted_k"][
            "site_count"
        ]
        == 66
    )
    assert (
        "S066:67"
        not in summary["national_metric_four_season_intersections"][
            "temperature_2m_height_adjusted_k"
        ]["site_keys"]
    )
    assert summary["common_65"]["site_count"] == 65
    assert summary["common_65"]["site_keys"][0] == "S000:1"
    assert len(rows) == 4 * 67 * 3 - 1
    assert summary["data_quality"]["station_season_exclusions"] == {
        "unequal_pair_count:SON:temperature_2m_height_adjusted_k": 1
    }
    national_temperature = next(
        item
        for item in summary["equal_station_summaries"]
        if item["subset"] == "national"
        and item["season"] == "DJF"
        and item["stratum"] == "all_sites"
        and item["metric"] == "temperature_2m_height_adjusted_k"
    )
    assert national_temperature["paired_station_count"] == 67
    assert national_temperature["improved_station_count"] == 50
    assert national_temperature["degraded_station_count"] == 16
    assert national_temperature["tied_station_count"] == 1
    assert national_temperature["mean_bias_paired_station_count"] == 67
    assert national_temperature["mean_station_hicar_rmse"] == pytest.approx(1.33)
    assert national_temperature["mean_station_rea_l_rmse"] == pytest.approx(1.5)
    assert national_temperature["equal_station_mean_hicar_rmse"] == pytest.approx(1.33)
    assert national_temperature["equal_station_mean_rea_l_rmse"] == pytest.approx(1.5)
    assert national_temperature["equal_station_network_hicar_rmse"] == pytest.approx(
        (sum((1.0 + index / 100.0) ** 2 for index in range(67)) / 67) ** 0.5
    )
    assert national_temperature["equal_station_network_rea_l_rmse"] == pytest.approx(1.5)
    assert national_temperature["equal_station_mean_hicar_bias"] == pytest.approx(1.33)
    assert national_temperature["equal_station_mean_rea_l_bias"] == pytest.approx(1.5)
    assert national_temperature["equal_station_mean_hicar_mae"] == pytest.approx(1.33)
    assert national_temperature["equal_station_mean_rea_l_mae"] == pytest.approx(1.5)
    assert national_temperature["equal_station_rms_hicar_station_bias"] == pytest.approx(
        national_temperature["equal_station_network_hicar_rmse"]
    )
    assert national_temperature["equal_station_rms_rea_l_station_bias"] == 1.5
    assert national_temperature["equal_station_within_station_hicar_centered_rmse"] == 0.0
    assert national_temperature["equal_station_within_station_rea_l_centered_rmse"] == 0.0
    assert national_temperature["equal_station_network_hicar_centered_rmse"] == 0.0
    assert national_temperature["equal_station_network_rea_l_centered_rmse"] == 0.0
    assert national_temperature["diagnostic_paired_station_count"] == 67
    assert national_temperature["median_station_hicar_standard_deviation_ratio"] == 1.2
    assert national_temperature["median_station_rea_l_standard_deviation_ratio"] == 1.2
    assert national_temperature["median_station_hicar_correlation"] == 0.8
    assert national_temperature["median_station_rea_l_correlation"] == 0.8
    assert national_temperature["equal_station_mean_hicar_model_mean"] == pytest.approx(281.33)
    assert national_temperature["equal_station_mean_rea_l_model_mean"] == pytest.approx(281.5)
    assert national_temperature["equal_station_mean_hicar_observation_mean"] == pytest.approx(280.0)
    assert national_temperature["equal_station_mean_rea_l_observation_mean"] == pytest.approx(280.0)
    assert national_temperature["equal_station_mean_observation_mean"] == pytest.approx(280.0)
    # Retained compatibility alias for existing consumers.
    assert national_temperature["equal_station_mean_observation"] == pytest.approx(280.0)
    assert national_temperature["network_pooled_hicar_rmse"] == pytest.approx(
        (sum((1.0 + index / 100.0) ** 2 for index in range(67)) / 67) ** 0.5
    )
    assert national_temperature["network_pooled_rea_l_rmse"] == pytest.approx(1.5)
    assert national_temperature["network_pooled_rmse_delta_hicar_minus_rea_l"] == pytest.approx(
        national_temperature["network_pooled_hicar_rmse"] - 1.5
    )
    national_intersection_temperature = next(
        item
        for item in summary["equal_station_summaries"]
        if item["subset"] == "national_four_season_intersection"
        and item["season"] == "DJF"
        and item["stratum"] == "all_sites"
        and item["metric"] == "temperature_2m_height_adjusted_k"
    )
    assert national_intersection_temperature["paired_station_count"] == 66
    intersection_counts = {
        (item["season"], item["metric"]): item["paired_station_count"]
        for item in summary["equal_station_summaries"]
        if item["subset"] == "national_four_season_intersection" and item["stratum"] == "all_sites"
    }
    assert all(
        intersection_counts[(season, "temperature_2m_height_adjusted_k")] == 66
        for season in MODULE.SEASONS
    )
    assert all(intersection_counts[(season, "wind_vector")] == 67 for season in MODULE.SEASONS)
    lead_temperature = next(
        item
        for item in summary["lead_hour_tables"]["DJF"]
        if item["metric"] == "temperature_2m_height_adjusted_k" and item["stratum"] == "all_sites"
    )
    assert lead_temperature["hicar_bias"] == pytest.approx(1.0)
    assert lead_temperature["rea_l_bias"] == pytest.approx(1.5)
    assert lead_temperature["hicar_model_mean"] == pytest.approx(281.0)
    assert lead_temperature["rea_l_model_mean"] == pytest.approx(281.5)
    assert lead_temperature["hicar_observation_mean"] == pytest.approx(280.0)
    assert lead_temperature["rea_l_observation_mean"] == pytest.approx(280.0)
    assert lead_temperature["observation_mean"] == pytest.approx(280.0)
    assert lead_temperature["lead_hour"] == 1
    assert lead_temperature["physical_lead_hour"] == 25
    assert {(item["stratum"], item["metric"]) for item in summary["lead_hour_tables"]["JJA"]} == {
        ("all_sites", "temperature_2m_height_adjusted_k"),
        ("all_sites", "wind_speed_10m_m_s"),
        ("all_sites", "wind_vector"),
        ("terrain_ridge_relative_gt_150m", "temperature_2m_height_adjusted_k"),
        ("terrain_ridge_relative_gt_150m", "wind_speed_10m_m_s"),
        ("terrain_ridge_relative_gt_150m", "wind_vector"),
    }
    ridge_wind = next(
        item
        for item in summary["lead_hour_tables"]["JJA"]
        if item["stratum"] == "terrain_ridge_relative_gt_150m" and item["metric"] == "wind_vector"
    )
    assert ridge_wind["pair_count"] == 2
    assert ridge_wind["hicar_rmse"] == pytest.approx(4.0)
    assert ridge_wind["rea_l_rmse"] == pytest.approx(3.0)
    assert len(summary["selected_site_listings"]["station_elevation_ge_3000m"]) == 1
    assert len(summary["selected_site_listings"]["terrain_ridge_relative_gt_150m"]) == 12
    decision = summary["wind_decision_readout"]
    assert decision["classification"] == "degraded"
    assert decision["event_counts"]["wind_vector"] == {
        "material_improvement": 0,
        "neutral": 0,
        "material_degradation": 4,
        "nondegradation": 0,
    }
    assert decision["event_counts"]["wind_speed_10m_m_s"] == {
        "material_improvement": 4,
        "neutral": 0,
        "material_degradation": 0,
        "nondegradation": 4,
    }
    assert {event["classification"] for event in decision["event_evidence"]} == {"degraded"}
    assert decision["station_event_evidence"]["wind_vector"]["median_direction"] == "degrading"
    assert decision["leave_one_event_out"]["wind_vector"]["all_omissions_nondegrading"] is False
    assert decision["safeguards"]["status"] == "fail"
    assert {item["stratum"] for item in decision["safeguards"]["strata"]} == set(
        MODULE.WIND_SAFEGUARDS
    )
    assert summary["footprint_sensitivity"]["status"] == (
        "not_computable_from_evaluator_aggregates"
    )
    assert (
        "approximately 10-km-wide square (5-km half-width)"
        in summary["method"]["terrain_ridge_definition"]
    )


def test_network_pooled_rmse_uses_pair_counts_and_preserves_equal_station_mean():
    rows = [
        {
            "pair_count": 1,
            "hicar_rmse": 1.0,
            "rea_l_rmse": 2.0,
            "rmse_delta_hicar_minus_rea_l": -1.0,
            "outcome": "improved",
        },
        {
            "pair_count": 3,
            "hicar_rmse": 3.0,
            "rea_l_rmse": 4.0,
            "rmse_delta_hicar_minus_rea_l": -1.0,
            "outcome": "improved",
        },
    ]

    summary = MODULE.summarize_group(rows)

    assert summary["pair_count_total"] == 4
    assert summary["equal_station_mean_hicar_rmse"] == pytest.approx(2.0)
    assert summary["equal_station_mean_rea_l_rmse"] == pytest.approx(3.0)
    assert summary["mean_station_hicar_rmse"] == pytest.approx(2.0)
    assert summary["mean_station_rea_l_rmse"] == pytest.approx(3.0)
    assert summary["equal_station_network_hicar_rmse"] == pytest.approx(5.0**0.5)
    assert summary["equal_station_network_rea_l_rmse"] == pytest.approx(10.0**0.5)
    assert summary["equal_station_network_rmse_delta_hicar_minus_rea_l"] == pytest.approx(
        5.0**0.5 - 10.0**0.5
    )
    assert summary["network_pooled_hicar_rmse"] == pytest.approx(7.0**0.5)
    assert summary["network_pooled_rea_l_rmse"] == pytest.approx(13.0**0.5)
    assert summary["network_pooled_rmse_delta_hicar_minus_rea_l"] == pytest.approx(
        7.0**0.5 - 13.0**0.5
    )


def make_wind_decision_rows(event_deltas, site_count=30, pair_count=24):
    rows = []
    for season, (vector_delta, speed_delta) in zip(MODULE.SEASONS, event_deltas, strict=True):
        for index in range(site_count):
            terrain = (
                "terrain_ridge_relative_gt_150m"
                if index < 10
                else "terrain_valley_relative_lt_minus_150m"
                if index < 20
                else "terrain_neutral_relative_pm_150m"
            )
            for metric_name, delta in (
                ("wind_vector", vector_delta),
                ("wind_speed_10m_m_s", speed_delta),
            ):
                rows.append(
                    {
                        "season": season,
                        "event_name": f"event-{season}",
                        "station_key": f"S{index:03d}:1",
                        "station_elevation_m": 1600.0 if index < 10 else 500.0,
                        "terrain_class": terrain,
                        "metric": metric_name,
                        "pair_count": pair_count,
                        "hicar_rmse": 2.0 + delta,
                        "rea_l_rmse": 2.0,
                        "rmse_delta_hicar_minus_rea_l": delta,
                        "outcome": "improved" if delta < 0 else "degraded" if delta > 0 else "tied",
                    }
                )
    return rows


@pytest.mark.parametrize(
    ("event_deltas", "expected"),
    [
        ([(-0.2, -0.2)] * 4, "strong"),
        ([(-0.2, 0.0)] * 4, "qualified"),
        ([(0.0, 0.0)] * 4, "neutral"),
        ([(-0.2, 0.0), (0.0, 0.0), (0.0, 0.0), (0.0, 0.0)], "mixed"),
        ([(0.2, 0.0), (0.2, 0.0), (0.0, 0.0), (0.0, 0.0)], "degraded"),
    ],
)
def test_preregistered_wind_campaign_classifications(event_deltas, expected):
    decision = MODULE.wind_decision_readout(make_wind_decision_rows(event_deltas))

    assert decision["classification"] == expected
    assert decision["next_action"]["interpolation_only_control_required"] is (
        expected in {"degraded", "neutral", "mixed"}
    )
    assert len(decision["event_evidence"]) == 4
    assert all(len(values["omissions"]) == 4 for values in decision["leave_one_event_out"].values())


def test_wind_truth_table_tolerates_one_vector_degradation_but_never_rescues_event():
    decision = MODULE.wind_decision_readout(
        make_wind_decision_rows([(-0.3, -0.3), (-0.3, -0.3), (0.0, 0.0), (0.2, -0.3)])
    )

    assert decision["classification"] == "strong"
    degraded_event = next(event for event in decision["event_evidence"] if event["season"] == "SON")
    assert degraded_event["metrics"]["wind_vector"]["classification"] == ("material_degradation")
    assert degraded_event["classification"] == "degraded"
    assert decision["requirements"]["vector_nondegradation"]["observed"] == 3
    assert decision["requirements"]["vector_leave_one_event_out_nondegradation"]["passes"] is True


@pytest.mark.parametrize(
    ("vector", "speed", "expected"),
    [
        ("material_improvement", "material_improvement", "strong"),
        ("material_improvement", "neutral", "qualified"),
        ("material_improvement", "material_degradation", "mixed"),
        ("neutral", "material_improvement", "mixed"),
        ("neutral", "neutral", "neutral"),
        ("neutral", "material_degradation", "degraded"),
        ("material_degradation", "material_improvement", "degraded"),
        ("material_degradation", "neutral", "degraded"),
        ("material_degradation", "material_degradation", "degraded"),
    ],
)
def test_joint_wind_event_truth_table(vector, speed, expected):
    assert MODULE.joint_wind_event_classification(vector, speed) == expected


def test_wind_truth_table_freezes_staggered_and_repeated_speed_outcomes():
    staggered = MODULE.wind_decision_readout(
        make_wind_decision_rows([(-0.2, 0.0), (-0.2, 0.0), (0.0, -0.2), (0.0, -0.2)])
    )
    repeated_speed_regression = MODULE.wind_decision_readout(
        make_wind_decision_rows([(-0.2, 0.2), (-0.2, 0.2), (0.0, 0.0), (0.0, 0.0)])
    )

    assert staggered["classification"] == "qualified"
    assert staggered["joint_event_counts"]["strong"] == 0
    assert repeated_speed_regression["classification"] == "mixed"
    assert repeated_speed_regression["event_counts"]["wind_vector"]["material_degradation"] == 0
    assert repeated_speed_regression["next_action"]["interpolation_only_control_required"] is True


def test_vector_gate_requires_all_leave_one_event_out_medians_nondegrading():
    decision = MODULE.wind_decision_readout(
        make_wind_decision_rows([(-0.3, 0.0), (-0.2, 0.0), (0.05, 0.0), (0.05, 0.0)])
    )

    assert decision["event_counts"]["wind_vector"]["material_improvement"] == 2
    assert decision["station_event_evidence"]["wind_vector"]["median_direction"] == ("improving")
    assert decision["leave_one_event_out"]["wind_vector"]["all_omissions_nondegrading"] is False
    assert decision["classification"] == "mixed"


def apply_ridge_vector_regression(rows, seasons):
    updated = [dict(row) for row in rows]
    for row in updated:
        if (
            row["season"] in seasons
            and row["metric"] == "wind_vector"
            and row["terrain_class"] == "terrain_ridge_relative_gt_150m"
        ):
            row["hicar_rmse"] = 2.2
            row["rmse_delta_hicar_minus_rea_l"] = 0.2
            row["outcome"] = "degraded"
    return updated


def test_one_safeguard_regression_passes_but_two_fail_and_force_degraded():
    baseline = make_wind_decision_rows([(-0.4, 0.0)] * 4)
    one = MODULE.wind_decision_readout(apply_ridge_vector_regression(baseline, {"DJF"}))
    two = MODULE.wind_decision_readout(apply_ridge_vector_regression(baseline, {"DJF", "MAM"}))

    assert one["safeguards"]["status"] == "pass"
    assert one["classification"] == "qualified"
    assert two["safeguards"]["status"] == "fail"
    assert two["classification"] == "degraded"
    assert two["next_action"]["interpolation_only_control_required"] is True


def test_fixed_cohort_intersects_changing_event_populations():
    rows = make_wind_decision_rows([(-0.2, -0.2)] * 4)
    rows = [
        row
        for row in rows
        if not (
            (row["season"] == "DJF" and row["station_key"] == "S029:1")
            or (row["season"] == "MAM" and row["station_key"] == "S028:1")
    )
    ]

    decision = MODULE.wind_decision_readout(rows)

    assert decision["classification"] == "strong"
    assert decision["cohort"]["station_count"] == 28
    assert all(event["paired_station_count"] == 28 for event in decision["event_evidence"])
    assert "S028:1" not in decision["cohort"]["station_keys"]
    assert "S029:1" not in decision["cohort"]["station_keys"]


def test_unequal_vector_speed_pair_count_excludes_station_from_fixed_cohort():
    rows = make_wind_decision_rows([(-0.2, 0.0)] * 4)
    for row in rows:
        if (
            row["season"] == "SON"
            and row["station_key"] == "S029:1"
            and row["metric"] == "wind_speed_10m_m_s"
        ):
            row["pair_count"] = 23

    decision = MODULE.wind_decision_readout(rows)

    assert decision["cohort"]["station_count"] == 29
    assert "S029:1" not in decision["cohort"]["station_keys"]


def test_material_threshold_boundary_is_neutral_and_raw_values_are_retained():
    evidence = MODULE.material_wind_change(2.1, 2.0)

    assert evidence == {
        "hicar_rmse_m_s": 2.1,
        "rea_l_rmse_m_s": 2.0,
        "delta_hicar_minus_rea_l_m_s": pytest.approx(0.1),
        "material_threshold_m_s": pytest.approx(0.1),
        "classification": "neutral",
    }


@pytest.mark.parametrize(
    ("hicar_rmse", "expected"),
    [
        (1.9, "neutral"),
        (1.9 - 1.0e-9, "material_improvement"),
        (2.1, "neutral"),
        (2.1 + 1.0e-9, "material_degradation"),
    ],
)
def test_material_threshold_equality_and_epsilon(hicar_rmse, expected):
    assert MODULE.material_wind_change(hicar_rmse, 2.0)["classification"] == expected


def test_wind_decision_rejects_supplied_delta_inconsistency():
    rows = make_wind_decision_rows([(-0.2, 0.0)] * 4)
    rows[0]["rmse_delta_hicar_minus_rea_l"] = -0.3

    with pytest.raises(ValueError, match="supplied RMSE delta is inconsistent"):
        MODULE.wind_decision_readout(rows)


def test_wind_decision_fails_closed_on_population_or_metric_loss():
    too_small = make_wind_decision_rows([(-0.2, 0.0)] * 4, site_count=20)
    for row in too_small:
        if (
            row["season"] == "DJF"
            and row["station_key"] == "S019:1"
            and row["metric"] == "wind_speed_10m_m_s"
        ):
            row["pair_count"] = 23
    with pytest.raises(ValueError, match="fixed four-event/two-metric cohort has 19"):
        MODULE.wind_decision_readout(too_small)

    rows = make_wind_decision_rows([(-0.2, 0.0)] * 4)
    insufficient_ridge = [dict(row) for row in rows]
    for row in insufficient_ridge:
        if row["station_key"] == "S009:1":
            row["terrain_class"] = "terrain_neutral_relative_pm_150m"
    with pytest.raises(ValueError, match="safeguard has 9 paired stations"):
        MODULE.wind_decision_readout(insufficient_ridge)

    missing_schema = [dict(row) for row in rows]
    del missing_schema[0]["rea_l_rmse"]
    with pytest.raises(ValueError, match="lacks required fields: rea_l_rmse"):
        MODULE.wind_decision_readout(missing_schema)


def test_lead_hours_are_evaluation_relative_and_preserve_physical_lead(tmp_path):
    reports = {}
    for season in MODULE.SEASONS:
        path = make_report(tmp_path / f"{season}.json", season)
        reports[season] = (path, MODULE.load_report(path))

    tables, exclusions = MODULE.lead_hour_tables(reports, {"temperature_2m_height_adjusted_k"})

    assert exclusions == {}
    assert {
        (row["lead_hour"], row["physical_lead_hour"])
        for row in tables["DJF"]
        if row["stratum"] == "all_sites"
    } == {(lead - 24, lead) for lead in range(25, 49)}


def source_reports(tmp_path):
    reports = {}
    for season in MODULE.SEASONS:
        path = make_report(tmp_path / f"source-{season}.json", season)
        reports[season] = (path, MODULE.load_report(path))
    return reports


def test_wind_source_contract_proves_times_leads_and_reconciles_counts(tmp_path):
    evidence = MODULE.validate_wind_source_reports(source_reports(tmp_path))

    assert set(evidence) == set(MODULE.SEASONS)
    assert evidence["DJF"]["matched_endpoint_count"] == 25
    assert evidence["DJF"]["physical_leads"] == list(range(25, 49))
    assert evidence["DJF"]["normalized_leads"] == list(range(1, 25))
    assert (
        evidence["DJF"]["common_triplet_reconciliation"]["wind_vector"][
            "accepted_common_triplet_count"
        ]
        == 67 * 24
    )


def test_wind_source_and_decision_contract_accepts_seven_day_events(tmp_path):
    reports = source_reports(tmp_path)
    pair_count = 168
    for path, report in reports.values():
        evaluation_start = datetime.fromisoformat(
            report["sampling"]["evaluation_start_inclusive"]
        )
        report["sampling"]["evaluation_end_inclusive"] = (
            evaluation_start + timedelta(hours=pair_count)
        ).isoformat()
        report["matched_model_times"] = [
            (evaluation_start + timedelta(hours=index)).isoformat()
            for index in range(pair_count + 1)
        ]
        template = next(iter(report["lead_time_metrics"].values()))
        report["lead_time_metrics"] = {
            str(lead): template for lead in range(25, 25 + pair_count)
        }
        for values in report["site_metrics"].values():
            for source in MODULE.SOURCES:
                for metric_name in MODULE.WIND_METRICS:
                    values[source][metric_name]["count"] = pair_count
        refresh_wind_report_totals(report)
        path.write_text(json.dumps(report))

    evidence = MODULE.validate_wind_source_reports(
        {season: (path, MODULE.load_report(path)) for season, (path, _) in reports.items()}
    )
    decision = MODULE.wind_decision_readout(
        make_wind_decision_rows([(-0.2, 0.0)] * 4, pair_count=pair_count),
        required_pair_count=pair_count,
    )

    assert {item["common_ending_hour_pair_count"] for item in evidence.values()} == {
        pair_count
    }
    assert all(item["matched_endpoint_count"] == 169 for item in evidence.values())
    assert evidence["DJF"]["physical_leads"] == list(range(25, 193))
    assert decision["rule"]["required_event_counts"][
        "common_ending_hour_pairs_per_station_event_metric"
    ] == pair_count


@pytest.mark.parametrize("broken_field", ["matched_model_times", "lead_time_metrics"])
def test_wind_source_contract_rejects_incomplete_times_or_leads(tmp_path, broken_field):
    reports = source_reports(tmp_path)
    path, report = reports["DJF"]
    if broken_field == "matched_model_times":
        report[broken_field].pop()
        message = "requires exactly 25 matched endpoints"
    else:
        del report[broken_field]["48"]
        message = "physical leads must be exactly 25..48"
    path.write_text(json.dumps(report))
    reports["DJF"] = (path, MODULE.load_report(path))

    with pytest.raises(ValueError, match=message):
        MODULE.validate_wind_source_reports(reports)


def test_wind_source_contract_rejects_pair_and_accounting_inconsistency(tmp_path):
    reports = source_reports(tmp_path)
    path, report = reports["DJF"]
    report["site_metrics"]["S000:1"]["hicar"]["wind_vector"]["count"] = 23
    path.write_text(json.dumps(report))
    reports["DJF"] = (path, MODULE.load_report(path))
    with pytest.raises(ValueError, match="HICAR and REA-L common-pair counts differ"):
        MODULE.validate_wind_source_reports(reports)

    reports = source_reports(tmp_path)
    path, report = reports["DJF"]
    report["common_triplet_accounting"]["metrics"]["wind_vector"][
        "accepted_common_triplet_count"
    ] += 1
    path.write_text(json.dumps(report))
    reports["DJF"] = (path, MODULE.load_report(path))
    with pytest.raises(ValueError, match="common-triplet accounting does not reconcile"):
        MODULE.validate_wind_source_reports(reports)

    reports = source_reports(tmp_path)
    path, report = reports["DJF"]
    report["metrics"]["hicar"]["all_sites"]["wind_vector"]["count"] -= 1
    path.write_text(json.dumps(report))
    reports["DJF"] = (path, MODULE.load_report(path))
    with pytest.raises(ValueError, match="aggregate counts do not equal accepted"):
        MODULE.validate_wind_source_reports(reports)


def test_lead_hour_normalization_rejects_non_hourly_evaluation_offset(tmp_path):
    reports = {}
    for season in MODULE.SEASONS:
        path = make_report(tmp_path / f"{season}.json", season)
        report = json.loads(path.read_text())
        report["sampling"]["evaluation_start_inclusive"] = "2020-01-02T00:30:00+00:00"
        path.write_text(json.dumps(report))
        reports[season] = (path, MODULE.load_report(path))

    with pytest.raises(ValueError, match="not a nonnegative whole-hour lead"):
        MODULE.lead_hour_tables(reports, None)


def test_national_four_season_intersection_is_distinct_from_per_season(tmp_path):
    national = {
        season: make_report(tmp_path / f"national-{season}.json", season)
        for season in MODULE.SEASONS
    }
    mam = json.loads(national["MAM"].read_text())
    missing_key = "S066:67"
    mam["station_mapping"]["sites"] = [
        site for site in mam["station_mapping"]["sites"] if site["key"] != missing_key
    ]
    del mam["site_metrics"][missing_key]
    refresh_wind_report_totals(mam)
    national["MAM"].write_text(json.dumps(mam))

    bridge = [
        make_report(tmp_path / f"bridge-{season}.json", season, 65) for season in MODULE.SEASONS
    ]
    output_csv = tmp_path / "station-season.csv"
    output_json = tmp_path / "summary.json"
    argv = []
    for season, path in national.items():
        argv.extend(["--report", f"{season}={path}"])
    for path in bridge:
        argv.extend(["--common-65-report", str(path)])
    argv.extend(["--output-csv", str(output_csv), "--output-summary", str(output_json)])

    assert MODULE.main(argv) == 0
    summary = json.loads(output_json.read_text())
    groups = {
        (item["subset"], item["season"], item["stratum"], item["metric"]): item
        for item in summary["equal_station_summaries"]
    }
    metric_name = "temperature_2m_height_adjusted_k"

    assert summary["coverage"]["station_key_union_count"] == 67
    assert summary["coverage"]["station_key_four_season_intersection_count"] == 66
    assert summary["national_four_season_intersection"]["site_count"] == 66
    assert missing_key not in summary["national_four_season_intersection"]["site_keys"]
    assert groups[("national", "DJF", "all_sites", metric_name)]["paired_station_count"] == 67
    assert (
        groups[("national_four_season_intersection", "DJF", "all_sites", metric_name)][
            "paired_station_count"
        ]
        == 66
    )
    assert groups[("common_65", "DJF", "all_sites", metric_name)]["paired_station_count"] == 65


def test_common_definition_must_have_exactly_65_keys(tmp_path):
    paths = [make_report(tmp_path / f"bridge-{index}.json", "DJF", 64) for index in range(4)]
    with pytest.raises(ValueError, match="64 keys, expected 65"):
        MODULE.load_common_keys(paths, None)


def test_mean_and_bias_require_matching_observation_aggregate():
    hicar = metric(
        1.0,
        model_mean=281.0,
        observation_mean=280.0,
        bias=1.0,
    )
    rea_l = metric(
        1.5,
        model_mean=281.5,
        observation_mean=280.1,
        bias=1.4,
    )

    comparison, reason = MODULE.comparison_row(
        hicar,
        rea_l,
        "temperature_2m_height_adjusted_k",
    )

    assert reason is None
    assert comparison is not None
    assert comparison["hicar_rmse"] == pytest.approx(1.0)
    assert comparison["rea_l_rmse"] == pytest.approx(1.5)
    assert comparison["hicar_observation_mean"] is None
    assert comparison["rea_l_observation_mean"] is None
    assert comparison["observation_mean"] is None
    assert comparison["hicar_bias"] is None
    assert comparison["rea_l_bias"] is None


def test_station_diagnostics_require_twenty_temporal_pairs():
    hicar = metric(1.0, count=19, model_mean=281.0, observation_mean=280.0, bias=1.0)
    rea_l = metric(1.5, count=19, model_mean=281.5, observation_mean=280.0, bias=1.5)

    comparison, reason = MODULE.comparison_row(hicar, rea_l, "temperature_2m_height_adjusted_k")

    assert reason is None
    assert comparison["hicar_mae"] == 1.0
    assert comparison["rea_l_mae"] == 1.5
    assert comparison["hicar_standard_deviation_ratio"] is None
    assert comparison["rea_l_standard_deviation_ratio"] is None
    assert comparison["hicar_correlation"] is None
    assert comparison["rea_l_correlation"] is None


def test_rejects_nonempty_evaluator_issues(tmp_path):
    path = make_report(tmp_path / "report.json", "DJF")
    value = json.loads(path.read_text())
    value["issues"] = ["missing observation"]
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="reports issues"):
        MODULE.load_report(path)
