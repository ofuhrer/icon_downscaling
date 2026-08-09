#!/usr/bin/env python3
"""Build the HICAR readiness report artifact from completed local results."""

from __future__ import annotations

import argparse
import csv
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import sqlite3
from tempfile import NamedTemporaryFile


SEASONS = ("DJF", "MAM", "JJA", "SON")
METRICS = {
    "temperature_2m_height_adjusted_k": ("2 m temperature", "K"),
    "relative_humidity_2m_percent": ("2 m relative humidity", "%"),
    "surface_pressure_height_adjusted_pa": ("Surface pressure, elevation adjusted", "Pa"),
    "precipitation_interval_kg_m2": ("Interval precipitation", "kg m^-2"),
    "wind_speed_10m_m_s": ("10 m wind speed", "m s^-1"),
    "wind_vector": ("10 m wind vector", "m s^-1"),
}
SCALAR_STATS = {
    "temperature_2m_height_adjusted_k",
    "relative_humidity_2m_percent",
    "surface_pressure_height_adjusted_pa",
    "precipitation_interval_kg_m2",
}
WIND_METRICS = {"wind_speed_10m_m_s", "wind_vector"}
RIDGE_LEAD_STRATA = {
    "terrain_ridge_relative_gt_150m": "Terrain ridge (>150 m relative)",
    "station_elevation_2000_3000m": "Station elevation 2000–3000 m",
    "station_elevation_ge_3000m": "Station elevation >=3000 m",
}
FINDINGS = ("seasonal_skill", "lead_time", "elevation_wind",
            "footprint_sensitivity", "inputs_and_grid", "restart")
TITLE = "HICAR 20 m national four-season readiness assessment"
SEASON_ORDER = "CASE season WHEN 'DJF' THEN 1 WHEN 'MAM' THEN 2 WHEN 'JJA' THEN 3 ELSE 4 END"
QUERIES = {
    "seasonal_metrics": f"SELECT * FROM seasonal ORDER BY {SEASON_ORDER}, metric_order",
    "seasonal_population_sensitivity": f"SELECT * FROM seasonal_sensitivity ORDER BY {SEASON_ORDER}, metric_order, population_order",
    "lead_metrics": f"SELECT * FROM lead ORDER BY {SEASON_ORDER}, metric_order, lead_hour",
    "ridge_lead_metrics": f"SELECT * FROM ridge_lead ORDER BY {SEASON_ORDER}, stratum, lead_hour",
    "station_wind": f"SELECT * FROM station ORDER BY {SEASON_ORDER}, metric_order, station_elevation_m, station_key",
    "elevation_counts": f"SELECT * FROM elevation ORDER BY {SEASON_ORDER}, metric_order, elevation_class, terrain_class",
    "footprint_wind": f"SELECT * FROM footprint ORDER BY {SEASON_ORDER}, site_key, radius_km",
}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    value = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def load_evidence(inputs_path):
    """Load known report inputs and reject only missing/ambiguous evidence."""
    inputs_path = Path(inputs_path).resolve()
    if not inputs_path.is_file():
        raise ValueError(f"input manifest does not exist: {inputs_path}")
    spec = read_json(inputs_path)
    root, files = inputs_path.parent, spec["files"]

    def resolve(raw):
        relative = Path(raw)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"report input paths must be relative to {root}: {raw}")
        return relative.as_posix(), (root / relative).resolve()

    relative, paths = {}, {}
    for name in (
        "campaign_evidence", "geometry_validation", "restart_comparison",
        "national_summary", "station_season_csv", "reviewed_assessment",
    ):
        relative[name], paths[name] = resolve(files[name])
    if set(files["footprint_reports"]) != set(SEASONS):
        raise ValueError("footprint reports must cover DJF, MAM, JJA, and SON")
    relative["footprint_reports"], paths["footprint_reports"] = {}, {}
    for season in SEASONS:
        relative["footprint_reports"][season], paths["footprint_reports"][season] = resolve(
            files["footprint_reports"][season]
        )
    required = [path for name, path in paths.items() if name != "footprint_reports"]
    required += list(paths["footprint_reports"].values())
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ValueError("required real result files are absent:\n- " + "\n- ".join(missing))

    evidence = {
        name: read_json(path) for name, path in paths.items()
        if name not in {"station_season_csv", "footprint_reports"}
    }
    evidence["footprint_reports"] = {
        season: read_json(paths["footprint_reports"][season]) for season in SEASONS
    }
    with paths["station_season_csv"].open(encoding="utf-8", newline="") as stream:
        evidence["station_rows"] = list(csv.DictReader(stream))
    national, campaign = evidence["national_summary"], evidence["campaign_evidence"]
    if set(national["coverage"]["events"]) != set(SEASONS) or set(national["lead_hour_tables"]) != set(SEASONS):
        raise ValueError("national station evidence must cover all four seasons")
    if len(evidence["station_rows"]) != int(national["station_season_row_count"]):
        raise ValueError("station CSV row count differs from the national summary")
    if set(event["season"] for event in campaign["seasonal_campaign"]["events"]) != set(SEASONS):
        raise ValueError("campaign evidence must cover all four seasons")
    for season, report in evidence["footprint_reports"].items():
        if not report["data_quality"]["model_times_exactly_match_evaluator"] or not report["sites"]:
            raise ValueError(f"{season} footprint evidence is incomplete or time-mismatched")
    assessment = evidence["reviewed_assessment"]
    if set(assessment["findings"]) != set(FINDINGS):
        raise ValueError("reviewed assessment must contain the six report findings")
    if any(not assessment.get(name) for name in ("technical_summary", "limitations", "recommended_next_steps", "further_questions")):
        raise ValueError("reviewed assessment is missing summary, limitations, next steps, or questions")
    return {"generated_at": spec["snapshot_generated_at"], "relative": relative,
            "paths": paths, **evidence}


