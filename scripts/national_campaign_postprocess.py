#!/usr/bin/env python3
"""Summarize four national SwissMetNet HICAR/REA-L evaluator reports.

The evaluator reports contain aggregate errors at each station and lead hour.
Consequently, this program can make both equal-station comparisons and
pair-count-weighted network-pooled RMSE reconstructions without opening large
HICAR files.  It deliberately excludes a station/metric when HICAR and REA-L
do not have the same positive pair count.

Footprint sensitivity cannot be reconstructed from evaluator aggregates.  The
output JSON therefore records the exact row-level/model-file contract needed by
a later implementation that streams one HICAR timestep at a time.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from hashlib import sha256
import json
import math
import os
from pathlib import Path
from statistics import mean, median
from tempfile import NamedTemporaryFile
from typing import Iterable


SEASONS = ("DJF", "MAM", "JJA", "SON")
SOURCES = ("hicar", "rea_l")
TIE_TOLERANCE = 1.0e-12

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
    return {
        "pair_count": h_count,
        "hicar_rmse": h_rmse,
        "rea_l_rmse": r_rmse,
        "rmse_delta_hicar_minus_rea_l": delta,
        "outcome": outcome,
        **moments,
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
    equal_station_network_hicar_rmse = math.sqrt(
        mean(row["hicar_rmse"] ** 2 for row in rows)
    )
    equal_station_network_rea_l_rmse = math.sqrt(
        mean(row["rea_l_rmse"] ** 2 for row in rows)
    )
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
        if row["station_key"] in national_metric_four_season_keys.get(
            row["metric"], set()
        ):
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
        season_rows = []
        for raw_hour, values in sorted(
            report["lead_time_metrics"].items(), key=lambda item: int(item[0])
        ):
            hicar_strata = values.get("hicar", {})
            rea_l_strata = values.get("rea_l", {})
            for stratum in sorted(set(hicar_strata) & set(rea_l_strata)):
                hicar = hicar_strata[stratum]
                rea_l = rea_l_strata[stratum]
                metrics = set(hicar) & set(rea_l)
                if selected_metrics is not None:
                    metrics &= selected_metrics
                for metric in sorted(metrics):
                    comparison, reason = comparison_row(
                        hicar[metric], rea_l[metric], metric
                    )
                    if comparison is None:
                        exclusions[
                            f"{reason}:{season}:{raw_hour}:{stratum}:{metric}"
                        ] += 1
                        continue
                    season_rows.append(
                        {
                            "lead_hour": int(raw_hour),
                            "stratum": stratum,
                            "metric": metric,
                            **comparison,
                        }
                    )
        output[season] = season_rows
    return output, dict(sorted(exclusions.items()))


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
                "retain only positive, equal HICAR and REA-L pair counts within "
                "each evaluator station/metric aggregate"
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
            "national_four_season_intersection": (
                "For each metric, the exact intersection of stations with eligible "
                "paired HICAR/REA-L aggregates in all four seasons. Raw station-key "
                "coverage intersection is reported separately."
            ),
            "lead_hour_aggregation": (
                "Lead-hour rows reproduce the evaluator's pooled-pair RMSE for every "
                "reported spatial stratum; they are not equal-station averages."
            ),
            "rmse_delta_sign": "negative improves on REA-L; positive degrades",
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
                metric: len(keys)
                for metric, keys in national_metric_four_season_keys.items()
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
