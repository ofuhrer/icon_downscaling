import csv
import importlib.util
import json
from pathlib import Path
import sys

import nbformat
from nbclient import NotebookClient
import pytest


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "analysis" / "hicar_readiness_20m"
SPEC = importlib.util.spec_from_file_location("hicar_report", REPORT_DIR / "build_artifact.py")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n")


def fixture(tmp_path):
    results = tmp_path / "results"
    results.mkdir()
    campaign = {
        "coordinator_commit": "a" * 40, "hicar_commit": "b" * 40,
        "preprocessing": {"implementation": "hicarprep", "legacy_path_used": False,
            "forcing_validator_passed": True, "boundary_validator_passed": True,
            "expected_hourly_records": 100, "forcing_records_validated": 100,
            "boundary_records_validated": 100},
        "domain": {"station_sites_requested": 3, "topography_relaxation_width_km": 30,
            "topography_relaxation_target": "REA-L-CH1"},
        "pilot": {"continuous_completed": True, "segmented_completed": True,
            "same_executable_and_inputs": True, "integrated_hours_each": 2},
        "seasonal_campaign": {"events": [
            {"season": season, "status": "complete", "integrated_hours": 24,
             "segment_count": 2, "preemptible": True, "restart_linked": True,
             "validation_passed": True} for season in MODULE.SEASONS]},
    }
    geometry = {"status": "PASS", "static_sha256": "c" * 64,
        "configuration": {"height_lowest_level_m": 20},
        "acceptance": {"minimum_interface_layer_thickness_m": 12},
        "minimum_interface_layer_thickness": {"value_m": 15.956}}
    restart = {"bitwise_equal_model_core_state": True, "differing_variable_count": 0,
        "schema": {"compared_variable_count": 196}}

    summaries, leads, stations = [], {}, []
    for season_index, season in enumerate(MODULE.SEASONS):
        leads[season] = []
        for metric_index, metric in enumerate(MODULE.METRICS):
            hicar = 1 + metric_index / 2 + season_index / 10
            rea_l = hicar + 0.2
            for subset, station_count in (("national", 12), ("national_four_season_intersection", 10)):
                summary = {"subset": subset, "season": season,
                    "stratum": "all_sites", "metric": metric,
                    "paired_station_count": station_count,
                    "equal_station_mean_hicar_rmse": hicar,
                    "equal_station_mean_rea_l_rmse": rea_l,
                    "network_pooled_hicar_rmse": hicar + 0.05,
                    "network_pooled_rea_l_rmse": rea_l + 0.05}
                if metric in MODULE.SCALAR_STATS:
                    summary.update({"equal_station_mean_hicar_bias": -0.1,
                        "equal_station_mean_rea_l_bias": 0.1,
                        "equal_station_mean_hicar_model_mean": 10 + metric_index,
                        "equal_station_mean_rea_l_model_mean": 10.2 + metric_index,
                        "equal_station_mean_observation": 10.1 + metric_index})
                summaries.append(summary)
            first_hour = 1 if metric == "precipitation_interval_kg_m2" else 0
            for hour in range(first_hour, 25):
                lead = {"lead_hour": hour, "metric": metric, "pair_count": 96,
                    "hicar_rmse": hicar + hour / 50, "rea_l_rmse": rea_l + hour / 100}
                if metric in MODULE.SCALAR_STATS:
                    lead.update({"hicar_bias": -0.1, "rea_l_bias": 0.1,
                        "hicar_model_mean": 10 + metric_index,
                        "rea_l_model_mean": 10.2 + metric_index,
                        "observation_mean": 10.1 + metric_index})
                leads[season].append(lead)
        for metric_index, metric in enumerate(MODULE.METRICS):
            for station_index in range(12):
                hicar = 1.5 + station_index / 20 + season_index / 10 + metric_index / 10
                rea_l = 1.8 + station_index / 30 + metric_index / 10
                group = station_index % 3
                stations.append({"season": season, "station_key": f"S{station_index}:1",
                    "station_elevation_m": 400 + 200 * station_index,
                    "hicar_elevation_m": 420 + 210 * station_index,
                    "nearest_cell_distance_km": 0.05 + station_index / 100,
                    "elevation_class": ("low", "middle", "high")[group],
                    "terrain_relative_elevation_m": -200 + 200 * group,
                    "terrain_class": ("valley", "neutral", "ridge")[group],
                    "metric": metric, "pair_count": 8, "hicar_rmse": hicar,
                    "rea_l_rmse": rea_l})
    national = {"method": {"station_grain": "one station-season-metric row",
        "pairing_rule": "retain equal positive model pair counts",
        "aggregation": "arithmetic means of station RMSEs",
        "lead_hour_aggregation": "all-sites pooled-pair RMSE"},
        "coverage": {"events": {season: {} for season in MODULE.SEASONS},
            "station_key_union_count": 166, "station_key_four_season_intersection_count": 12},
        "station_season_row_count": len(stations), "equal_station_summaries": summaries,
        "lead_hour_tables": leads}
    station_path = results / "station_season_metrics.csv"
    with station_path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=stations[0])
        writer.writeheader()
        writer.writerows(stations)

    assessment = {"readiness_status": "ready", "technical_summary": "Fixture evidence is complete.",
        "findings": {name: {"heading": name.replace("_", " ").title(),
            "body": f"Reviewed {name.replace('_', ' ')} result."} for name in MODULE.FINDINGS},
        "limitations": ["Four cases are not a climatology."],
        "recommended_next_steps": ["Monitor longer samples."],
        "further_questions": ["Does skill persist?"]}
    files = {
        "campaign_evidence": "results/campaign.json", "geometry_validation": "results/geometry.json",
        "restart_comparison": "results/restart.json", "national_summary": "results/national.json",
        "station_season_csv": "results/station_season_metrics.csv",
        "reviewed_assessment": "results/assessment.json", "footprint_reports": {},
    }
    for name, value in (("campaign", campaign), ("geometry", geometry), ("restart", restart),
                        ("national", national), ("assessment", assessment)):
        dump(results / f"{name}.json", value)
    for season_index, season in enumerate(MODULE.SEASONS):
        footprints = {radius: {"nearest_cell": {"pair_count": 8, "vector_rmse_m_s": 3 + season_index / 10},
            "footprint_mean_vector": {"pair_count": 8, "vector_rmse_m_s": 2.8 + season_index / 10 + float(radius) / 10},
            "geometry": {"coverage_fraction": 1, "actual_cell_count": 13 if radius == "0.4" else 81}}
            for radius in ("0.4", "1")}
        dump(results / f"footprint_{season}.json", {"data_quality": {"model_times_exactly_match_evaluator": True},
            "sites": [{"site_key": "S2:1", "station_elevation_m": 2800,
                "terrain_relative_elevation_m": 200, "footprints": footprints}]})
        files["footprint_reports"][season] = f"results/footprint_{season}.json"
    inputs = tmp_path / "inputs.json"
    dump(inputs, {"snapshot_generated_at": "2026-08-09T02:00:00Z", "files": files})
    return inputs


