#!/usr/bin/env python3
"""Assess a matched hicarprep SMI/relative-saturation land-response experiment."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

import netCDF4
import numpy as np


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from preprocessing.hicarprep.surface import parse_noahmp_stas_hydraulics  # noqa: E402


METHODS = ("smi", "relative_saturation")
PAIR_FIELDS = (
    "soil_water_content", "soil_water_content_liq", "soil_temperature",
    "soil_column_total_water", "swet", "snow_height", "tsfe", "taix",
    "hus2m", "u10m", "v10m", "hpbl", "hfss", "hfls",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_mapping(value: str) -> tuple[str, Path]:
    try:
        method, path = value.split("=", 1)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("mapping must be METHOD=PATH") from exc
    if method not in METHODS:
        raise argparse.ArgumentTypeError(f"method must be one of {METHODS}")
    return method, Path(path)


def decoded_times(dataset: netCDF4.Dataset) -> list[dt.datetime]:
    variable = dataset["time"]
    values = netCDF4.num2date(
        variable[:], variable.units,
        calendar=getattr(variable, "calendar", "standard"),
        only_use_cftime_datetimes=False, only_use_python_datetimes=True,
    )
    result = []
    for value in np.ravel(values):
        value = value.replace(tzinfo=dt.timezone.utc)
        nearest = value.replace(microsecond=0)
        if abs((value - nearest).total_seconds()) <= 1.0:
            value = nearest
        result.append(value)
    return result


def records(run: Path) -> dict[dt.datetime, tuple[Path, int]]:
    result: dict[dt.datetime, tuple[Path, int]] = {}
    for path in sorted((run / "output").glob("*.nc")):
        with netCDF4.Dataset(path) as dataset:
            for index, timestamp in enumerate(decoded_times(dataset)):
                if timestamp in result:
                    raise ValueError(f"duplicate output time {timestamp} in {run}")
                result[timestamp] = (path, index)
    return result


def read_record(path: Path, index: int, name: str) -> np.ndarray:
    with netCDF4.Dataset(path) as dataset:
        if name not in dataset.variables:
            raise ValueError(f"{path}: missing required diagnostic {name}")
        variable = dataset[name]
        values = np.ma.asarray(variable[:], dtype=np.float64)
        if "time" in variable.dimensions:
            values = np.take(values, index, axis=variable.dimensions.index("time"))
        if np.ma.count_masked(values):
            values = values.filled(np.nan)
        return np.asarray(values, dtype=np.float64)


def stats(values: np.ndarray, mask: np.ndarray) -> dict[str, float | int]:
    selected = np.asarray(values[np.broadcast_to(mask, values.shape)], dtype=np.float64)
    selected = selected[np.isfinite(selected)]
    if selected.size == 0:
        raise ValueError("diagnostic has no finite support")
    return {
        "count": int(selected.size),
        "minimum": float(np.min(selected)),
        "p01": float(np.quantile(selected, 0.01)),
        "p50": float(np.quantile(selected, 0.5)),
        "p99": float(np.quantile(selected, 0.99)),
        "maximum": float(np.max(selected)),
        "mean": float(np.mean(selected)),
        "absolute_p99": float(np.quantile(np.abs(selected), 0.99)),
    }


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def verify_run(run: Path, method: str, definition: dict) -> dict:
    failures: list[str] = []
    for marker in (run / "model.out.ready", run / "solver_report.json.ready"):
        if not marker.is_file():
            raise ValueError(f"unpublished run evidence: {marker}")
    solver = json.loads((run / "solver_report.json").read_text())
    if solver.get("status") != "PASS":
        failures.append("solver/conservation report is not PASS")
    executable_line = (run / "executable.sha256").read_text().strip().split()[0]
    expected_executable = definition["case"]["hicar_executable_sha256"]
    if executable_line != expected_executable:
        raise ValueError(f"{run}: executable identity differs from frozen definition")
    log = (run / "model.out").read_text(errors="replace")
    commit = re.search(r"Git commit:\s*([0-9a-f]+)", log)
    expected_commit = definition["case"]["hicar_source_commit"]
    if commit is None or not expected_commit.startswith(commit.group(1)):
        raise ValueError(f"{run}: HICAR source identity differs from frozen definition")
    runtime_line = (run / "runtime_domain.sha256").read_text().strip().split(maxsplit=1)
    runtime = Path(runtime_line[1].lstrip("*"))
    if sha256(runtime) != runtime_line[0]:
        raise ValueError(f"{run}: runtime-domain checksum changed")
    with netCDF4.Dataset(runtime) as static:
        if str(getattr(static, "land_state_soil_water_method", "")) != method:
            raise ValueError(f"{run}: runtime domain carries wrong soil-water method")
    return {
        "run": str(run.resolve()),
        "runtime_domain": str(runtime.resolve()),
        "runtime_domain_sha256": runtime_line[0],
        "executable_sha256": executable_line,
        "hicar_source_commit": expected_commit,
        "solver": solver,
        "failures": failures,
    }


def assess_arm(
    method: str,
    run: Path,
    run_records: dict[dt.datetime, tuple[Path, int]],
    definition: dict,
    land: np.ndarray,
    active: np.ndarray,
    dry: np.ndarray,
    maximum: np.ndarray,
) -> dict:
    thresholds = definition["viability_thresholds"]
    start = dt.datetime.fromisoformat(definition["case"]["start"].replace("Z", "+00:00"))
    expected = [
        start + dt.timedelta(seconds=index * definition["case"]["output_interval_seconds"])
        for index in range(definition["case"]["expected_output_records"])
    ]
    failures: list[str] = []
    if list(sorted(run_records)) != expected:
        failures.append("output time axis is not the frozen 13-record half-hour axis")
    first_flux_masks: dict[str, np.ndarray] = {}
    flux_tail: dict[str, list[dict]] = {"hfss": [], "hfls": []}
    series: list[dict] = []
    for timestamp in expected:
        if timestamp not in run_records:
            continue
        path, index = run_records[timestamp]
        elapsed = (timestamp - start).total_seconds()
        values = {name: read_record(path, index, name) for name in PAIR_FIELDS}
        total = values["soil_water_content"]
        liquid = values["soil_water_content_liq"]
        ice = total - liquid
        active3 = np.broadcast_to(active, total.shape)
        finite_fields = {
            name: array
            for name, array in values.items()
            if name not in {"hpbl"} or array.size
        }
        for name, array in finite_fields.items():
            support = active3 if array.ndim == 3 else land
            selected = array[np.broadcast_to(support, array.shape)]
            if not np.all(np.isfinite(selected)):
                failures.append(f"{timestamp.isoformat()}: {name} is non-finite")
        hydraulic_tolerance = thresholds["hydraulic_tolerance_m3_m3"]
        if np.any(total[active3] < dry[active3] - hydraulic_tolerance):
            failures.append(f"{timestamp.isoformat()}: total soil water below DRYSMC")
        if np.any(total[active3] > maximum[active3] + hydraulic_tolerance):
            failures.append(f"{timestamp.isoformat()}: total soil water above MAXSMC")
        if np.any(liquid[active3] < -hydraulic_tolerance):
            failures.append(f"{timestamp.isoformat()}: liquid soil water is negative")
        if np.any(ice[active3] < -thresholds["maximum_negative_soil_ice_m3_m3"]):
            failures.append(f"{timestamp.isoformat()}: liquid exceeds total soil water")
        near_tolerance = thresholds["near_hydraulic_bound_tolerance_m3_m3"]
        near_bounds = ((total <= dry + near_tolerance) | (total >= maximum - near_tolerance)) & active3
        near_fraction = float(np.count_nonzero(near_bounds) / np.count_nonzero(active3))
        if near_fraction > thresholds["maximum_near_hydraulic_bound_fraction"]:
            failures.append(f"{timestamp.isoformat()}: hydraulic-bound occupancy {near_fraction:.4g} exceeds limit")
        temperature_limits = thresholds["temperature_k"]
        for name in ("soil_temperature", "tsfe", "taix"):
            array = values[name]
            support = active3 if array.ndim == 3 else land
            selected = array[np.broadcast_to(support, array.shape)]
            if np.any((selected < temperature_limits["minimum"]) | (selected > temperature_limits["maximum"])):
                failures.append(f"{timestamp.isoformat()}: {name} outside frozen range")
        for name, contract in (("swet", thresholds["swe_kg_m2"]), ("snow_height", thresholds["snow_height_m"])):
            selected = values[name][land]
            if np.any((selected < contract["minimum"]) | (selected > contract["maximum"])):
                failures.append(f"{timestamp.isoformat()}: {name} outside frozen range")
        flux_diagnostics = {}
        for name in ("hfss", "hfls"):
            selected = np.abs(values[name][land])
            maximum_abs = float(np.max(selected))
            if maximum_abs > thresholds["flux_whole_period_absolute_max_w_m2"]:
                failures.append(f"{timestamp.isoformat()}: {name} exceeds whole-period flux bound")
            exceedance = np.abs(values[name]) > thresholds["flux_tail_threshold_w_m2"]
            exceedance_fraction = float(np.count_nonzero(exceedance & land) / np.count_nonzero(land))
            if elapsed == definition["case"]["output_interval_seconds"]:
                first_flux_masks[name] = exceedance & land
            overlap_fraction = None
            if name in first_flux_masks and np.any(first_flux_masks[name]):
                overlap_fraction = float(
                    np.count_nonzero(exceedance & first_flux_masks[name])
                    / np.count_nonzero(first_flux_masks[name])
                )
            if elapsed >= thresholds["flux_tail_start_seconds"]:
                flux_tail[name].append({
                    "elapsed_seconds": elapsed,
                    "absolute_max_w_m2": maximum_abs,
                    "exceedance_fraction": exceedance_fraction,
                    "startup_extreme_retained_fraction": overlap_fraction,
                })
                if exceedance_fraction > thresholds["flux_tail_maximum_exceedance_fraction"]:
                    failures.append(f"{timestamp.isoformat()}: {name} tail exceedance fraction persists")
                if maximum_abs > thresholds["flux_tail_absolute_max_w_m2"]:
                    failures.append(f"{timestamp.isoformat()}: {name} tail absolute maximum persists")
            flux_diagnostics[name] = {
                "absolute_max_w_m2": maximum_abs,
                "exceedance_fraction": exceedance_fraction,
            }
        series.append({
            "valid_time": timestamp.isoformat(),
            "elapsed_seconds": elapsed,
            "hydraulic_bound_occupancy_fraction": near_fraction,
            "soil_total": stats(total, active3),
            "soil_liquid": stats(liquid, active3),
            "soil_ice": stats(ice, active3),
            "soil_temperature": stats(values["soil_temperature"], active3),
            "surface_temperature": stats(values["tsfe"], land),
            "temperature_2m": stats(values["taix"], land),
            "swe": stats(values["swet"], land),
            "flux": flux_diagnostics,
        })
    return {
        "method": method,
        "status": "FAIL_VIABILITY" if failures else "PASS_VIABILITY",
        "failures": sorted(set(failures)),
        "time_series": series,
        "flux_tail_persistence": flux_tail,
    }


def source_gate(path: Path, thresholds: dict) -> dict:
    report = json.loads(path.read_text())
    failures = []
    if report.get("status") != "PASS":
        failures.append("source comparison report is not PASS")
    metrics = report.get("metrics", {}).get("active_soil_all", {})
    for name, contract in thresholds.items():
        item = metrics.get(name, {})
        if not item:
            failures.append(f"source comparison lacks {name}")
            continue
        if abs(item["bias"]) > contract["maximum_absolute_bias"]:
            failures.append(f"{name} absolute bias exceeds frozen limit")
        if item["root_mean_squared_error"] > contract["maximum_rmse"]:
            failures.append(f"{name} RMSE exceeds frozen limit")
    return {"path": str(path.resolve()), "sha256": sha256(path), "failures": failures, "metrics": metrics}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--definition", type=Path, required=True)
    parser.add_argument("--run", type=parse_mapping, action="append", required=True)
    parser.add_argument("--source-report", type=parse_mapping, action="append", required=True)
    parser.add_argument("--noahmp-table", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    definition = json.loads(args.definition.read_text())
    runs = dict(args.run)
    source_reports = dict(args.source_report)
    if set(runs) != set(METHODS) or set(source_reports) != set(METHODS):
        raise SystemExit("provide exactly one run and source report for each method")
    provenance = {method: verify_run(path, method, definition) for method, path in runs.items()}
    runtime = Path(provenance["smi"]["runtime_domain"])
    with netCDF4.Dataset(runtime) as static:
        land = np.asarray(static["landmask"][:]) >= 0.5
        active = land & (np.asarray(static["landuse"][:]) != 24)
        soil_type = np.asarray(static["soil_type_layer"][:], dtype=np.int64)
    hydraulics = parse_noahmp_stas_hydraulics(args.noahmp_table)
    lookup = np.clip(soil_type - 1, 0, 18)
    dry = hydraulics["DRYSMC"][lookup]
    maximum = hydraulics["MAXSMC"][lookup]
    run_records = {method: records(path) for method, path in runs.items()}
    arms = {
        method: assess_arm(method, runs[method], run_records[method], definition, land, active, dry, maximum)
        for method in METHODS
    }
    source = {
        method: source_gate(
            source_reports[method],
            definition["viability_thresholds"]["source_consistency_active_soil_all"],
        )
        for method in METHODS
    }
    for method in METHODS:
        arms[method]["failures"].extend(provenance[method]["failures"])
        arms[method]["failures"].extend(source[method]["failures"])
        arms[method]["failures"] = sorted(set(arms[method]["failures"]))
        arms[method]["status"] = "FAIL_VIABILITY" if arms[method]["failures"] else "PASS_VIABILITY"
    comparisons = []
    common_times = sorted(set(run_records["smi"]) & set(run_records["relative_saturation"]))
    for timestamp in common_times:
        left_path, left_index = run_records["smi"][timestamp]
        right_path, right_index = run_records["relative_saturation"][timestamp]
        fields = {}
        for name in PAIR_FIELDS:
            left = read_record(left_path, left_index, name)
            right = read_record(right_path, right_index, name)
            mask = np.broadcast_to(active if left.ndim == 3 else land, left.shape)
            difference = left - right
            selected = difference[mask]
            fields[name] = {
                "signed_mean": float(np.mean(selected)),
                "rmse": float(np.sqrt(np.mean(selected * selected))),
                "absolute_p99": float(np.quantile(np.abs(selected), 0.99)),
                "maximum_absolute": float(np.max(np.abs(selected))),
            }
        comparisons.append({"valid_time": timestamp.isoformat(), "smi_minus_relative_saturation": fields})
    final_separation = comparisons[-1]["smi_minus_relative_saturation"]["soil_water_content"]["rmse"]
    if final_separation <= 1e-6:
        raise SystemExit("matched methods are not distinct at the final time")
    passes = {method: arms[method]["status"] == "PASS_VIABILITY" for method in METHODS}
    key = (
        "both_pass" if all(passes.values()) else
        "only_smi_passes" if passes["smi"] else
        "only_relative_saturation_passes" if passes["relative_saturation"] else
        "neither_passes"
    )
    report = {
        "schema_version": 1,
        "status": "PASS_INTERPRETABLE_EXPERIMENT",
        "experiment_id": definition["experiment_id"],
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "definition": {"path": str(args.definition.resolve()), "sha256": sha256(args.definition)},
        "decision": definition["decision_states"][key],
        "decision_basis": key,
        "arms": arms,
        "source_consistency": source,
        "comparisons": comparisons,
        "provenance": provenance,
        "noahmp_table": {"path": str(args.noahmp_table.resolve()), "sha256": sha256(args.noahmp_table)},
        "remaining_ambiguity": (
            "A six-hour common-source response can reject physical or numerical invalidity, "
            "but cannot establish observational superiority or long-window equilibrium."
        ),
    }
    atomic_json(args.output, report)
    Path(f"{args.output}.ready").touch()
    print(json.dumps({"status": report["status"], "decision": report["decision"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
