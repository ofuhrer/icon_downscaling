import csv
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
            }
        )
    return result


def make_report(path, season, site_count=67, common_count=65, mismatch=False):
    sites = []
    site_metrics = {}
    for index in range(site_count):
        key = f"S{index:03d}:{index + 1}"
        elevation = 3100.0 if index == 0 else 1200.0 if index == 1 else 450.0
        relative = 200.0 if index in (0, 1) else -200.0 if index == 2 else 0.0
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
            },
        }
    lead = {
        "0": {
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
                }
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
                }
            },
        }
    }
    report = {
        "schema_version": 1,
        "event_name": f"event-{season}",
        "matched_model_times": ["2020-01-01T00:00:00+00:00"],
        "station_mapping": {"sites": sites},
        "site_metrics": site_metrics,
        "lead_time_metrics": lead,
        "issues": [],
    }
    path.write_text(json.dumps(report))
    return path


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
    assert summary["common_65"]["site_count"] == 65
    assert summary["common_65"]["site_keys"][0] == "S000:1"
    assert len(rows) == 4 * 67 * 2 - 1
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
    assert national_temperature["equal_station_mean_hicar_bias"] == pytest.approx(1.33)
    assert national_temperature["equal_station_mean_rea_l_bias"] == pytest.approx(1.5)
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
    assert national_intersection_temperature["paired_station_count"] == 67
    lead_temperature = next(
        item
        for item in summary["lead_hour_tables"]["DJF"]
        if item["metric"] == "temperature_2m_height_adjusted_k"
    )
    assert lead_temperature["hicar_bias"] == pytest.approx(1.0)
    assert lead_temperature["rea_l_bias"] == pytest.approx(1.5)
    assert lead_temperature["hicar_model_mean"] == pytest.approx(281.0)
    assert lead_temperature["rea_l_model_mean"] == pytest.approx(281.5)
    assert lead_temperature["hicar_observation_mean"] == pytest.approx(280.0)
    assert lead_temperature["rea_l_observation_mean"] == pytest.approx(280.0)
    assert lead_temperature["observation_mean"] == pytest.approx(280.0)
    assert len(summary["lead_hour_tables"]["JJA"]) == 2
    assert len(summary["selected_site_listings"]["station_elevation_ge_3000m"]) == 1
    assert len(summary["selected_site_listings"]["terrain_ridge_relative_gt_150m"]) == 2
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
    assert summary["network_pooled_hicar_rmse"] == pytest.approx(7.0**0.5)
    assert summary["network_pooled_rea_l_rmse"] == pytest.approx(13.0**0.5)
    assert summary["network_pooled_rmse_delta_hicar_minus_rea_l"] == pytest.approx(
        7.0**0.5 - 13.0**0.5
    )


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


def test_rejects_nonempty_evaluator_issues(tmp_path):
    path = make_report(tmp_path / "report.json", "DJF")
    value = json.loads(path.read_text())
    value["issues"] = ["missing observation"]
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="reports issues"):
        MODULE.load_report(path)