def test_missing_results_are_reported_together(tmp_path):
    files = {name: f"results/{name}" for name in (
        "campaign_evidence", "geometry_validation", "restart_comparison",
        "national_summary", "station_season_csv", "reviewed_assessment")}
    files["footprint_reports"] = {season: f"results/{season}" for season in MODULE.SEASONS}
    inputs = tmp_path / "inputs.json"
    dump(inputs, {"snapshot_generated_at": "2026-08-09T02:00:00Z", "files": files})
    with pytest.raises(ValueError, match="required real result files are absent") as error:
        MODULE.load_evidence(inputs)
    assert str(error.value).count("\n- ") == 10


def test_artifact_is_deterministic_and_canonical(tmp_path):
    evidence = MODULE.load_evidence(fixture(tmp_path))
    first, second = MODULE.build_artifact(evidence), MODULE.build_artifact(evidence)
    assert first == second
    assert first["surface"] == first["manifest"]["surface"] == "report"
    assert first["snapshot"]["status"] == "ready"
    assert {name: len(rows) for name, rows in first["snapshot"]["datasets"].items()} == {
        "seasonal_metrics": 20, "seasonal_population_sensitivity": 40,
        "lead_metrics": 496, "station_wind": 96,
        "elevation_counts": 24, "footprint_wind": 8}
    seasonal = first["snapshot"]["datasets"]["seasonal_metrics"]
    assert {row["metric"] for row in seasonal} == set(MODULE.METRICS)
    assert all(row["paired_station_count"] == 12 for row in seasonal)
    assert all(row["network_pooled_hicar_rmse"] > row["hicar_rmse"] for row in seasonal)
    assert all(row["hicar_bias"] is not None for row in seasonal
        if row["metric"] in MODULE.SCALAR_STATS)
    sensitivity = first["snapshot"]["datasets"]["seasonal_population_sensitivity"]
    assert {row["population"] for row in sensitivity} == {
        "All season-available stations", "Four-season station intersection"}
    assert {row["paired_station_count"] for row in sensitivity} == {10, 12}
    station = first["snapshot"]["datasets"]["station_wind"][0]
    assert station["hicar_minus_station_elevation_m"] == pytest.approx(
        station["hicar_elevation_m"] - station["station_elevation_m"]
    )
    assert all(not item["path"].startswith("/") for item in first["sources"])

    national_path = tmp_path / "results" / "national.json"
    national = json.loads(national_path.read_text())
    incomplete = json.loads(json.dumps(national))
    incomplete["lead_hour_tables"]["DJF"] = [
        row for row in incomplete["lead_hour_tables"]["DJF"]
        if not (row["metric"] == "temperature_2m_height_adjusted_k" and row["lead_hour"] == 0)
    ]
    dump(national_path, incomplete)
    with pytest.raises(ValueError, match="lead hours must be exactly"):
        MODULE.derive_datasets(MODULE.load_evidence(tmp_path / "inputs.json"))

    national["equal_station_summaries"] = [
        row for row in national["equal_station_summaries"] if row["metric"] == "wind_vector"
    ]
    national["lead_hour_tables"] = {
        season: [row for row in rows if row["metric"] == "wind_vector"]
        for season, rows in national["lead_hour_tables"].items()
    }
    dump(national_path, national)
    with pytest.raises(ValueError, match="all five headline metrics"):
        MODULE.derive_datasets(MODULE.load_evidence(tmp_path / "inputs.json"))


def test_notebook_executes_top_to_bottom(tmp_path, monkeypatch):
    inputs = fixture(tmp_path)
    notebook = nbformat.read(REPORT_DIR / "readiness_analysis.ipynb", as_version=4)
    monkeypatch.setenv("HICAR_READINESS_REPORT_DIR", str(REPORT_DIR))
    monkeypatch.setenv("HICAR_READINESS_INPUTS", str(inputs))
    executed = NotebookClient(notebook, timeout=120, kernel_name="python3",
        resources={"metadata": {"path": str(ROOT)}}).execute()
    assert all(cell.execution_count for cell in executed.cells if cell.cell_type == "code")