def query_rows(table, rows, query):
    """Run the exact SQLite selection recorded in artifact provenance."""
    columns = list(rows[0])
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute(f"CREATE TABLE {table} ({', '.join(f'[{name}]' for name in columns)})")
        connection.executemany(
            f"INSERT INTO {table} VALUES ({','.join('?' for _ in columns)})",
            [[row.get(name) for name in columns] for row in rows],
        )
        cursor = connection.execute(query)
        fields = [item[0] for item in cursor.description]
        return [dict(zip(fields, values, strict=True)) for values in cursor.fetchall()]
    finally:
        connection.close()


def number(row, label, *names):
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return float(value)
    raise ValueError(f"{label} is missing required postprocessor field: {' or '.join(names)}")


def normalized_difference(hicar, rea_l):
    scale = hicar + rea_l
    return (hicar - rea_l) / scale if scale else 0.0


def scalar_stats(row, label, prefix):
    """Read bias/model/observation means from enhanced postprocessor rows."""
    if prefix == "equal_station_mean_":
        return {
            "hicar_bias": number(row, label, prefix + "hicar_bias"),
            "rea_l_bias": number(row, label, prefix + "rea_l_bias"),
            "hicar_model_mean": number(row, label, prefix + "hicar_model_mean"),
            "rea_l_model_mean": number(row, label, prefix + "rea_l_model_mean"),
            "hicar_observation_mean": number(row, label, prefix + "hicar_observation_mean", prefix + "observation_mean", prefix + "observation"),
            "rea_l_observation_mean": number(row, label, prefix + "rea_l_observation_mean", prefix + "observation_mean", prefix + "observation"),
        }
    return {
        "hicar_bias": number(row, label, "hicar_bias"),
        "rea_l_bias": number(row, label, "rea_l_bias"),
        "hicar_model_mean": number(row, label, "hicar_model_mean"),
        "rea_l_model_mean": number(row, label, "rea_l_model_mean"),
        "hicar_observation_mean": number(row, label, "hicar_observation_mean", "observation_mean"),
        "rea_l_observation_mean": number(row, label, "rea_l_observation_mean", "observation_mean"),
    }


