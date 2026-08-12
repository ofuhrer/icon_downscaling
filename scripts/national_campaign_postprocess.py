#!/usr/bin/env python3
"""Summarize four national SwissMetNet HICAR/REA-L evaluator reports.

The evaluator reports contain aggregate errors at each station and lead hour.
Consequently, this program can make both equal-station comparisons and
pair-count-weighted network-pooled RMSE reconstructions without opening large
HICAR files. It deliberately excludes a station/metric when HICAR and REA-L
do not have the same pair count or fewer than 20 valid hourly pairs.

Footprint sensitivity cannot be reconstructed from evaluator aggregates.  The
output JSON therefore records the exact row-level/model-file contract needed by
a later implementation that streams one HICAR timestep at a time.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
from statistics import mean, median, quantiles
from tempfile import NamedTemporaryFile
from typing import Iterable


SEASONS = ("DJF", "MAM", "JJA", "SON")
SOURCES = ("hicar", "rea_l")
TIE_TOLERANCE = 1.0e-12
MINIMUM_STATION_EVENT_PAIRS = 20
MINIMUM_NATIONAL_WIND_STATIONS = 20
MINIMUM_SAFEGUARD_STATIONS = 10
REQUIRED_WIND_EVENT_PAIR_COUNT = 24
REQUIRED_WIND_MATCHED_ENDPOINT_COUNT = 25
REQUIRED_WIND_PHYSICAL_LEADS = tuple(range(25, 49))
WIND_METRICS = ("wind_vector", "wind_speed_10m_m_s")
WIND_SAFEGUARDS = {
    "terrain_ridge_relative_gt_150m": (
        lambda row: row["terrain_class"] == "terrain_ridge_relative_gt_150m"
    ),
    "terrain_valley_relative_lt_minus_150m": (
        lambda row: row["terrain_class"] == "terrain_valley_relative_lt_minus_150m"
    ),
    "station_elevation_ge_1500m": (lambda row: float(row["station_elevation_m"]) >= 1500.0),
}

FOOTPRINT_INPUT_CONTRACT = {
    "status": "not_computable_from_evaluator_aggregates",
    "reason": (
        "The evaluator JSON has aggregate station errors and HICAR x/y indices, "
        "but no timestamp-level observed wind vectors or model output-file list."
    ),
    "required_manifest": {
        "schema_version": 1,
        "static_file": (
            "NetCDF used by the evaluator, containing the 2-D terrain field and "
            "enough grid metadata to establish 400 m and 1000 m cell-center masks"
        ),
        "events": {
            "<season>": {
                "evaluator_report": "path to the evaluator JSON used here",
                "hicar_output_files": ["time-ordered NetCDF files containing time, u10m, and v10m"],
                "observed_wind_pairs_csv": (
                    "CSV with unique (valid_time, station_key) rows and finite "
                    "observed_u10_m_s, observed_v10_m_s"
                ),
            }
        },
    },
    "mapping_requirements": (
        "Use station_mapping.sites[].hicar_y_index/hicar_x_index from the report; "
        "the output grid and report mapping must refer to the same static domain."
    ),
    "streaming_algorithm": (
        "For each output timestep, read only u10/v10; for radii 400 m and 1000 m "
        "accumulate nearest-cell and footprint-mean vector squared errors, each "
        "fixed cell's squared error, spatial wind spread, and terrain range/std. "
        "Report RMSE only after all timesteps and never select a best cell as the "
        "primary score."
    ),
}


def parse_report_spec(value: str) -> tuple[str, Path]:
    try:
        season, raw_path = value.split("=", 1)
    except ValueError as error:
        raise argparse.ArgumentTypeError("report must be SEASON=PATH") from error
    season = season.upper()
    if season not in SEASONS:
        raise argparse.ArgumentTypeError(f"season must be one of {', '.join(SEASONS)}")
    if not raw_path:
        raise argparse.ArgumentTypeError("report path is empty")
    return season, Path(raw_path)


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_report(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        report = json.load(stream)
    required = {
        "common_triplet_accounting",
        "event_name",
        "matched_model_times",
        "station_mapping",
        "site_metrics",
        "lead_time_metrics",
    }
    absent = sorted(required - set(report))
    if absent:
        raise ValueError(f"{path}: missing report fields: {', '.join(absent)}")
    accounting = report["common_triplet_accounting"]
    if not isinstance(accounting, dict) or not isinstance(accounting.get("metrics"), dict):
        raise ValueError(f"{path}: invalid common-triplet accounting")
    if report.get("issues"):
        raise ValueError(f"{path}: evaluator reports issues: {report['issues']}")
    return report


def station_metadata(report: dict, path: Path) -> dict[str, dict]:
    sites: dict[str, dict] = {}
    for item in report["station_mapping"].get("sites", []):
        key = item.get("key")
        if not key or key in sites:
            raise ValueError(f"{path}: missing or duplicate station key {key!r}")
        sites[key] = item
    metric_keys = set(report["site_metrics"])
    if metric_keys != set(sites):
        missing_mapping = sorted(metric_keys - set(sites))
        missing_metrics = sorted(set(sites) - metric_keys)
        raise ValueError(
            f"{path}: mapping/site_metrics key mismatch; "
            f"missing mapping={missing_mapping[:5]}, missing metrics={missing_metrics[:5]}"
        )
    return sites


def rmse_value(metric_name: str, values: dict) -> float | None:
    field = (
        "vector_root_mean_squared_error_m_s"
        if metric_name == "wind_vector"
        else "root_mean_squared_circular_error_degrees"
        if metric_name == "wind_direction"
        else "root_mean_squared_error"
    )
    value = values.get(field)
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def comparison_row(
    hicar: dict,
    rea_l: dict,
    metric: str,
) -> tuple[dict | None, str | None]:
    h_count = int(hicar.get("count", 0))
    r_count = int(rea_l.get("count", 0))
    if h_count <= 0 or r_count <= 0:
        return None, "zero_pair_count"
    if h_count != r_count:
        return None, "unequal_pair_count"
    h_rmse = rmse_value(metric, hicar)
    r_rmse = rmse_value(metric, rea_l)
    if h_rmse is None or r_rmse is None:
        return None, "missing_or_nonfinite_rmse"
    delta = h_rmse - r_rmse
    outcome = (
        "improved" if delta < -TIE_TOLERANCE else "degraded" if delta > TIE_TOLERANCE else "tied"
    )
    moments: dict[str, float | None] = {
        "hicar_bias": None,
        "rea_l_bias": None,
        "hicar_model_mean": None,
        "rea_l_model_mean": None,
        "hicar_observation_mean": None,
        "rea_l_observation_mean": None,
        "observation_mean": None,
    }
    diagnostics: dict[str, float | None] = {
        "hicar_mae": None,
        "rea_l_mae": None,
        "hicar_centered_rmse": None,
        "rea_l_centered_rmse": None,
        "hicar_standard_deviation_ratio": None,
        "rea_l_standard_deviation_ratio": None,
        "hicar_correlation": None,
        "rea_l_correlation": None,
    }
    try:
        hicar_model_mean = float(hicar["model_mean"])
        rea_l_model_mean = float(rea_l["model_mean"])
        hicar_observation_mean = float(hicar["observation_mean"])
        rea_l_observation_mean = float(rea_l["observation_mean"])
        hicar_bias = float(hicar["bias"])
        rea_l_bias = float(rea_l["bias"])
    except (KeyError, TypeError, ValueError):
        pass
    else:
        values = (
            hicar_model_mean,
            rea_l_model_mean,
            hicar_observation_mean,
            rea_l_observation_mean,
            hicar_bias,
            rea_l_bias,
        )
        if all(math.isfinite(value) for value in values) and math.isclose(
            hicar_observation_mean,
            rea_l_observation_mean,
            rel_tol=0.0,
            abs_tol=TIE_TOLERANCE,
        ):
            moments = {
                "hicar_bias": hicar_bias,
                "rea_l_bias": rea_l_bias,
                "hicar_model_mean": hicar_model_mean,
                "rea_l_model_mean": rea_l_model_mean,
                "hicar_observation_mean": hicar_observation_mean,
                "rea_l_observation_mean": rea_l_observation_mean,
                # Backward-compatible shared-observation alias. The explicit
                # model-pair names above make the field naming consistent with
                # the corresponding model means.
                "observation_mean": (hicar_observation_mean + rea_l_observation_mean) / 2.0,
            }
    try:
        hicar_mae = float(hicar["mean_absolute_error"])
        rea_l_mae = float(rea_l["mean_absolute_error"])
    except (KeyError, TypeError, ValueError):
        pass
    else:
        if math.isfinite(hicar_mae) and math.isfinite(rea_l_mae):
            diagnostics["hicar_mae"] = hicar_mae
            diagnostics["rea_l_mae"] = rea_l_mae
    if moments["hicar_bias"] is not None and moments["rea_l_bias"] is not None:
        diagnostics["hicar_centered_rmse"] = math.sqrt(
            max(h_rmse * h_rmse - moments["hicar_bias"] ** 2, 0.0)
        )
        diagnostics["rea_l_centered_rmse"] = math.sqrt(
            max(r_rmse * r_rmse - moments["rea_l_bias"] ** 2, 0.0)
        )
    if h_count >= 20:
        try:
            hicar_model_sd = float(hicar["model_standard_deviation"])
            rea_l_model_sd = float(rea_l["model_standard_deviation"])
            hicar_observation_sd = float(hicar["observation_standard_deviation"])
            rea_l_observation_sd = float(rea_l["observation_standard_deviation"])
            hicar_correlation = float(hicar["correlation"])
            rea_l_correlation = float(rea_l["correlation"])
        except (KeyError, TypeError, ValueError):
            pass
        else:
            values = (
                hicar_model_sd,
                rea_l_model_sd,
                hicar_observation_sd,
                rea_l_observation_sd,
                hicar_correlation,
                rea_l_correlation,
            )
            if (
                all(math.isfinite(value) for value in values)
                and hicar_observation_sd > 0.0
                and rea_l_observation_sd > 0.0
                and math.isclose(
                    hicar_observation_sd,
                    rea_l_observation_sd,
                    rel_tol=0.0,
                    abs_tol=TIE_TOLERANCE,
                )
            ):
                diagnostics.update(
                    {
                        "hicar_standard_deviation_ratio": hicar_model_sd / hicar_observation_sd,
                        "rea_l_standard_deviation_ratio": rea_l_model_sd / rea_l_observation_sd,
                        "hicar_correlation": hicar_correlation,
                        "rea_l_correlation": rea_l_correlation,
                    }
                )
    return {
        "pair_count": h_count,
        "hicar_rmse": h_rmse,
        "rea_l_rmse": r_rmse,
        "rmse_delta_hicar_minus_rea_l": delta,
        "outcome": outcome,
        **moments,
        **diagnostics,
    }, None


def metric_names(site_metrics: dict) -> set[str]:
    result: set[str] = set()
    for values in site_metrics.values():
        if not all(source in values for source in SOURCES):
            continue
        for metric in set(values["hicar"]) & set(values["rea_l"]):
            if rmse_value(metric, values["hicar"][metric]) is not None:
                result.add(metric)
    return result


def elevation_class(elevation_m: float) -> str:
    if elevation_m < 500.0:
        return "elevation_lt_500m"
    if elevation_m < 1000.0:
        return "elevation_500_1000m"
    if elevation_m < 1500.0:
        return "elevation_1000_1500m"
    if elevation_m < 2000.0:
        return "elevation_1500_2000m"
    if elevation_m < 3000.0:
        return "elevation_2000_3000m"
    return "elevation_ge_3000m"


def terrain_class(relative_elevation_m: float) -> str:
    if relative_elevation_m < -150.0:
        return "terrain_valley_relative_lt_minus_150m"
    if relative_elevation_m > 150.0:
        return "terrain_ridge_relative_gt_150m"
    return "terrain_neutral_relative_pm_150m"


def load_common_keys(
    report_paths: list[Path], site_file: Path | None, expected_count: int = 65
) -> tuple[set[str] | None, dict]:
    if report_paths and site_file is not None:
        raise ValueError("use either --common-65-report or --common-65-site-file, not both")
    if report_paths:
        if len(report_paths) != 4:
            raise ValueError("--common-65-report must be supplied exactly four times")
        key_sets = []
        for path in report_paths:
            report = load_report(path)
            key_sets.append(set(report["site_metrics"]))
        keys = set.intersection(*key_sets)
        provenance = {
            "method": "intersection_of_four_evaluator_reports",
            "source_reports": [str(path.resolve()) for path in report_paths],
        }
    elif site_file is not None:
        keys = {
            line.strip()
            for line in site_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        provenance = {
            "method": "explicit_site_key_file",
            "source_file": str(site_file.resolve()),
        }
    else:
        return None, {
            "available": False,
            "reason": (
                "No historical four-report intersection or reviewed 65-key file "
                "was provided; the national four-season intersection is not a "
                "substitute for the historical common-65 bridge subset."
            ),
        }
    if len(keys) != expected_count:
        raise ValueError(f"common-site definition has {len(keys)} keys, expected {expected_count}")
    provenance.update({"available": True, "site_count": len(keys)})
    return keys, provenance


def station_season_rows(
    reports: dict[str, tuple[Path, dict]],
    common_keys: set[str] | None,
    selected_metrics: set[str] | None,
) -> tuple[list[dict], dict, dict[str, dict]]:
    rows: list[dict] = []
    exclusions: dict[str, int] = defaultdict(int)
    canonical_metadata: dict[str, dict] = {}
    for season in SEASONS:
        path, report = reports[season]
        metadata = station_metadata(report, path)
        available_metrics = metric_names(report["site_metrics"])
        metrics = (
            available_metrics if selected_metrics is None else available_metrics & selected_metrics
        )
        for key, site_values in sorted(report["site_metrics"].items()):
            site = metadata[key]
            canonical = {
                field: site[field]
                for field in (
                    "key",
                    "abbreviation",
                    "meas_site",
                    "latitude",
                    "longitude",
                    "station_elevation_m",
                    "hicar_elevation_m",
                    "nearest_cell_distance_km",
                    "terrain_relative_elevation_m",
                )
            }
            previous = canonical_metadata.setdefault(key, canonical)
            if previous != canonical:
                raise ValueError(f"station metadata changes across seasons for {key}")
            for metric in sorted(metrics):
                comparison, reason = comparison_row(
                    site_values["hicar"].get(metric, {}),
                    site_values["rea_l"].get(metric, {}),
                    metric,
                )
                if comparison is None:
                    exclusions[f"{reason}:{season}:{metric}"] += 1
                    continue
                if comparison["pair_count"] < MINIMUM_STATION_EVENT_PAIRS:
                    exclusions[f"insufficient_pair_count:{season}:{metric}"] += 1
                    continue
                elevation = float(site["station_elevation_m"])
                relative = float(site["terrain_relative_elevation_m"])
                rows.append(
                    {
                        "season": season,
                        "event_name": report["event_name"],
                        "station_key": key,
                        "abbreviation": site["abbreviation"],
                        "meas_site": site["meas_site"],
                        "latitude": float(site["latitude"]),
                        "longitude": float(site["longitude"]),
                        "station_elevation_m": elevation,
                        "hicar_elevation_m": float(site["hicar_elevation_m"]),
                        "terrain_relative_elevation_m": relative,
                        "elevation_class": elevation_class(elevation),
                        "terrain_class": terrain_class(relative),
                        "nearest_cell_distance_km": float(site["nearest_cell_distance_km"]),
                        "in_common_65": key in common_keys if common_keys is not None else None,
                        "metric": metric,
                        **comparison,
                    }
                )
    return rows, dict(sorted(exclusions.items())), canonical_metadata


def summarize_group(rows: list[dict]) -> dict:
    deltas = [row["rmse_delta_hicar_minus_rea_l"] for row in rows]
    pair_count_total = sum(row["pair_count"] for row in rows)
    mean_station_hicar_rmse = mean(row["hicar_rmse"] for row in rows)
    mean_station_rea_l_rmse = mean(row["rea_l_rmse"] for row in rows)
    equal_station_network_hicar_rmse = math.sqrt(mean(row["hicar_rmse"] ** 2 for row in rows))
    equal_station_network_rea_l_rmse = math.sqrt(mean(row["rea_l_rmse"] ** 2 for row in rows))
    network_pooled_hicar_rmse = math.sqrt(
        sum(row["pair_count"] * row["hicar_rmse"] ** 2 for row in rows) / pair_count_total
    )
    network_pooled_rea_l_rmse = math.sqrt(
        sum(row["pair_count"] * row["rea_l_rmse"] ** 2 for row in rows) / pair_count_total
    )
    result = {
        "paired_station_count": len(rows),
        "pair_count_total": pair_count_total,
        "mean_station_hicar_rmse": mean_station_hicar_rmse,
        "mean_station_rea_l_rmse": mean_station_rea_l_rmse,
        "mean_station_rmse_delta_hicar_minus_rea_l": mean(deltas),
        "equal_station_network_hicar_rmse": equal_station_network_hicar_rmse,
        "equal_station_network_rea_l_rmse": equal_station_network_rea_l_rmse,
        "equal_station_network_rmse_delta_hicar_minus_rea_l": (
            equal_station_network_hicar_rmse - equal_station_network_rea_l_rmse
        ),
        # Compatibility aliases for the original mean-of-station-RMSE estimand.
        "equal_station_mean_hicar_rmse": mean_station_hicar_rmse,
        "equal_station_mean_rea_l_rmse": mean_station_rea_l_rmse,
        "equal_station_mean_rmse_delta_hicar_minus_rea_l": mean(deltas),
        "network_pooled_hicar_rmse": network_pooled_hicar_rmse,
        "network_pooled_rea_l_rmse": network_pooled_rea_l_rmse,
        "network_pooled_rmse_delta_hicar_minus_rea_l": (
            network_pooled_hicar_rmse - network_pooled_rea_l_rmse
        ),
        "median_station_rmse_delta_hicar_minus_rea_l": median(deltas),
        "improved_station_count": sum(row["outcome"] == "improved" for row in rows),
        "degraded_station_count": sum(row["outcome"] == "degraded" for row in rows),
        "tied_station_count": sum(row["outcome"] == "tied" for row in rows),
    }
    moment_fields = (
        "hicar_bias",
        "rea_l_bias",
        "hicar_model_mean",
        "rea_l_model_mean",
        "hicar_observation_mean",
        "rea_l_observation_mean",
        "observation_mean",
    )
    moment_rows = [
        row for row in rows if all(row.get(field) is not None for field in moment_fields)
    ]
    if moment_rows:
        result["mean_bias_paired_station_count"] = len(moment_rows)
        summary_fields = {
            "hicar_bias": "equal_station_mean_hicar_bias",
            "rea_l_bias": "equal_station_mean_rea_l_bias",
            "hicar_model_mean": "equal_station_mean_hicar_model_mean",
            "rea_l_model_mean": "equal_station_mean_rea_l_model_mean",
            "hicar_observation_mean": "equal_station_mean_hicar_observation_mean",
            "rea_l_observation_mean": "equal_station_mean_rea_l_observation_mean",
            "observation_mean": "equal_station_mean_observation_mean",
        }
        result.update(
            {
                output_field: mean(row[input_field] for row in moment_rows)
                for input_field, output_field in summary_fields.items()
            }
        )
        # Preserve the original public field while exposing the consistently
        # composed ``equal_station_mean_`` + ``observation_mean`` name.
        result["equal_station_mean_observation"] = result["equal_station_mean_observation_mean"]
    anatomy_fields = (
        "hicar_bias",
        "rea_l_bias",
        "hicar_mae",
        "rea_l_mae",
    )
    anatomy_rows = [
        row for row in rows if all(row.get(field) is not None for field in anatomy_fields)
    ]
    if len(anatomy_rows) == len(rows):
        hicar_rms_station_bias = math.sqrt(mean(row["hicar_bias"] ** 2 for row in anatomy_rows))
        rea_l_rms_station_bias = math.sqrt(mean(row["rea_l_bias"] ** 2 for row in anatomy_rows))
        hicar_within_station_centered_rmse = math.sqrt(
            mean(row["hicar_centered_rmse"] ** 2 for row in anatomy_rows)
        )
        rea_l_within_station_centered_rmse = math.sqrt(
            mean(row["rea_l_centered_rmse"] ** 2 for row in anatomy_rows)
        )
        result.update(
            {
                "error_anatomy_paired_station_count": len(anatomy_rows),
                "equal_station_mean_hicar_mae": mean(row["hicar_mae"] for row in anatomy_rows),
                "equal_station_mean_rea_l_mae": mean(row["rea_l_mae"] for row in anatomy_rows),
                "equal_station_rms_hicar_station_bias": hicar_rms_station_bias,
                "equal_station_rms_rea_l_station_bias": rea_l_rms_station_bias,
                "equal_station_within_station_hicar_centered_rmse": (
                    hicar_within_station_centered_rmse
                ),
                "equal_station_within_station_rea_l_centered_rmse": (
                    rea_l_within_station_centered_rmse
                ),
                # Retain the established public names, now with the exact
                # station-aware decomposition. Removing only the signed
                # network-mean bias incorrectly classifies opposing persistent
                # station biases as temporal variability.
                "equal_station_network_hicar_centered_rmse": (hicar_within_station_centered_rmse),
                "equal_station_network_rea_l_centered_rmse": (rea_l_within_station_centered_rmse),
            }
        )
    diagnostic_fields = (
        "hicar_standard_deviation_ratio",
        "rea_l_standard_deviation_ratio",
        "hicar_correlation",
        "rea_l_correlation",
    )
    diagnostic_rows = [
        row
        for row in rows
        if row["pair_count"] >= 20
        and all(row.get(field) is not None for field in diagnostic_fields)
    ]
    if len(diagnostic_rows) >= 10:
        result["diagnostic_paired_station_count"] = len(diagnostic_rows)
        for field in diagnostic_fields:
            values = [row[field] for row in diagnostic_rows]
            lower, _, upper = quantiles(values, n=4, method="inclusive")
            result[f"median_station_{field}"] = median(values)
            result[f"station_{field}_q1"] = lower
            result[f"station_{field}_q3"] = upper
    return result


def equal_station_summaries(
    rows: list[dict],
    common_available: bool,
    national_metric_four_season_keys: dict[str, set[str]],
) -> list[dict]:
    groups: dict[tuple[str, str, str, str], list[dict]] = defaultdict(list)
    for row in rows:
        strata = ("all_sites", row["elevation_class"], row["terrain_class"])
        subsets = ["national"]
        if row["station_key"] in national_metric_four_season_keys.get(row["metric"], set()):
            subsets.append("national_four_season_intersection")
        if common_available and row["in_common_65"]:
            subsets.append("common_65")
        for subset in subsets:
            for stratum in strata:
                groups[(subset, row["season"], stratum, row["metric"])].append(row)
    return [
        {
            "subset": key[0],
            "season": key[1],
            "stratum": key[2],
            "metric": key[3],
            **summarize_group(values),
        }
        for key, values in sorted(groups.items())
    ]


def lead_hour_tables(
    reports: dict[str, tuple[Path, dict]], selected_metrics: set[str] | None
) -> tuple[dict[str, list[dict]], dict[str, int]]:
    output: dict[str, list[dict]] = {}
    exclusions: dict[str, int] = defaultdict(int)
    for season in SEASONS:
        report = reports[season][1]
        sampling = report.get("sampling", {})
        try:
            simulation_start = datetime.fromisoformat(
                sampling["simulation_start"].replace("Z", "+00:00")
            )
            evaluation_start = datetime.fromisoformat(
                sampling["evaluation_start_inclusive"].replace("Z", "+00:00")
            )
        except (AttributeError, KeyError, ValueError) as error:
            raise ValueError(
                f"{reports[season][0]}: evaluator sampling lacks valid simulation "
                "and evaluation start times"
            ) from error
        if simulation_start.tzinfo is None:
            simulation_start = simulation_start.replace(tzinfo=timezone.utc)
        if evaluation_start.tzinfo is None:
            evaluation_start = evaluation_start.replace(tzinfo=timezone.utc)
        offset_seconds = (evaluation_start - simulation_start).total_seconds()
        if offset_seconds < 0.0 or offset_seconds % 3600.0:
            raise ValueError(
                f"{reports[season][0]}: evaluation start is not a nonnegative "
                "whole-hour lead from simulation start"
            )
        evaluation_start_lead_hour = int(offset_seconds // 3600.0)
        season_rows = []
        for raw_hour, values in sorted(
            report["lead_time_metrics"].items(), key=lambda item: int(item[0])
        ):
            physical_lead_hour = int(raw_hour)
            lead_hour = physical_lead_hour - evaluation_start_lead_hour
            if lead_hour < 0:
                raise ValueError(
                    f"{reports[season][0]}: physical lead {physical_lead_hour} "
                    "precedes the evaluation window"
                )
            hicar_strata = values.get("hicar", {})
            rea_l_strata = values.get("rea_l", {})
            for stratum in sorted(set(hicar_strata) & set(rea_l_strata)):
                hicar = hicar_strata[stratum]
                rea_l = rea_l_strata[stratum]
                metrics = set(hicar) & set(rea_l)
                if selected_metrics is not None:
                    metrics &= selected_metrics
                for metric in sorted(metrics):
                    comparison, reason = comparison_row(hicar[metric], rea_l[metric], metric)
                    if comparison is None:
                        exclusions[f"{reason}:{season}:{raw_hour}:{stratum}:{metric}"] += 1
                        continue
                    season_rows.append(
                        {
                            "physical_lead_hour": physical_lead_hour,
                            "lead_hour": lead_hour,
                            "stratum": stratum,
                            "metric": metric,
                            **comparison,
                        }
                    )
        output[season] = season_rows
    return output, dict(sorted(exclusions.items()))


def parsed_utc_time(value: object, context: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{context} must be an ISO timestamp string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{context} is not a valid ISO timestamp") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def nonnegative_count(value: object, context: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{context} must be a nonnegative integer")
    try:
        count = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{context} must be a nonnegative integer") from error
    if count < 0 or count != value:
        raise ValueError(f"{context} must be a nonnegative integer")
    return count


def validate_wind_source_reports(
    reports: dict[str, tuple[Path, dict]],
) -> dict[str, dict]:
    """Prove the fixed 24-hour wind-decision input contract for each event."""
    evidence: dict[str, dict] = {}
    expected_lead_keys = {str(value) for value in REQUIRED_WIND_PHYSICAL_LEADS}
    for season in SEASONS:
        path, report = reports[season]
        sampling = report.get("sampling")
        if not isinstance(sampling, dict):
            raise ValueError(f"{path}: evaluator sampling is missing or invalid")
        simulation_start = parsed_utc_time(
            sampling.get("simulation_start"), f"{path}: simulation_start"
        )
        evaluation_start = parsed_utc_time(
            sampling.get("evaluation_start_inclusive"),
            f"{path}: evaluation_start_inclusive",
        )
        evaluation_end = parsed_utc_time(
            sampling.get("evaluation_end_inclusive"),
            f"{path}: evaluation_end_inclusive",
        )
        if evaluation_start - simulation_start != timedelta(hours=24):
            raise ValueError(f"{path}: wind evaluation must start at physical lead 24")
        if evaluation_end - evaluation_start != timedelta(hours=24):
            raise ValueError(f"{path}: wind evaluation endpoints must span exactly 24 hours")

        raw_times = report.get("matched_model_times")
        if (
            not isinstance(raw_times, list)
            or len(raw_times) != REQUIRED_WIND_MATCHED_ENDPOINT_COUNT
        ):
            count = len(raw_times) if isinstance(raw_times, list) else "invalid"
            raise ValueError(
                f"{path}: wind decision requires exactly "
                f"{REQUIRED_WIND_MATCHED_ENDPOINT_COUNT} matched endpoints; got {count}"
            )
        matched_times = [
            parsed_utc_time(value, f"{path}: matched_model_times[{index}]")
            for index, value in enumerate(raw_times)
        ]
        expected_times = [
            evaluation_start + timedelta(hours=index)
            for index in range(REQUIRED_WIND_MATCHED_ENDPOINT_COUNT)
        ]
        if matched_times != expected_times:
            raise ValueError(
                f"{path}: matched_model_times must be the 25 ordered inclusive "
                "hourly evaluation endpoints"
            )

        lead_metrics = report.get("lead_time_metrics")
        if not isinstance(lead_metrics, dict) or set(lead_metrics) != expected_lead_keys:
            raise ValueError(f"{path}: lead_time_metrics physical leads must be exactly 25..48")
        evaluation_start_lead = int((evaluation_start - simulation_start).total_seconds() // 3600)
        normalized_leads = {int(raw_lead) - evaluation_start_lead for raw_lead in lead_metrics}
        if normalized_leads != set(range(1, 25)):
            raise ValueError(f"{path}: normalized wind evaluation leads must be exactly 1..24")

        accounting = report.get("common_triplet_accounting", {}).get("metrics", {})
        aggregate_metrics = report.get("metrics")
        if not isinstance(accounting, dict) or not isinstance(aggregate_metrics, dict):
            raise ValueError(f"{path}: wind decision requires common-triplet and aggregate metrics")
        reconciled: dict[str, dict] = {}
        for metric in WIND_METRICS:
            metric_accounting = accounting.get(metric)
            if not isinstance(metric_accounting, dict):
                raise ValueError(f"{path}: common-triplet accounting lacks {metric}")
            station_total = 0
            for station_key, sources in report["site_metrics"].items():
                try:
                    hicar_count = nonnegative_count(
                        sources["hicar"][metric]["count"],
                        f"{path}: {station_key}/hicar/{metric} count",
                    )
                    rea_l_count = nonnegative_count(
                        sources["rea_l"][metric]["count"],
                        f"{path}: {station_key}/rea_l/{metric} count",
                    )
                except KeyError as error:
                    raise ValueError(
                        f"{path}: station {station_key} lacks {metric} counts"
                    ) from error
                if hicar_count != rea_l_count:
                    raise ValueError(
                        f"{path}: station {station_key}/{metric} HICAR and REA-L "
                        "common-pair counts differ"
                    )
                station_total += hicar_count

            accepted = nonnegative_count(
                metric_accounting.get("accepted_common_triplet_count"),
                f"{path}: {metric} accepted_common_triplet_count",
            )
            candidate = nonnegative_count(
                metric_accounting.get("candidate_station_time_count"),
                f"{path}: {metric} candidate_station_time_count",
            )
            excluded = nonnegative_count(
                metric_accounting.get("excluded_station_time_count"),
                f"{path}: {metric} excluded_station_time_count",
            )
            exclusions = metric_accounting.get("exclusions", {})
            if not isinstance(exclusions, dict):
                raise ValueError(f"{path}: {metric} exclusions must be an object")
            exclusion_total = sum(
                nonnegative_count(value, f"{path}: {metric} exclusion {name}")
                for name, value in exclusions.items()
            )
            if candidate - accepted != excluded or exclusion_total != excluded:
                raise ValueError(f"{path}: {metric} common-triplet accounting does not reconcile")
            if station_total != accepted:
                raise ValueError(
                    f"{path}: {metric} station counts total {station_total}, "
                    f"but accounting reports {accepted} accepted triplets"
                )
            try:
                hicar_aggregate = nonnegative_count(
                    aggregate_metrics["hicar"]["all_sites"][metric]["count"],
                    f"{path}: hicar/all_sites/{metric} count",
                )
                rea_l_aggregate = nonnegative_count(
                    aggregate_metrics["rea_l"]["all_sites"][metric]["count"],
                    f"{path}: rea_l/all_sites/{metric} count",
                )
            except KeyError as error:
                raise ValueError(
                    f"{path}: aggregate metrics lack all_sites/{metric} counts"
                ) from error
            if hicar_aggregate != accepted or rea_l_aggregate != accepted:
                raise ValueError(
                    f"{path}: {metric} aggregate counts do not equal accepted triplets"
                )
            reconciled[metric] = {
                "accepted_common_triplet_count": accepted,
                "candidate_station_time_count": candidate,
                "excluded_station_time_count": excluded,
            }
        evidence[season] = {
            "matched_endpoint_count": len(matched_times),
            "first_matched_endpoint": matched_times[0].isoformat(),
            "last_matched_endpoint": matched_times[-1].isoformat(),
            "physical_leads": list(REQUIRED_WIND_PHYSICAL_LEADS),
            "normalized_leads": list(range(1, 25)),
            "common_triplet_reconciliation": reconciled,
        }
    return evidence


def material_wind_change(hicar_rmse: float, rea_l_rmse: float) -> dict:
    """Classify one preregistered HICAR-minus-REA-L wind RMSE difference."""
    if not all(math.isfinite(value) and value >= 0.0 for value in (hicar_rmse, rea_l_rmse)):
        raise ValueError("wind decision RMSE values must be finite and nonnegative")
    delta = hicar_rmse - rea_l_rmse
    threshold = max(0.10, 0.05 * rea_l_rmse)
    classification = (
        "material_improvement"
        if delta < -threshold - TIE_TOLERANCE
        else "material_degradation"
        if delta > threshold + TIE_TOLERANCE
        else "neutral"
    )
    return {
        "hicar_rmse_m_s": hicar_rmse,
        "rea_l_rmse_m_s": rea_l_rmse,
        "delta_hicar_minus_rea_l_m_s": delta,
        "material_threshold_m_s": threshold,
        "classification": classification,
    }


def joint_wind_event_classification(vector: str, speed: str) -> str:
    """Combine primary vector and co-primary speed material classifications."""
    allowed = {"material_improvement", "neutral", "material_degradation"}
    if vector not in allowed or speed not in allowed:
        raise ValueError("unknown wind material classification")
    if vector == "material_degradation":
        return "degraded"
    if vector == "material_improvement":
        return {
            "material_improvement": "strong",
            "neutral": "qualified",
            "material_degradation": "mixed",
        }[speed]
    return {
        "material_improvement": "mixed",
        "neutral": "neutral",
        "material_degradation": "degraded",
    }[speed]


def delta_direction(value: float) -> str:
    if value < -TIE_TOLERANCE:
        return "improving"
    if value > TIE_TOLERANCE:
        return "degrading"
    return "neutral"


def wind_decision_readout(rows: list[dict]) -> dict:
    """Apply the fixed four-event wind added-value rule without significance claims."""
    required_fields = {
        "season",
        "event_name",
        "station_key",
        "station_elevation_m",
        "terrain_class",
        "metric",
        "pair_count",
        "hicar_rmse",
        "rea_l_rmse",
        "rmse_delta_hicar_minus_rea_l",
        "outcome",
    }
    wind_rows = [row for row in rows if row.get("metric") in WIND_METRICS]
    if not wind_rows:
        raise ValueError("wind decision requires station-event wind rows")
    for index, row in enumerate(wind_rows):
        absent = sorted(required_fields - set(row))
        if absent:
            raise ValueError(
                f"wind decision row {index} lacks required fields: {', '.join(absent)}"
            )
        try:
            nonnegative_count(row["pair_count"], f"wind decision row {index} pair_count")
            hicar_rmse = float(row["hicar_rmse"])
            rea_l_rmse = float(row["rea_l_rmse"])
            supplied_delta = float(row["rmse_delta_hicar_minus_rea_l"])
        except (TypeError, ValueError) as error:
            raise ValueError(f"wind decision row {index} has invalid numeric evidence") from error
        if not all(
            math.isfinite(value) and value >= 0.0 for value in (hicar_rmse, rea_l_rmse)
        ) or not math.isfinite(supplied_delta):
            raise ValueError(f"wind decision row {index} has nonfinite or negative RMSE evidence")
        expected_delta = hicar_rmse - rea_l_rmse
        if not math.isclose(supplied_delta, expected_delta, rel_tol=0.0, abs_tol=TIE_TOLERANCE):
            raise ValueError(f"wind decision row {index} supplied RMSE delta is inconsistent")
        expected_outcome = (
            "improved"
            if expected_delta < -TIE_TOLERANCE
            else "degraded"
            if expected_delta > TIE_TOLERANCE
            else "tied"
        )
        if row["outcome"] != expected_outcome:
            raise ValueError(f"wind decision row {index} outcome is inconsistent with RMSE delta")
    grain = [
        (row["season"], row["event_name"], row["station_key"], row["metric"]) for row in wind_rows
    ]
    if len(grain) != len(set(grain)):
        raise ValueError("wind decision rows are not unique at station-event-metric grain")
    if {row["season"] for row in wind_rows} != set(SEASONS):
        raise ValueError("wind decision requires exactly one event in each season")
    event_names = {
        season: {row["event_name"] for row in wind_rows if row["season"] == season}
        for season in SEASONS
    }
    if any(len(names) != 1 for names in event_names.values()):
        raise ValueError("wind decision requires one event_name per season")
    if len(set.union(*event_names.values())) != len(SEASONS):
        raise ValueError("wind decision event_name values must be unique across seasons")

    eligible_station_sets = {
        (season, metric): {
            row["station_key"]
            for row in wind_rows
            if row["season"] == season
            and row["metric"] == metric
            and row["pair_count"] == REQUIRED_WIND_EVENT_PAIR_COUNT
        }
        for season in SEASONS
        for metric in WIND_METRICS
    }
    cohort = set.intersection(*eligible_station_sets.values())
    if len(cohort) < MINIMUM_NATIONAL_WIND_STATIONS:
        raise ValueError(
            f"wind decision fixed four-event/two-metric cohort has {len(cohort)} "
            f"stations; requires at least {MINIMUM_NATIONAL_WIND_STATIONS}"
        )
    decision_rows = [row for row in wind_rows if row["station_key"] in cohort]
    expected_grain = {
        (season, next(iter(event_names[season])), station_key, metric)
        for season in SEASONS
        for station_key in cohort
        for metric in WIND_METRICS
    }
    decision_grain = {
        (row["season"], row["event_name"], row["station_key"], row["metric"])
        for row in decision_rows
    }
    if decision_grain != expected_grain or len(decision_rows) != len(expected_grain):
        raise ValueError("wind decision fixed cohort is incomplete at station-event-metric grain")
    for season in SEASONS:
        for station_key in cohort:
            station_rows = [
                row
                for row in decision_rows
                if row["season"] == season and row["station_key"] == station_key
            ]
            pair_counts = {row["metric"]: row["pair_count"] for row in station_rows}
            if pair_counts != {metric: REQUIRED_WIND_EVENT_PAIR_COUNT for metric in WIND_METRICS}:
                raise ValueError(
                    f"{season}/{station_key}: vector and speed must each have exactly "
                    f"{REQUIRED_WIND_EVENT_PAIR_COUNT} common ending-hour pairs"
                )
    for station_key in cohort:
        metadata = {
            (float(row["station_elevation_m"]), row["terrain_class"])
            for row in decision_rows
            if row["station_key"] == station_key
        }
        if len(metadata) != 1:
            raise ValueError(f"wind decision fixed-cohort metadata changes for {station_key}")

    event_evidence = []
    metric_statuses = {metric: [] for metric in WIND_METRICS}
    for season in SEASONS:
        by_metric = {
            metric: [
                row for row in decision_rows if row["season"] == season and row["metric"] == metric
            ]
            for metric in WIND_METRICS
        }
        station_count = len(cohort)
        metrics = {}
        for metric, metric_rows in by_metric.items():
            summary = summarize_group(metric_rows)
            evidence = material_wind_change(
                summary["equal_station_network_hicar_rmse"],
                summary["equal_station_network_rea_l_rmse"],
            )
            evidence.update(
                {
                    "paired_station_count": summary["paired_station_count"],
                    "pair_count_total": summary["pair_count_total"],
                    "median_station_delta_m_s": summary[
                        "median_station_rmse_delta_hicar_minus_rea_l"
                    ],
                }
            )
            metrics[metric] = evidence
            metric_statuses[metric].append(evidence["classification"])
        event_evidence.append(
            {
                "season": season,
                "event_name": next(iter(event_names[season])),
                "paired_station_count": station_count,
                "metrics": metrics,
                "classification": joint_wind_event_classification(
                    metrics["wind_vector"]["classification"],
                    metrics["wind_speed_10m_m_s"]["classification"],
                ),
            }
        )

    station_event = {}
    leave_one_event_out = {}
    for metric in WIND_METRICS:
        metric_rows = [row for row in decision_rows if row["metric"] == metric]
        deltas = [float(row["rmse_delta_hicar_minus_rea_l"]) for row in metric_rows]
        if not all(math.isfinite(value) for value in deltas):
            raise ValueError(f"wind decision {metric} station-event deltas are nonfinite")
        station_median = median(deltas)
        station_event[metric] = {
            "station_event_count": len(deltas),
            "median_delta_hicar_minus_rea_l_m_s": station_median,
            "median_direction": delta_direction(station_median),
            "minimum_delta_m_s": min(deltas),
            "maximum_delta_m_s": max(deltas),
        }
        omitted = []
        for season in SEASONS:
            remaining = [
                float(row["rmse_delta_hicar_minus_rea_l"])
                for row in metric_rows
                if row["season"] != season
            ]
            omitted_median = median(remaining)
            omitted.append(
                {
                    "omitted_season": season,
                    "station_event_count": len(remaining),
                    "median_delta_hicar_minus_rea_l_m_s": omitted_median,
                    "direction": delta_direction(omitted_median),
                    "nondegrading": omitted_median <= TIE_TOLERANCE,
                }
            )
        leave_one_event_out[metric] = {
            "all_omissions_nondegrading": all(row["nondegrading"] for row in omitted),
            "omissions": omitted,
        }

    safeguard_evidence = []
    repeated_regression = False
    vector_rows = [row for row in decision_rows if row["metric"] == "wind_vector"]
    for stratum, predicate in WIND_SAFEGUARDS.items():
        events = []
        for season in SEASONS:
            stratum_rows = [
                row for row in vector_rows if row["season"] == season and predicate(row)
            ]
            if len(stratum_rows) < MINIMUM_SAFEGUARD_STATIONS:
                raise ValueError(
                    f"{season}/{stratum}: safeguard has {len(stratum_rows)} paired "
                    f"stations; requires at least {MINIMUM_SAFEGUARD_STATIONS}"
                )
            summary = summarize_group(stratum_rows)
            evidence = material_wind_change(
                summary["equal_station_network_hicar_rmse"],
                summary["equal_station_network_rea_l_rmse"],
            )
            events.append(
                {
                    "season": season,
                    "event_name": next(iter(event_names[season])),
                    "paired_station_count": len(stratum_rows),
                    **evidence,
                }
            )
        degradation_count = sum(
            event["classification"] == "material_degradation" for event in events
        )
        stratum_repeated = degradation_count >= 2
        repeated_regression = repeated_regression or stratum_repeated
        safeguard_evidence.append(
            {
                "stratum": stratum,
                "minimum_required_stations_per_event": MINIMUM_SAFEGUARD_STATIONS,
                "material_degradation_event_count": degradation_count,
                "repeated_broad_regression": stratum_repeated,
                "status": "fail" if stratum_repeated else "pass",
                "events": events,
            }
        )

    event_counts = {}
    for metric, statuses in metric_statuses.items():
        event_counts[metric] = {
            "material_improvement": statuses.count("material_improvement"),
            "neutral": statuses.count("neutral"),
            "material_degradation": statuses.count("material_degradation"),
            "nondegradation": sum(status != "material_degradation" for status in statuses),
        }
    joint_event_counts = {
        status: sum(event["classification"] == status for event in event_evidence)
        for status in ("strong", "qualified", "neutral", "mixed", "degraded")
    }
    vector_gate = (
        event_counts["wind_vector"]["nondegradation"] >= 3
        and event_counts["wind_vector"]["material_improvement"] >= 2
        and station_event["wind_vector"]["median_direction"] == "improving"
        and leave_one_event_out["wind_vector"]["all_omissions_nondegrading"]
        and not repeated_regression
    )
    speed_gate = (
        event_counts["wind_speed_10m_m_s"]["nondegradation"] >= 3
        and event_counts["wind_speed_10m_m_s"]["material_improvement"] >= 2
        and station_event["wind_speed_10m_m_s"]["median_direction"] == "improving"
    )
    speed_supports_qualified = (
        event_counts["wind_speed_10m_m_s"]["material_degradation"] == 0
        and station_event["wind_speed_10m_m_s"]["median_direction"] != "degrading"
    )
    all_materially_neutral = all(
        counts["material_improvement"] == 0 and counts["material_degradation"] == 0
        for counts in event_counts.values()
    )
    repeated_vector_or_safeguard_degradation = (
        repeated_regression or event_counts["wind_vector"]["material_degradation"] >= 2
    )
    classification = (
        "degraded"
        if repeated_vector_or_safeguard_degradation
        else "strong"
        if vector_gate and speed_gate and joint_event_counts["strong"] >= 2
        else "qualified"
        if vector_gate and speed_supports_qualified
        else "neutral"
        if all_materially_neutral
        else "mixed"
    )
    interpolation_control_required = classification in {
        "degraded",
        "neutral",
        "mixed",
    }
    return {
        "schema_version": 1,
        "classification": classification,
        "next_action": {
            "interpolation_only_control_required": interpolation_control_required,
            "instruction": (
                "Run the identically sampled interpolation-only control next; "
                "report any safeguard failure and do not open a tuning matrix."
                if interpolation_control_required
                else "The wind decision rule does not trigger an interpolation-only control."
            ),
        },
        "rule": {
            "estimand": (
                "For each event and metric, sqrt(mean over the fixed four-event, "
                "two-metric station cohort of station RMSE squared)"
            ),
            "cohort": (
                "Exact intersection of station keys with 24 common ending-hour pairs "
                "for both wind_vector and wind_speed_10m_m_s in all four events"
            ),
            "source_report_contract": (
                "Each event has exactly 25 ordered inclusive hourly matched_model_times; "
                "lead_time_metrics has physical leads 25..48, normalized to 1..24"
            ),
            "delta_sign": "negative favors HICAR",
            "material_threshold": "max(0.10 m s-1, 0.05 * REA-L RMSE)",
            "threshold_boundary": "absolute delta equal to the threshold is neutral",
            "event_classifications": {
                "strong": "material vector and speed improvement",
                "qualified": "material vector improvement and neutral speed",
                "neutral": "neutral vector and speed",
                "mixed": "vector improvement with speed degradation, or neutral vector with speed improvement",
                "degraded": "material vector degradation, or neutral vector with speed degradation",
            },
            "campaign_classifications": {
                "strong": (
                    "vector and speed each pass replicated-improvement gates, at "
                    "least two events are jointly strong, and safeguards pass"
                ),
                "qualified": "vector gate passes; speed has no material degradation and nondegrading median",
                "neutral": "all eight event-metric changes are materially neutral",
                "degraded": "vector degradation in at least two events or a repeated vector safeguard regression",
                "mixed": "all other combinations",
            },
            "required_event_counts": {
                "event_count": 4,
                "common_ending_hour_pairs_per_station_event_metric": REQUIRED_WIND_EVENT_PAIR_COUNT,
                "vector_nondegradation_minimum": 3,
                "vector_material_improvement_minimum": 2,
                "vector_leave_one_event_out_nondegradation_minimum": 4,
                "speed_nondegradation_minimum_for_strong": 3,
                "speed_material_improvement_minimum_for_strong": 2,
                "joint_strong_event_minimum_for_strong": 2,
            },
            "median_rule": "raw station-event median; negative is improving; no significance inference",
            "safeguard_rule": (
                "For vector RMSE, material broad degradation in the same sufficiently "
                "populated fixed-cohort stratum in at least two events fails the safeguard"
            ),
            "minimum_fixed_cohort_stations": MINIMUM_NATIONAL_WIND_STATIONS,
            "minimum_national_stations_per_event": MINIMUM_NATIONAL_WIND_STATIONS,
            "minimum_safeguard_stations_per_event": MINIMUM_SAFEGUARD_STATIONS,
            "leave_one_event_out_role": (
                "All four vector leave-one-event-out station-event medians must be "
                "nondegrading for the vector gate; speed omissions are diagnostic"
            ),
        },
        "cohort": {
            "station_count": len(cohort),
            "station_keys": sorted(cohort),
            "excluded_station_counts_by_event_and_metric": {
                f"{season}:{metric}": len(eligible_station_sets[(season, metric)] - cohort)
                for season in SEASONS
                for metric in WIND_METRICS
            },
        },
        "event_counts": event_counts,
        "joint_event_counts": joint_event_counts,
        "requirements": {
            "vector_nondegradation": {
                "observed": event_counts["wind_vector"]["nondegradation"],
                "required": 3,
                "passes": event_counts["wind_vector"]["nondegradation"] >= 3,
            },
            "vector_material_improvement": {
                "observed": event_counts["wind_vector"]["material_improvement"],
                "required": 2,
                "passes": event_counts["wind_vector"]["material_improvement"] >= 2,
            },
            "negative_vector_station_event_median": {
                "observed_direction": station_event["wind_vector"]["median_direction"],
                "passes": station_event["wind_vector"]["median_direction"] == "improving",
            },
            "vector_leave_one_event_out_nondegradation": {
                "observed": sum(
                    row["nondegrading"] for row in leave_one_event_out["wind_vector"]["omissions"]
                ),
                "required": 4,
                "passes": leave_one_event_out["wind_vector"]["all_omissions_nondegrading"],
            },
            "safeguards": {"passes": not repeated_regression},
            "vector_gate_passes": vector_gate,
            "speed_gate_passes_for_strong": speed_gate,
            "joint_strong_event_minimum_for_strong": {
                "observed": joint_event_counts["strong"],
                "required": 2,
                "passes": joint_event_counts["strong"] >= 2,
            },
            "speed_supports_qualified": speed_supports_qualified,
        },
        "event_evidence": event_evidence,
        "station_event_evidence": station_event,
        "leave_one_event_out": leave_one_event_out,
        "safeguards": {
            "status": "fail" if repeated_regression else "pass",
            "strata": safeguard_evidence,
        },
    }


def selected_site_listings(rows: list[dict], metadata: dict[str, dict], worst_count: int) -> dict:
    wind_metrics = {"wind_speed_10m_m_s", "wind_vector"}
    wind_rows = [row for row in rows if row["metric"] in wind_metrics]

    def site_records(predicate) -> list[dict]:
        keys = sorted(key for key, site in metadata.items() if predicate(site))
        return [
            {
                **metadata[key],
                "station_season_wind_metrics": [
                    row for row in wind_rows if row["station_key"] == key
                ],
            }
            for key in keys
        ]

    worst: dict[str, dict[str, list[dict]]] = {}
    for season in SEASONS:
        worst[season] = {}
        for metric in sorted({row["metric"] for row in rows}):
            candidates = [
                row for row in rows if row["season"] == season and row["metric"] == metric
            ]
            worst[season][metric] = sorted(
                candidates,
                key=lambda row: row["rmse_delta_hicar_minus_rea_l"],
                reverse=True,
            )[:worst_count]
    return {
        "station_elevation_ge_3000m": site_records(
            lambda site: float(site["station_elevation_m"]) >= 3000.0
        ),
        "terrain_ridge_relative_gt_150m": site_records(
            lambda site: float(site.get("terrain_relative_elevation_m", math.nan)) > 150.0
        ),
        "worst_hicar_minus_rea_l_by_event_and_metric": worst,
    }


def atomic_write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def atomic_write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0]) if rows else []
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        temporary = Path(stream.name)
    os.replace(temporary, path)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "--report",
        action="append",
        required=True,
        type=parse_report_spec,
        metavar="SEASON=PATH",
        help="one national evaluator JSON for each of DJF, MAM, JJA, and SON",
    )
    result.add_argument("--output-csv", required=True, type=Path)
    result.add_argument("--output-summary", required=True, type=Path)
    result.add_argument(
        "--common-65-report",
        action="append",
        type=Path,
        default=[],
        help="one of four historical evaluator reports whose exact intersection is 65 sites",
    )
    result.add_argument(
        "--common-65-site-file",
        type=Path,
        help="reviewed alternative: one exact station key per line (must contain 65)",
    )
    result.add_argument(
        "--metric",
        action="append",
        help="metric to retain; repeat as needed (default: every metric with RMSE)",
    )
    result.add_argument("--worst-count", type=int, default=5)
    return result


def run(args: argparse.Namespace) -> dict:
    if args.worst_count < 1:
        raise ValueError("--worst-count must be positive")
    specs: dict[str, Path] = {}
    for season, path in args.report:
        if season in specs:
            raise ValueError(f"duplicate report for {season}")
        specs[season] = path
    if set(specs) != set(SEASONS):
        raise ValueError(f"reports must cover exactly {', '.join(SEASONS)}")

    reports = {season: (specs[season], load_report(specs[season])) for season in SEASONS}
    wind_source_evidence = validate_wind_source_reports(reports)
    common_keys, common_provenance = load_common_keys(
        args.common_65_report, args.common_65_site_file
    )
    if common_keys is not None:
        for season, (_, report) in reports.items():
            absent = sorted(common_keys - set(report["site_metrics"]))
            if absent:
                raise ValueError(
                    f"{season} national report lacks {len(absent)} common-65 keys: {absent[:5]}"
                )

    selected_metrics = set(args.metric) if args.metric else None
    rows, station_exclusions, metadata = station_season_rows(reports, common_keys, selected_metrics)
    if not rows:
        raise ValueError("no valid paired station-season RMSE rows")
    wind_rows, wind_exclusions, _ = station_season_rows(reports, common_keys, set(WIND_METRICS))
    wind_decision = wind_decision_readout(wind_rows)
    wind_decision["data_quality"] = {
        "station_event_exclusions": wind_exclusions,
        "source_report_contract": wind_source_evidence,
        "population_policy": (
            "The decision cohort is the exact four-event/two-metric intersection. "
            "Every retained station-event metric has 24 common pairs; source reports "
            "prove 25 inclusive endpoints and physical leads 25..48."
        ),
    }
    lead_tables, lead_exclusions = lead_hour_tables(reports, selected_metrics)
    coverage = {
        season: {
            "event_name": report["event_name"],
            "station_count": len(report["site_metrics"]),
            "matched_model_time_count": len(report["matched_model_times"]),
            "first_matched_model_time": report["matched_model_times"][0]
            if report["matched_model_times"]
            else None,
            "last_matched_model_time": report["matched_model_times"][-1]
            if report["matched_model_times"]
            else None,
        }
        for season, (_, report) in reports.items()
    }
    national_key_sets = [set(report["site_metrics"]) for _, report in reports.values()]
    national_four_season_keys = set.intersection(*national_key_sets)
    row_metrics = sorted({row["metric"] for row in rows})
    national_metric_four_season_keys = {
        metric: set.intersection(
            *[
                {
                    row["station_key"]
                    for row in rows
                    if row["season"] == season and row["metric"] == metric
                }
                for season in SEASONS
            ]
        )
        for metric in row_metrics
    }
    common_provenance["site_keys"] = sorted(common_keys) if common_keys is not None else []
    summary = {
        "schema_version": 1,
        "method": {
            "station_grain": "one station-season-metric row",
            "comparison": "RMSE(HICAR, observation) minus RMSE(REA-L, observation)",
            "pairing_rule": (
                "retain only equal HICAR and REA-L pair counts of at least "
                f"{MINIMUM_STATION_EVENT_PAIRS} within each evaluator "
                "station/metric aggregate"
            ),
            "pairing_limitation": (
                "Equal aggregate counts do not independently prove identical valid-time "
                "sets; exact timestamps remain owned by the upstream evaluator."
            ),
            "mean_and_bias_aggregation": (
                "Model means, observation means, and biases use the same eligible "
                "station rows as RMSE and are averaged with equal station weight. "
                "They are retained only when HICAR and REA-L report the same finite "
                "observation mean for that station aggregate."
            ),
            "aggregation": (
                "Mean-station RMSE is the arithmetic mean of station RMSEs. "
                "Equal-station network RMSE is sqrt(mean_i(RMSE_i^2)), giving "
                "each eligible station equal weight. Network-pooled RMSE is "
                "reconstructed separately as "
                "sqrt(sum(n_i * RMSE_i^2) / sum(n_i)), where n_i is the eligible "
                "paired count for station i."
            ),
            "error_anatomy_decomposition": (
                "For each model, equal-station network RMSE squared equals the "
                "mean squared station bias plus the mean squared within-station "
                "centered RMSE. Signed network bias is reported separately; it "
                "is not used to remove opposing persistent station biases."
            ),
            "national_four_season_intersection": (
                "For each metric, the exact intersection of stations with eligible "
                "paired HICAR/REA-L aggregates in all four seasons. Raw station-key "
                "coverage intersection is reported separately."
            ),
            "lead_hour_aggregation": (
                "Lead-hour rows reproduce the evaluator's pooled-pair RMSE for every "
                "reported spatial stratum; they are not equal-station averages. "
                "lead_hour is elapsed time from the evaluation-window start, while "
                "physical_lead_hour preserves elapsed time from simulation start."
            ),
            "rmse_delta_sign": "negative improves on REA-L; positive degrades",
            "wind_decision": wind_decision["rule"],
            "terrain_ridge_definition": (
                "HICAR terrain exceeds the median in an approximately 10-km-wide "
                "square (5-km half-width) by 150 m"
            ),
        },
        "inputs": [
            {
                "season": season,
                "path": str(path.resolve()),
                "sha256": file_sha256(path),
            }
            for season, (path, _) in reports.items()
        ],
        "coverage": {
            "events": coverage,
            "station_key_union_count": len(set.union(*national_key_sets)),
            "station_key_four_season_intersection_count": len(national_four_season_keys),
            "metric_eligible_four_season_intersection_counts": {
                metric: len(keys) for metric, keys in national_metric_four_season_keys.items()
            },
        },
        "data_quality": {
            "station_season_exclusions": station_exclusions,
            "lead_hour_exclusions": lead_exclusions,
            "report_issue_policy": "reports with nonempty issues are rejected",
        },
        "station_season_csv": str(args.output_csv.resolve()),
        "station_season_row_count": len(rows),
        "metrics": sorted({row["metric"] for row in rows}),
        "wind_decision_readout": wind_decision,
        "equal_station_summaries": equal_station_summaries(
            rows, common_keys is not None, national_metric_four_season_keys
        ),
        "lead_hour_tables": lead_tables,
        "national_four_season_intersection": {
            "interpretation": (
                "Backward-compatible raw station-key intersection before metric "
                "eligibility; use national_metric_four_season_intersections for "
                "same-station seasonal metric comparisons."
            ),
            "site_count": len(national_four_season_keys),
            "site_keys": sorted(national_four_season_keys),
        },
        "national_metric_four_season_intersections": {
            metric: {
                "site_count": len(keys),
                "site_keys": sorted(keys),
            }
            for metric, keys in national_metric_four_season_keys.items()
        },
        "common_65": common_provenance,
        "selected_site_listings": selected_site_listings(rows, metadata, args.worst_count),
        "footprint_sensitivity": FOOTPRINT_INPUT_CONTRACT,
    }
    atomic_write_csv(args.output_csv, rows)
    atomic_write_json(args.output_summary, summary)
    return summary


def main(argv: Iterable[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        summary = run(args)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser().error(str(error))
    print(
        f"Wrote {summary['station_season_row_count']} station-season-metric rows "
        f"and {len(summary['equal_station_summaries'])} equal-station summaries"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
