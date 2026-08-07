#!/usr/bin/env python3
"""Summarize preserved V29 history without rerunning HICAR.

The report is descriptive rather than causal.  It creates the compact evidence
needed to choose one mechanism-based follow-up, while retaining the frozen
summer failure and its no-escalation decision.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import netCDF4
import numpy as np


FIELDS = (
    "taix", "hus2m", "rsds", "hfss", "hfls", "tsfe", "precipitation",
    "snow_height", "soil_column_total_water", "soil_water_content",
)

HYDROMETEOR_MAPPINGS = ("qcvar", "qivar", "qrvar", "qgvar", "qsvar")
RADIATION_MAPPINGS = ("swdown_var", "lwdown_var")
CLOUD_STATE_FIELDS = (
    "cloud_fraction", "cloud_water_mass", "ice_mass", "rain_mass",
    "graupel_mass", "snow_mass",
)


def read_2d(dataset: netCDF4.Dataset, name: str) -> np.ndarray:
    value = np.asarray(dataset.variables[name][:])
    value = np.squeeze(value)
    if value.ndim != 2:
        raise ValueError(f"{name} must be two-dimensional, got {value.shape}")
    return value


def horizontal_mean(value: np.ndarray) -> np.ndarray:
    """Reduce non-horizontal dimensions; output fields end in y,x."""
    value = np.asarray(value, dtype=np.float64)
    if value.ndim < 2:
        raise ValueError(f"expected horizontal dimensions, got {value.shape}")
    return np.nanmean(value, axis=tuple(range(value.ndim - 2))) if value.ndim > 2 else value


def classes(static_file: Path) -> dict[str, np.ndarray]:
    with netCDF4.Dataset(static_file) as dataset:
        land = read_2d(dataset, "landmask") > 0
        landuse = read_2d(dataset, "landuse")
        terrain = read_2d(dataset, "topo")
    active = land & (landuse != 16) & (landuse != 24)
    return {
        "active_soil_lt_1000m": active & (terrain < 1000),
        "active_soil_1000_2000m": active & (terrain >= 1000) & (terrain < 2000),
        "active_soil_ge_2000m": active & (terrain >= 2000),
    }


def stats(value: np.ndarray, mask: np.ndarray) -> dict[str, float | int]:
    selected = value[mask]
    selected = selected[np.isfinite(selected)]
    if not selected.size:
        return {"count": 0}
    return {"count": int(selected.size), "mean": float(np.mean(selected)), "p05": float(np.quantile(selected, .05)), "p95": float(np.quantile(selected, .95))}


def read_assessment(path: Path) -> dict[str, object]:
    report = json.loads(path.read_text())
    return {key: report[key] for key in ("decision", "reason", "failed_science_metrics", "secondary_diagnostics", "authorization") if key in report}


def read_report(path: Path) -> dict[str, object]:
    return json.loads(path.read_text())


def namelist_group(path: Path, name: str) -> dict[str, str]:
    """Read simple key/value assignments from one archived Fortran namelist."""
    values: dict[str, str] = {}
    in_group = False
    for raw_line in path.read_text().splitlines():
        line = raw_line.split("!", 1)[0].strip()
        if line.lower() == f"&{name.lower()}":
            in_group = True
            continue
        if in_group and line == "/":
            break
        if in_group and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip().lower()] = value.strip().rstrip(",")
    if not in_group:
        raise ValueError(f"{path} lacks &{name}")
    return values


def mechanism_separation_evidence(
    input_namelist: Path, present_fields: set[str]
) -> dict[str, object]:
    forcing = namelist_group(input_namelist, "forcing")
    physics = namelist_group(input_namelist, "physics")
    mapped = {
        name: forcing.get(name, "")
        for name in ("pvar", "tvar", "qvvar", "uvar", "vvar", "wvar", *HYDROMETEOR_MAPPINGS, *RADIATION_MAPPINGS)
    }
    absent_hydrometeors = [name for name in HYDROMETEOR_MAPPINGS if not mapped[name]]
    absent_radiation = [name for name in RADIATION_MAPPINGS if not mapped[name]]
    absent_cloud_state = [name for name in CLOUD_STATE_FIELDS if name not in present_fields]
    return {
        "archived_namelist": str(input_namelist.resolve()),
        "physics": {name: physics.get(name, "") for name in ("mp", "rad", "lsm", "pbl")},
        "forcing_mappings": mapped,
        "forcing_path": {
            "hydrometeor_mappings_absent": absent_hydrometeors,
            "downwelling_radiation_mappings_absent": absent_radiation,
            "interpretation": "The archived V29 namelist supplies thermodynamic and wind fields but does not map hydrometeor or downwelling-radiation forcing. It therefore cannot support an attribution to prescribed cloud condensate or prescribed surface radiative fluxes.",
        },
        "temporal_discrimination": {
            "history_cloud_state_fields_absent": absent_cloud_state,
            "frozen_reference_skill_is_aggregate": True,
            "decision": "NOT_SEPARABLE_FROM_RETAINED_ARTIFACTS",
            "reason": "The retained history has model surface state and fluxes but lacks cloud/hydrometeor state, while the frozen reference reports do not retain matched valid-time errors. Model-only co-occurrence cannot establish whether radiative/cloud error or land-surface feedback starts first.",
        },
    }


def metric(report: dict[str, object], *keys: str) -> dict[str, object]:
    current: object = report
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return {"available": False}
        current = current[key]
    return {"available": isinstance(current, dict), "value": current}


def frozen_reference_evidence(
    station: dict[str, object], ogd: dict[str, object], source: dict[str, object]
) -> dict[str, object]:
    """Extract only pre-existing, comparable evidence; never recalculate skill."""
    return {
        "swissmetnet_all_sites": {
            name: metric(station, "metrics", "hicar", "all_sites", name)
            for name in (
                "temperature_2m_height_adjusted_k",
                "relative_humidity_2m_percent",
                "global_shortwave_radiation_w_m2",
                "precipitation_interval_kg_m2",
            )
        },
        "ogd_grid_all": {
            name: metric(ogd, "metrics", name, "hicar", "all")
            for name in ("tabsd", "rhiresd")
        },
        "rea_l_active_soil_interior": {
            name: metric(source, "metrics", "active_soil_interior", name)
            for name in (
                "temperature_2m_height_adjusted_k",
                "specific_humidity_2m",
                "precipitation_interval_kg_m2",
            )
        },
        "radiation_products": radiation_products(ogd),
    }


def radiation_products(ogd: dict[str, object]) -> dict[str, object]:
    metrics = ogd.get("metrics", {})
    if not isinstance(metrics, dict):
        return {}
    # The OGD comparator stores both products inside the SIS comparison for
    # this event.  Retain a fallback for reports that expose them separately.
    grouped = metrics.get("sis", {})
    if isinstance(grouped, dict) and {"sis", "sis_no_horizon"} & set(grouped):
        products = {name: grouped.get(name, {}) for name in ("sis", "sis_no_horizon")}
    else:
        products = {name: metrics.get(name, {}) for name in ("sis", "sis_no_horizon")}

    sis = products.get("sis", {})
    no_horizon = products.get("sis_no_horizon", {})
    bias_delta: dict[str, float] = {}
    if isinstance(sis, dict) and isinstance(no_horizon, dict):
        for scope in sorted(set(sis) & set(no_horizon)):
            with_sis = sis[scope]
            without_horizon = no_horizon[scope]
            if (
                isinstance(with_sis, dict)
                and isinstance(without_horizon, dict)
                and isinstance(with_sis.get("bias"), (float, int))
                and isinstance(without_horizon.get("bias"), (float, int))
            ):
                bias_delta[scope] = float(with_sis["bias"] - without_horizon["bias"])
    return {
        "products": products,
        "hicar_bias_sis_minus_sis_no_horizon_w_m2": bias_delta,
    }


def ranked_hypotheses() -> list[dict[str, object]]:
    """Fixed interpretation policy: priority is evidence coverage, not proof."""
    return [
        {
            "rank": 1,
            "hypothesis": "Cloud-precipitation-radiative deficit",
            "support": "strong symptom support",
            "evidence_required_in_report": [
                "warm temperature and dry relative humidity at SwissMetNet",
                "positive HICAR shortwave bias",
                "near-zero HICAR precipitation against SwissMetNet, RhiresD, and REA-L",
            ],
            "interpretation": "The combined symptoms are consistent with insufficient cloud/precipitation-mediated radiative attenuation. They do not identify whether forcing, microphysics, radiation, or surface feedback initiates the deficit.",
        },
        {
            "rank": 2,
            "hypothesis": "Surface moisture and turbulent-flux partitioning feedback",
            "support": "medium, pending trajectory stratification",
            "evidence_required_in_report": [
                "daytime hfss/hfls, tsfe, soil water, and snow evolution by elevation class",
                "whether the warm/dry signal grows after initialization rather than being present immediately",
            ],
            "interpretation": "The retained trajectory can establish timing and co-occurrence, but cannot prove a land-surface cause without a controlled experiment.",
        },
        {
            "rank": 3,
            "hypothesis": "Missing terrain-horizon shortwave attenuation",
            "support": "low; SIS versus SIS-No-Horizon is retained for direct quantification",
            "evidence_required_in_report": [
                "HICAR shortwave error against both SIS products by terrain class",
            ],
            "interpretation": "Horizon shading could affect daytime energy locally, but cannot by itself explain the domain-wide precipitation deficit.",
        },
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--static-file", required=True, type=Path)
    parser.add_argument("--history", required=True, type=Path, action="append", help="Preserved V29 NetCDF history; repeat for every file")
    parser.add_argument("--assessment", required=True, type=Path, help="Frozen V29 summer assessment JSON")
    parser.add_argument("--input-namelist", required=True, type=Path, help="Archived V29 model namelist")
    parser.add_argument("--station-report", required=True, type=Path)
    parser.add_argument("--ogd-report", required=True, type=Path)
    parser.add_argument("--source-report", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    ready = args.report.with_suffix(args.report.suffix + ".ready")
    # Withdraw any old publication marker while replacing the report so that a
    # reader never mistakes a stale ready marker for the newly requested run.
    ready.unlink(missing_ok=True)

    masks = classes(args.static_file)
    station_report = read_report(args.station_report)
    ogd_report = read_report(args.ogd_report)
    source_report = read_report(args.source_report)
    by_hour: dict[str, dict[str, dict[str, list[dict[str, float | int]]]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    by_valid_time: dict[str, dict[str, dict[str, list[dict[str, float | int]]]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    present_fields: set[str] = set()
    records = 0
    for path in args.history:
        with netCDF4.Dataset(path) as dataset:
            if "time" not in dataset.variables:
                raise ValueError(f"{path} lacks time")
            time = dataset.variables["time"]
            dates = netCDF4.num2date(time[:], time.units, calendar=getattr(time, "calendar", "standard"))
            available = [name for name in FIELDS if name in dataset.variables]
            for index, date in enumerate(dates):
                records += 1
                hour = f"{int(date.hour):02d}"
                valid_time = (
                    f"{int(date.year):04d}-{int(date.month):02d}-{int(date.day):02d}"
                    f"T{int(date.hour):02d}:00:00"
                )
                for name in available:
                    value = horizontal_mean(dataset.variables[name][index])
                    for class_name, mask in masks.items():
                        summary = stats(value, mask)
                        by_hour[hour][class_name][name].append(summary)
                        by_valid_time[valid_time][class_name][name].append(summary)
            present_fields.update(available)

    def collapse(items: list[dict[str, float | int]]) -> dict[str, float | int]:
        valid = [item for item in items if item["count"]]
        if not valid:
            return {"records": len(items), "count": 0}
        return {
            "records": len(items),
            "count": int(sum(int(item["count"]) for item in valid)),
            "mean_of_record_means": float(np.mean([float(item["mean"]) for item in valid])),
            "mean_p05": float(np.mean([float(item["p05"]) for item in valid])),
            "mean_p95": float(np.mean([float(item["p95"]) for item in valid])),
        }

    result = {
        "schema_version": 2,
        "classification": "ARTIFACT_ONLY_V29_SURFACE_REGIME_DIAGNOSIS",
        "inputs": {
            "static_file": str(args.static_file.resolve()),
            "history": [str(path.resolve()) for path in args.history],
            "assessment": str(args.assessment.resolve()),
            "input_namelist": str(args.input_namelist.resolve()),
            "station_report": str(args.station_report.resolve()),
            "ogd_report": str(args.ogd_report.resolve()),
            "source_report": str(args.source_report.resolve()),
        },
        "history_records": records,
        "available_fields": sorted(present_fields),
        "mechanism_separation": mechanism_separation_evidence(
            args.input_namelist, present_fields
        ),
        "frozen_assessment": read_assessment(args.assessment),
        "frozen_reference_evidence": frozen_reference_evidence(
            station_report, ogd_report, source_report
        ),
        "by_utc_hour_and_surface_class": {
            hour: {
                class_name: {name: collapse(items) for name, items in fields.items()}
                for class_name, fields in class_map.items()
            }
            for hour, class_map in sorted(by_hour.items())
        },
        "by_valid_time_and_surface_class": {
            valid_time: {
                class_name: {name: collapse(items) for name, items in fields.items()}
                for class_name, fields in class_map.items()
            }
            for valid_time, class_map in sorted(by_valid_time.items())
        },
        "ranked_mechanism_hypotheses": ranked_hypotheses(),
        "follow_up_experiment": {
            "authorized": False,
            "reason": "No configuration-only candidate is justified: the preserved SIS/SIS-No-Horizon comparison constrains horizon shading, while the remaining cloud/precipitation and surface-feedback alternatives are not separable without new controlled evidence.",
            "maximum_permitted_after_review": 1,
        },
        "interpretation": "Descriptive evidence only. It does not identify a cause, relax an acceptance threshold, authorize a rerun, or replace the frozen summer HOLD decision.",
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.report.with_suffix(args.report.suffix + ".tmp")
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    temporary.replace(args.report)
    digest = hashlib.sha256(args.report.read_bytes()).hexdigest()
    manifest = args.report.with_suffix(args.report.suffix + ".manifest.json")
    manifest_temporary = manifest.with_suffix(manifest.suffix + ".tmp")
    manifest_temporary.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "report": str(args.report.resolve()),
                "report_sha256": digest,
                "classification": result["classification"],
                "history_records": records,
                "model_rerun": False,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    manifest_temporary.replace(manifest)
    ready.touch()
    print(args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