def derive_datasets(evidence):
    national = evidence["national_summary"]
    seasonal = []
    seasonal_sensitivity = []
    for row in national["equal_station_summaries"]:
        metric = row["metric"]
        if row["subset"] not in {"national", "national_four_season_intersection"} or row["stratum"] != "all_sites" or metric not in METRICS:
            continue
        mean_station_hicar = number(
            row,
            f"seasonal {row['season']}/{metric}",
            "mean_station_hicar_rmse",
            "equal_station_mean_hicar_rmse",
        )
        mean_station_rea_l = number(
            row,
            f"seasonal {row['season']}/{metric}",
            "mean_station_rea_l_rmse",
            "equal_station_mean_rea_l_rmse",
        )
        hicar = number(
            row,
            f"seasonal {row['season']}/{metric}",
            "equal_station_network_hicar_rmse",
        )
        rea_l = number(
            row,
            f"seasonal {row['season']}/{metric}",
            "equal_station_network_rea_l_rmse",
        )
        pooled_hicar = float(row["network_pooled_hicar_rmse"])
        pooled_rea_l = float(row["network_pooled_rea_l_rmse"])
        population = ("All season-available stations" if row["subset"] == "national"
                      else "Four-season metric-eligible station intersection")
        item = {"season": row["season"], "metric": metric, "metric_label": METRICS[metric][0],
                "unit": METRICS[metric][1], "metric_order": list(METRICS).index(metric),
                "population": population, "population_order": 0 if row["subset"] == "national" else 1,
                "paired_station_count": int(row["paired_station_count"]),
                "primary_rmse_estimand": "equal_station_network_rmse",
                "hicar_rmse": hicar, "rea_l_rmse": rea_l,
                "rmse_delta": hicar - rea_l,
                "normalized_rmse_difference": normalized_difference(hicar, rea_l),
                "mean_station_hicar_rmse": mean_station_hicar,
                "mean_station_rea_l_rmse": mean_station_rea_l,
                "mean_station_rmse_delta": mean_station_hicar - mean_station_rea_l,
                "equal_station_network_hicar_rmse": hicar,
                "equal_station_network_rea_l_rmse": rea_l,
                "equal_station_network_rmse_delta": hicar - rea_l,
                "network_pooled_hicar_rmse": pooled_hicar,
                "network_pooled_rea_l_rmse": pooled_rea_l,
                "network_pooled_rmse_delta": pooled_hicar - pooled_rea_l,
                "network_pooled_normalized_rmse_difference": normalized_difference(pooled_hicar, pooled_rea_l)}
        if metric in SCALAR_STATS:
            item.update(scalar_stats(row, f"seasonal {row['season']}/{metric}", "equal_station_mean_"))
        seasonal_sensitivity.append(item)
        if row["subset"] == "national":
            seasonal.append(item)
    if {(row["season"], row["metric"]) for row in seasonal} != {
        (season, metric) for season in SEASONS for metric in METRICS
    }:
        raise ValueError("national seasonal summaries must contain all six headline metrics in all four seasons")
    if {(row["season"], row["metric"], row["population_order"]) for row in seasonal_sensitivity} != {
        (season, metric, population) for season in SEASONS for metric in METRICS for population in (0, 1)
    }:
        raise ValueError("seasonal population sensitivity requires national and four-season-intersection summaries")

    lead = []
    for season in SEASONS:
        for row in national["lead_hour_tables"][season]:
            metric = row["metric"]
            if metric not in METRICS or row.get("stratum", "all_sites") != "all_sites":
                continue
            hicar, rea_l = float(row["hicar_rmse"]), float(row["rea_l_rmse"])
            item = {"season": season, "lead_hour": int(row["lead_hour"]), "metric": metric,
                    "metric_label": METRICS[metric][0], "unit": METRICS[metric][1],
                    "metric_order": list(METRICS).index(metric), "pair_count": int(row["pair_count"]),
                    "hicar_rmse": hicar, "rea_l_rmse": rea_l, "rmse_delta": hicar - rea_l,
                    "normalized_rmse_difference": normalized_difference(hicar, rea_l)}
            if metric in SCALAR_STATS:
                item.update(scalar_stats(row, f"lead {season}/{item['lead_hour']}/{metric}", ""))
            lead.append(item)
    for season in SEASONS:
        for metric in METRICS:
            hours = [row["lead_hour"] for row in lead if row["season"] == season and row["metric"] == metric]
            expected = set(range(1, 25)) if metric == "precipitation_interval_kg_m2" else set(range(25))
            if len(hours) != len(expected) or set(hours) != expected:
                raise ValueError(f"{season}/{metric} lead hours must be exactly {sorted(expected)}; got {sorted(hours)}")

    ridge_lead = []
    for season in SEASONS:
        for row in national["lead_hour_tables"][season]:
            stratum = row.get("stratum", "all_sites")
            if row["metric"] != "wind_vector" or stratum not in RIDGE_LEAD_STRATA:
                continue
            hicar, rea_l = float(row["hicar_rmse"]), float(row["rea_l_rmse"])
            hour = int(row["lead_hour"])
            ridge_lead.append({
                "season": season,
                "lead_hour": hour,
                "segment": "first" if hour <= 12 else "restarted",
                "turnover_relative_hour": hour - 12,
                "stratum": stratum,
                "stratum_label": RIDGE_LEAD_STRATA[stratum],
                "pair_count": int(row["pair_count"]),
                "hicar_rmse": hicar,
                "rea_l_rmse": rea_l,
                "rmse_delta": hicar - rea_l,
                "normalized_rmse_difference": normalized_difference(hicar, rea_l),
            })
    missing_ridge = {
        (season, stratum) for season in SEASONS for stratum in RIDGE_LEAD_STRATA
    } - {(row["season"], row["stratum"]) for row in ridge_lead}
    if missing_ridge:
        raise ValueError(f"ridge/high-elevation wind-vector lead evidence is missing: {sorted(missing_ridge)}")

    station = []
    for row in evidence["station_rows"]:
        metric = row["metric"]
        if metric not in WIND_METRICS:
            continue
        hicar, rea_l = float(row["hicar_rmse"]), float(row["rea_l_rmse"])
        station.append({"season": row["season"], "metric": metric, "metric_label": METRICS[metric][0],
            "unit": METRICS[metric][1], "metric_order": list(METRICS).index(metric),
            "station_key": row["station_key"], "station_elevation_m": float(row["station_elevation_m"]),
            "hicar_elevation_m": float(row["hicar_elevation_m"]),
            "hicar_minus_station_elevation_m": float(row["hicar_elevation_m"]) - float(row["station_elevation_m"]),
            "nearest_cell_distance_km": float(row["nearest_cell_distance_km"]),
            "elevation_class": row["elevation_class"], "terrain_class": row["terrain_class"],
            "terrain_relative_elevation_m": float(row["terrain_relative_elevation_m"]),
            "pair_count": int(float(row["pair_count"])), "hicar_rmse": hicar, "rea_l_rmse": rea_l,
            "rmse_delta": hicar - rea_l, "normalized_rmse_difference": normalized_difference(hicar, rea_l)})
    if any(sum(row["season"] == season and row["metric"] == metric for row in station) < 12
           for season in SEASONS for metric in WIND_METRICS):
        raise ValueError("station results need at least 12 paired sites per season for both wind metrics")

    groups = {}
    for row in station:
        key = (row["season"], row["metric"], row["elevation_class"], row["terrain_class"])
        groups.setdefault(key, []).append(row)
    elevation = []
    for (season, metric, elevation_class, terrain_class), rows in groups.items():
        count = len(rows)
        mean_hicar = sum(row["hicar_rmse"] for row in rows) / count
        mean_rea_l = sum(row["rea_l_rmse"] for row in rows) / count
        hicar = math.sqrt(sum(row["hicar_rmse"] ** 2 for row in rows) / count)
        rea_l = math.sqrt(sum(row["rea_l_rmse"] ** 2 for row in rows) / count)
        elevation.append({"season": season, "metric": metric, "metric_label": METRICS[metric][0],
            "unit": METRICS[metric][1], "metric_order": list(METRICS).index(metric),
            "elevation_class": elevation_class, "terrain_class": terrain_class,
            "paired_station_count": count, "pair_count_total": sum(row["pair_count"] for row in rows),
            "mean_terrain_relative_elevation_m": sum(row["terrain_relative_elevation_m"] for row in rows) / count,
            "primary_rmse_estimand": "equal_station_network_rmse",
            "equal_station_network_hicar_rmse": hicar,
            "equal_station_network_rea_l_rmse": rea_l,
            "equal_station_network_rmse_delta": hicar - rea_l,
            "mean_station_hicar_rmse": mean_hicar,
            "mean_station_rea_l_rmse": mean_rea_l,
            "mean_station_rmse_delta": mean_hicar - mean_rea_l,
            "normalized_rmse_difference": normalized_difference(hicar, rea_l)})

    footprint = []
    for season in SEASONS:
        for site in evidence["footprint_reports"][season]["sites"]:
            for radius in ("0.4", "1"):
                result = site["footprints"][radius]
                nearest, mean, geometry = result["nearest_cell"], result["footprint_mean_vector"], result["geometry"]
                if int(nearest["pair_count"]) != int(mean["pair_count"]):
                    raise ValueError(f"{season}/{site['site_key']}/{radius} footprint pair counts differ")
                footprint.append({"season": season, "site_key": site["site_key"],
                    "station_elevation_m": float(site["station_elevation_m"]),
                    "terrain_relative_elevation_m": float(site["terrain_relative_elevation_m"]),
                    "radius_km": float(radius), "pair_count": int(nearest["pair_count"]),
                    "nearest_rmse_m_s": float(nearest["vector_rmse_m_s"]),
                    "footprint_mean_rmse_m_s": float(mean["vector_rmse_m_s"]),
                    "footprint_minus_nearest_rmse_m_s": float(mean["vector_rmse_m_s"]) - float(nearest["vector_rmse_m_s"]),
                    "coverage_fraction": float(geometry["coverage_fraction"]),
                    "actual_cell_count": int(geometry["actual_cell_count"])})
    if len(footprint) < 8:
        raise ValueError("footprint results are too sparse")
    tables = {"seasonal_metrics": "seasonal", "seasonal_population_sensitivity": "seasonal_sensitivity",
              "lead_metrics": "lead", "ridge_lead_metrics": "ridge_lead", "station_wind": "station",
              "elevation_counts": "elevation", "footprint_wind": "footprint"}
    rows = {"seasonal_metrics": seasonal, "seasonal_population_sensitivity": seasonal_sensitivity,
            "lead_metrics": lead, "ridge_lead_metrics": ridge_lead, "station_wind": station,
            "elevation_counts": elevation, "footprint_wind": footprint}
    return {name: query_rows(tables[name], rows[name], QUERIES[name]) for name in rows}


def source(evidence, source_id, key, label, season=None):
    path = evidence["paths"][key] if season is None else evidence["paths"][key][season]
    relative = evidence["relative"][key] if season is None else evidence["relative"][key][season]
    return {"id": source_id, "label": label, "path": relative,
            "query": {"engine": "filesystem", "id": f"sha256:{digest(path)}",
                      "language": path.suffix.lstrip("."), "description": "Reviewed local result file."}}


def build_artifact(evidence):
    datasets, assessment = derive_datasets(evidence), evidence["reviewed_assessment"]
    sources = [
        source(evidence, "campaign_file", "campaign_evidence", "Campaign evidence"),
        source(evidence, "geometry_file", "geometry_validation", "Geometry validation"),
        source(evidence, "restart_file", "restart_comparison", "Exact restart comparison"),
        source(evidence, "national_file", "national_summary", "National station summary"),
        source(evidence, "station_file", "station_season_csv", "Station-season metrics"),
        source(evidence, "assessment_file", "reviewed_assessment", "Analyst-authored assessment"),
    ] + [source(evidence, f"footprint_{season.lower()}_file", "footprint_reports",
                f"{season} footprint diagnostic", season) for season in SEASONS]
    used = {
        "seasonal_metrics": ["national_summary.json#equal_station_summaries"],
        "seasonal_population_sensitivity": ["national_summary.json#equal_station_summaries"],
        "lead_metrics": ["national_summary.json#lead_hour_tables"],
        "ridge_lead_metrics": ["national_summary.json#lead_hour_tables"],
        "station_wind": ["station_season_metrics.csv"],
        "elevation_counts": ["station_season_metrics.csv"],
        "footprint_wind": [f"footprint_{season}.json#sites" for season in SEASONS],
    }
    for name in datasets:
        sources.append({"id": f"{name}_query", "label": name.replace("_", " ").title(),
            "path": "build_artifact.py", "query": {"engine": "sqlite", "sql": QUERIES[name],
            "description": "Deterministic selection of reviewed rows.", "language": "sql",
            "tables_used": used[name]}})

    def finding(name, source_id=None):
        item = assessment["findings"][name]
        block = {"id": f"finding_{name}", "type": "markdown",
                 "body": f"## {item['heading']}\n\n{item['body']}"}
        if source_id:
            block["sourceId"] = source_id
        return block

    def encoding(field, label, kind="quantitative", unit=None):
        value = {"field": field, "type": kind, "label": label}
        if unit:
            value["unit"] = unit
        return value

    charts = [
        {"id": "seasonal_metrics", "title": "Seasonal equal-station network RMSE difference",
         "subtitle": "sqrt(mean station MSE); normalized HICAR - REA-L difference, negative favors HICAR",
         "type": "bar", "dataset": "seasonal_metrics", "sourceId": "seasonal_metrics_query",
         "encodings": {"x": encoding("season", "Season", "ordinal"),
             "y": encoding("normalized_rmse_difference", "Normalized RMSE difference"),
             "color": encoding("metric_label", "Metric", "nominal"),
             "tooltip": [encoding("paired_station_count", "Paired stations"),
                         encoding("mean_station_hicar_rmse", "Mean station HICAR RMSE"),
                         encoding("network_pooled_hicar_rmse", "Pair-pooled HICAR RMSE"),
                         encoding("unit", "Native unit", "text")]}},
        {"id": "lead_metrics", "title": "Normalized RMSE difference by lead hour and metric",
         "subtitle": "Four event trajectories per metric; lead hour remains confounded with valid time",
         "type": "line", "dataset": "lead_metrics", "sourceId": "lead_metrics_query",
         "encodings": {"x": encoding("lead_hour", "Lead hour", unit="h"),
             "y": encoding("normalized_rmse_difference", "Normalized RMSE difference"),
             "color": encoding("season", "Season", "nominal"),
             "facet": encoding("metric_label", "Metric", "nominal"),
             "tooltip": [encoding("pair_count", "Paired observations"),
                         encoding("unit", "Native unit", "text")]}},
        {"id": "ridge_lead_metrics", "title": "Ridge/high-elevation wind-vector skill around restart turnover",
         "subtitle": "Lead 12 is the first segment endpoint; lead 13 is the first unique restarted-segment output",
         "type": "line", "dataset": "ridge_lead_metrics", "sourceId": "ridge_lead_metrics_query",
         "encodings": {"x": encoding("lead_hour", "Lead hour", unit="h"),
             "y": encoding("normalized_rmse_difference", "Normalized RMSE difference"),
             "color": encoding("stratum_label", "Spatial stratum", "nominal"),
             "facet": encoding("season", "Season", "nominal"),
             "tooltip": [encoding("pair_count", "Paired observations"),
                         encoding("segment", "Segment", "text"),
                         encoding("hicar_rmse", "HICAR vector RMSE", unit="m s^-1"),
                         encoding("rea_l_rmse", "REA-L vector RMSE", unit="m s^-1")]}},
        {"id": "station_wind", "title": "Station wind skill difference by elevation",
         "subtitle": "Wind speed and vector RMSE; one point per eligible station-season",
         "type": "scatter", "dataset": "station_wind", "sourceId": "station_wind_query",
         "encodings": {"x": encoding("station_elevation_m", "Station elevation", unit="m"),
             "y": encoding("normalized_rmse_difference", "Normalized RMSE difference"),
             "color": encoding("season", "Season", "nominal"),
             "facet": encoding("metric_label", "Wind metric", "nominal"),
             "label": encoding("station_key", "Station", "text"),
             "tooltip": [encoding("pair_count", "Paired observations"),
                         encoding("hicar_minus_station_elevation_m", "HICAR minus station elevation", unit="m"),
                         encoding("nearest_cell_distance_km", "Nearest-cell distance", unit="km"),
                         encoding("terrain_class", "Terrain class", "text"),
                         encoding("terrain_relative_elevation_m", "Terrain-relative elevation", unit="m")]}},
        {"id": "footprint_wind", "title": "Selected-site nearest-cell and footprint-mean vector RMSE",
         "subtitle": "400 m and 1 km neighborhoods; nearest cell remains the primary score",
         "type": "scatter", "dataset": "footprint_wind", "sourceId": "footprint_wind_query",
         "encodings": {"x": encoding("nearest_rmse_m_s", "Nearest-cell RMSE", unit="m s^-1"),
             "y": encoding("footprint_mean_rmse_m_s", "Footprint-mean RMSE", unit="m s^-1"),
             "color": encoding("radius_km", "Radius (km)", "nominal"),
             "label": encoding("site_key", "Station", "text"),
             "tooltip": [encoding("season", "Season", "nominal"),
                         encoding("pair_count", "Paired observations"),
                         encoding("terrain_relative_elevation_m", "Terrain-relative elevation", unit="m")]}},
    ]

    def columns(*pairs):
        return [{"field": field, "label": label} for field, label in pairs]

    tables = [
        {"id": "seasonal_table", "title": "Seasonal headline metrics, native units",
         "subtitle": "Equal-station network RMSE is primary; mean-station and pair-pooled RMSE are sensitivities",
         "dataset": "seasonal_metrics", "sourceId": "seasonal_metrics_query", "density": "compact",
         "defaultSort": {"field": "season", "direction": "asc"},
         "columns": columns(("season", "Season"), ("metric_label", "Metric"), ("unit", "Unit"),
             ("paired_station_count", "Paired stations"),
             ("hicar_rmse", "Equal-station network HICAR RMSE"),
             ("rea_l_rmse", "Equal-station network REA-L RMSE"),
             ("mean_station_hicar_rmse", "Mean station HICAR RMSE"),
             ("mean_station_rea_l_rmse", "Mean station REA-L RMSE"),
             ("network_pooled_hicar_rmse", "Pair-pooled HICAR RMSE"),
             ("network_pooled_rea_l_rmse", "Pair-pooled REA-L RMSE"),
             ("hicar_bias", "HICAR bias"),
             ("rea_l_bias", "REA-L bias"), ("hicar_model_mean", "HICAR mean"),
             ("rea_l_model_mean", "REA-L mean"),
             ("hicar_observation_mean", "Obs mean (HICAR pairs)"),
             ("rea_l_observation_mean", "Obs mean (REA-L pairs)"))},
        {"id": "population_table", "title": "Seasonal station-population sensitivity",
         "subtitle": "All season-eligible stations alongside each metric's exact four-season eligible intersection",
         "dataset": "seasonal_population_sensitivity", "sourceId": "seasonal_population_sensitivity_query", "density": "compact",
         "defaultSort": {"field": "season", "direction": "asc"},
         "columns": columns(("season", "Season"), ("metric_label", "Metric"),
             ("population", "Station population"), ("paired_station_count", "Paired stations"),
             ("hicar_rmse", "Equal-station network HICAR RMSE"),
             ("rea_l_rmse", "Equal-station network REA-L RMSE"),
             ("normalized_rmse_difference", "Equal-station network normalized difference"),
             ("mean_station_hicar_rmse", "Mean station HICAR RMSE"),
             ("mean_station_rea_l_rmse", "Mean station REA-L RMSE"),
             ("network_pooled_hicar_rmse", "Pair-pooled HICAR RMSE"),
             ("network_pooled_rea_l_rmse", "Pair-pooled REA-L RMSE"),
             ("network_pooled_normalized_rmse_difference", "Pair-pooled normalized difference"))},
        {"id": "elevation_table", "title": "Wind eligibility and skill by elevation and terrain stratum",
         "subtitle": "Station counts and valid-pair totals are shown separately for wind speed and vector",
         "dataset": "elevation_counts", "sourceId": "elevation_counts_query", "density": "compact",
         "defaultSort": {"field": "season", "direction": "asc"},
         "columns": columns(("season", "Season"), ("metric_label", "Metric"),
             ("elevation_class", "Elevation stratum"), ("terrain_class", "Terrain class"),
             ("paired_station_count", "Paired stations"), ("pair_count_total", "Valid pairs"),
             ("mean_terrain_relative_elevation_m", "Mean terrain-relative elevation (m)"),
             ("equal_station_network_hicar_rmse", "Equal-station network HICAR RMSE"),
             ("equal_station_network_rea_l_rmse", "Equal-station network REA-L RMSE"),
             ("mean_station_hicar_rmse", "Mean station HICAR RMSE"),
             ("mean_station_rea_l_rmse", "Mean station REA-L RMSE"))},
        {"id": "footprint_table", "title": "Selected-site footprint sensitivity detail",
         "dataset": "footprint_wind", "sourceId": "footprint_wind_query", "density": "compact",
         "defaultSort": {"field": "season", "direction": "asc"},
         "columns": columns(("season", "Season"), ("site_key", "Station"),
             ("station_elevation_m", "Station elevation (m)"),
             ("terrain_relative_elevation_m", "Terrain-relative elevation (m)"),
             ("radius_km", "Radius (km)"), ("pair_count", "Paired observations"),
             ("nearest_rmse_m_s", "Nearest RMSE"),
             ("footprint_mean_rmse_m_s", "Footprint mean RMSE"))},
    ]
    assessment_method = evidence["national_summary"]["method"]
    coverage = evidence["national_summary"]["coverage"]
    def bullets(values):
        return "\n".join(f"- {value}" for value in values)
    blocks = [
        {"id": "title", "type": "markdown", "body": f"# {TITLE}"},
        {"id": "summary", "type": "markdown", "sourceId": "assessment_file",
         "body": f"## Technical summary\n\n**Analyst assessment: {assessment['readiness_status'].replace('_', ' ')}.** {assessment['technical_summary']}"},
        finding("seasonal_skill", "national_file"),
        {"id": "seasonal_chart", "type": "chart", "chartId": "seasonal_metrics"},
        {"id": "seasonal_table_block", "type": "table", "tableId": "seasonal_table"},
        {"id": "population_table_block", "type": "table", "tableId": "population_table"},
        finding("lead_time", "national_file"),
        {"id": "lead_chart", "type": "chart", "chartId": "lead_metrics"},
        {"id": "ridge_lead_chart", "type": "chart", "chartId": "ridge_lead_metrics"},
        finding("elevation_wind", "station_file"),
        {"id": "station_chart", "type": "chart", "chartId": "station_wind"},
        {"id": "elevation_table_block", "type": "table", "tableId": "elevation_table"},
        finding("footprint_sensitivity"),
        {"id": "footprint_chart", "type": "chart", "chartId": "footprint_wind"},
        {"id": "footprint_table_block", "type": "table", "tableId": "footprint_table"},
        finding("inputs_and_grid"), finding("restart", "restart_file"),
        {"id": "scope", "type": "markdown", "sourceId": "national_file",
         "body": f"## Scope, comparison basis, and definitions\n\nThe four reports contain {coverage['station_key_union_count']} distinct SwissMetNet keys, but eligibility is metric- and season-specific; every result therefore carries its own paired-station or paired-observation count. Primary seasonal summaries use every eligible station available in that season. The exact four-season key intersection is reported separately as a station-population sensitivity. Normalized RMSE difference is (HICAR - REA-L)/(HICAR + REA-L); negative values favor HICAR."},
        {"id": "methods", "type": "markdown", "sourceId": "national_file",
         "body": f"## Validation and aggregation design\n\n**Station grain.** {assessment_method['station_grain']}.\n\n**Pairing.** {assessment_method['pairing_rule']}\n\n**Aggregation.** {assessment_method['aggregation']}\n\n**Lead hours.** {assessment_method['lead_hour_aggregation']} The first precipitation interval may be absent because accumulated precipitation needs a preceding model output. Lead hour remains confounded with valid time, diurnal phase, and event evolution.\n\nFootprint means diagnose point-to-grid sensitivity and do not replace the nearest-cell score."},
        {"id": "limitations", "type": "markdown", "sourceId": "assessment_file",
         "body": "## Limitations and uncertainty\n\n" + bullets(assessment["limitations"])},
        {"id": "next", "type": "markdown", "sourceId": "assessment_file",
         "body": "## Recommended next steps\n\n" + bullets(assessment["recommended_next_steps"])},
        {"id": "questions", "type": "markdown", "sourceId": "assessment_file",
         "body": "## Further questions\n\n" + bullets(assessment["further_questions"])},
    ]
    manifest_sources = [{key: item[key] for key in ("id", "label", "path")} for item in sources]
    return {"surface": "report", "manifest": {"version": 1, "surface": "report",
        "title": TITLE, "description": "Technical assessment of national 200 m HICAR downscaling.",
        "generatedAt": evidence["generated_at"], "cards": [], "charts": charts, "tables": tables,
        "sources": manifest_sources, "blocks": blocks}, "snapshot": {"version": 1,
        "generatedAt": evidence["generated_at"], "status": "ready", "datasets": datasets,
        "accessIssues": []}, "sources": sources}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        artifact = build_artifact(load_evidence(args.inputs))
        write_json(args.output, artifact)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        parser.error(str(error))
    print(f"Wrote {args.output} with six headline metrics from reviewed evidence")


if __name__ == "__main__":
    main()
