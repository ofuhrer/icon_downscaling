#!/usr/bin/env python3
"""Assess fixed-height HICAR wind convergence across cold-start spin-up times."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import netCDF4
import numpy as np


TIME_FORMAT = "%Y-%m-%dT%H:%M:%S"
HEIGHTS = (10, 50, 75, 100, 125, 150, 200)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def parse_time(value: str) -> datetime:
    return datetime.strptime(value, TIME_FORMAT)


def dataset_times(dataset: netCDF4.Dataset) -> list[datetime]:
    variable = dataset.variables["time"]
    decoded = netCDF4.num2date(
        variable[:],
        variable.units,
        calendar=getattr(variable, "calendar", "standard"),
        only_use_cftime_datetimes=False,
        only_use_python_datetimes=True,
    )
    return [
        datetime(item.year, item.month, item.day, item.hour, item.minute, item.second)
        for item in np.atleast_1d(decoded)
    ]


def require_published(path: Path, label: str) -> None:
    if not path.is_file() or not Path(f"{path}.ready").is_file():
        raise ValueError(f"{label} is not published: {path}")


class WindHistory:
    def __init__(self, paths: Path | list[Path]) -> None:
        self.paths = [paths] if isinstance(paths, Path) else list(paths)
        if not self.paths:
            raise ValueError("wind history has no files")
        for path in self.paths:
            require_published(path, "history file")
        self.path = ", ".join(str(path) for path in self.paths)
        self.datasets = [netCDF4.Dataset(path) for path in self.paths]
        required = {
            "time",
            "u10m",
            "v10m",
            "u_agl",
            "v_agl",
            "rho_agl",
            "ustar",
            "surface_roughness",
            "sfc_Ri",
            "hpbl",
            "height_agl",
        }
        self.times: list[datetime] = []
        self.time_index: dict[datetime, tuple[int, int]] = {}
        reference_heights: np.ndarray | None = None
        try:
            for dataset_index, (path, dataset) in enumerate(zip(self.paths, self.datasets)):
                missing = sorted(required - set(dataset.variables))
                if missing:
                    raise ValueError(f"{path} lacks required variables: {missing}")
                heights = np.asarray(dataset.variables["height_agl"][:], dtype=float)
                if reference_heights is None:
                    reference_heights = heights
                elif not np.array_equal(heights, reference_heights):
                    raise ValueError(f"{path} has inconsistent fixed AGL heights")
                for local_index, valid_time in enumerate(dataset_times(dataset)):
                    if valid_time in self.time_index:
                        raise ValueError(
                            f"wind history has duplicate time {valid_time:{TIME_FORMAT}}"
                        )
                    self.time_index[valid_time] = (dataset_index, local_index)
                    self.times.append(valid_time)
        except BaseException:
            self.close()
            raise
        if len(self.times) != len(set(self.times)):
            self.close()
            raise ValueError("wind history has duplicate times")
        assert reference_heights is not None
        self.height_index = {
            int(round(float(value))): index for index, value in enumerate(reference_heights)
        }
        if set(HEIGHTS[1:]) - set(self.height_index):
            self.close()
            raise ValueError("wind history lacks required fixed AGL heights")

    def close(self) -> None:
        for dataset in self.datasets:
            dataset.close()

    def field(self, component: str, height: int, valid_time: datetime) -> np.ndarray:
        if valid_time not in self.time_index:
            raise ValueError(f"{self.path} lacks time {valid_time:{TIME_FORMAT}}")
        dataset_index, local_index = self.time_index[valid_time]
        dataset = self.datasets[dataset_index]
        name = f"{component}10m" if height == 10 else f"{component}_agl"
        variable = dataset.variables[name]
        selection: list[Any] = []
        for dimension in variable.dimensions:
            if dimension == "time":
                selection.append(local_index)
            elif dimension == "height_agl":
                selection.append(self.height_index[height])
            else:
                selection.append(slice(None))
        values = variable[tuple(selection)]
        return np.asarray(np.ma.filled(values, np.nan), dtype=np.float64).squeeze()

    def diagnostic(self, name: str, valid_time: datetime, height: int | None = None) -> np.ndarray:
        if valid_time not in self.time_index:
            raise ValueError(f"{self.path} lacks time {valid_time:{TIME_FORMAT}}")
        dataset_index, local_index = self.time_index[valid_time]
        variable = self.datasets[dataset_index].variables[name]
        selection: list[Any] = []
        for dimension in variable.dimensions:
            if dimension == "time":
                selection.append(local_index)
            elif dimension == "height_agl":
                if height is None:
                    raise ValueError(f"{name} requires a height")
                selection.append(self.height_index[height])
            else:
                selection.append(slice(None))
        values = variable[tuple(selection)]
        return np.asarray(np.ma.filled(values, np.nan), dtype=np.float64).squeeze()


def expected_times(run: dict[str, Any]) -> list[datetime]:
    start = parse_time(run["retained_start_exclusive"])
    end = parse_time(run["overlap_end_inclusive"])
    cadence = timedelta(seconds=int(run["output_interval_seconds"]))
    values: list[datetime] = []
    cursor = start + cadence
    while cursor <= end:
        values.append(cursor)
        cursor += cadence
    if not values or values[-1] != end:
        raise ValueError(f"run {run['run_id']} assessment interval is off cadence")
    return values


def compare_height(
    candidate: WindHistory,
    reference: WindHistory,
    times: list[datetime],
    height: int,
    thresholds: dict[str, float],
) -> dict[str, Any]:
    count = 0
    missing = 0
    direction_count = 0
    sum_vector_error_sq = 0.0
    sum_reference_speed_sq = 0.0
    sum_speed_bias = 0.0
    sum_direction_error = 0.0
    histogram_edges = np.linspace(0.0, 10.0, 1001)
    histogram = np.zeros(histogram_edges.size - 1, dtype=np.int64)

    for valid_time in times:
        cu = candidate.field("u", height, valid_time)
        cv = candidate.field("v", height, valid_time)
        ru = reference.field("u", height, valid_time)
        rv = reference.field("v", height, valid_time)
        if not (cu.shape == cv.shape == ru.shape == rv.shape):
            raise ValueError(f"grid shape mismatch at {height} m")
        finite = np.isfinite(cu) & np.isfinite(cv) & np.isfinite(ru) & np.isfinite(rv)
        missing += int(finite.size - np.count_nonzero(finite))
        if not np.any(finite):
            continue
        cu = cu[finite]
        cv = cv[finite]
        ru = ru[finite]
        rv = rv[finite]
        candidate_speed = np.hypot(cu, cv)
        reference_speed = np.hypot(ru, rv)
        vector_error = np.hypot(cu - ru, cv - rv)
        sample_count = vector_error.size
        count += sample_count
        sum_vector_error_sq += float(np.dot(vector_error, vector_error))
        sum_reference_speed_sq += float(np.dot(reference_speed, reference_speed))
        sum_speed_bias += float(np.sum(candidate_speed - reference_speed))
        histogram += np.histogram(vector_error, bins=histogram_edges)[0]

        directional = (candidate_speed >= thresholds["direction_min_speed_m_s"]) & (
            reference_speed >= thresholds["direction_min_speed_m_s"]
        )
        if np.any(directional):
            dot = cu[directional] * ru[directional] + cv[directional] * rv[directional]
            cross = cu[directional] * rv[directional] - cv[directional] * ru[directional]
            angle = np.abs(np.degrees(np.arctan2(cross, dot)))
            direction_count += angle.size
            sum_direction_error += float(np.sum(angle))

    if count == 0:
        raise ValueError(f"no comparable samples at {height} m")
    vector_rmse = math.sqrt(sum_vector_error_sq / count)
    reference_rms_speed = math.sqrt(sum_reference_speed_sq / count)
    relative_rmse = vector_rmse / reference_rms_speed if reference_rms_speed > 0.0 else math.inf
    speed_bias = sum_speed_bias / count
    direction_mae = sum_direction_error / direction_count if direction_count else math.inf
    quantile_rank = math.ceil(0.99 * count)
    cumulative = np.cumsum(histogram)
    if not np.any(cumulative >= quantile_rank):
        vector_error_p99 = math.inf
    else:
        index = int(np.searchsorted(cumulative, quantile_rank))
        vector_error_p99 = float(histogram_edges[index + 1])

    checks = {
        "no_missing_values": missing == 0,
        "vector_rmse": vector_rmse <= thresholds["vector_rmse_m_s"],
        "relative_vector_rmse": relative_rmse <= thresholds["relative_vector_rmse"],
        "absolute_speed_bias": abs(speed_bias) <= thresholds["absolute_speed_bias_m_s"],
        "direction_mae": direction_mae <= thresholds["direction_mae_degrees"],
        "vector_error_p99": vector_error_p99 <= thresholds["vector_error_p99_m_s"],
    }
    return {
        "height_agl_m": height,
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "sample_count": count,
        "missing_count": missing,
        "direction_sample_count": direction_count,
        "vector_rmse_m_s": vector_rmse,
        "reference_rms_speed_m_s": reference_rms_speed,
        "relative_vector_rmse": relative_rmse,
        "speed_bias_m_s": speed_bias,
        "direction_mae_degrees": direction_mae,
        "vector_error_p99_m_s": vector_error_p99,
    }


def check_physicality(
    history: WindHistory,
    times: list[datetime],
) -> dict[str, Any]:
    specifications = [
        ("rho_agl", HEIGHTS[1:], 0.2, 1.6, False),
        ("ustar", (None,), 0.0, 10.0, True),
        ("surface_roughness", (None,), 0.0, 10.0, False),
        ("sfc_Ri", (None,), -1000.0, 10000.0, True),
        ("hpbl", (None,), 0.0, 12000.0, True),
    ]
    diagnostics: list[dict[str, Any]] = []
    for name, heights, lower, upper, lower_inclusive in specifications:
        minimum = math.inf
        maximum = -math.inf
        missing = 0
        count = 0
        for valid_time in times:
            for height in heights:
                values = history.diagnostic(name, valid_time, height)
                finite = np.isfinite(values)
                missing += int(finite.size - np.count_nonzero(finite))
                if np.any(finite):
                    valid = values[finite]
                    count += valid.size
                    minimum = min(minimum, float(np.min(valid)))
                    maximum = max(maximum, float(np.max(valid)))
        lower_ok = minimum >= lower if lower_inclusive else minimum > lower
        checks = {
            "has_samples": count > 0,
            "no_missing_values": missing == 0,
            "minimum": lower_ok,
            "maximum": maximum <= upper,
        }
        diagnostics.append(
            {
                "variable": name,
                "status": "PASS" if all(checks.values()) else "FAIL",
                "checks": checks,
                "sample_count": count,
                "missing_count": missing,
                "minimum": minimum,
                "maximum": maximum,
                "accepted_range": {
                    "minimum": lower,
                    "minimum_inclusive": lower_inclusive,
                    "maximum": upper,
                },
            }
        )
    return {
        "status": ("PASS" if all(item["status"] == "PASS" for item in diagnostics) else "FAIL"),
        "diagnostics": diagnostics,
    }


def assess(
    *,
    experiment_path: Path,
    results_path: Path,
    report_path: Path,
    selection_gate_path: Path | None = None,
) -> dict[str, Any]:
    require_published(experiment_path, "experiment manifest")
    require_published(results_path, "results manifest")
    experiment = json.loads(experiment_path.read_text())
    results = json.loads(results_path.read_text())
    if results.get("schema_version") != 1:
        raise ValueError("results manifest schema_version must be 1")
    if results.get("experiment_sha256") != sha256(experiment_path):
        raise ValueError("results manifest is not bound to the experiment")
    completion_value = results.get("campaign_completion")
    completion_sha256 = results.get("campaign_completion_sha256")
    if completion_value is not None:
        completion_path = Path(completion_value)
        require_published(completion_path, "campaign completion")
        if sha256(completion_path) != completion_sha256:
            raise ValueError("results campaign-completion checksum mismatch")
        if json.loads(completion_path.read_text()).get("status") != "PASS":
            raise ValueError("results campaign completion is not PASS")
    selection_gate: dict[str, Any] | None = None
    if selection_gate_path is not None:
        require_published(selection_gate_path, "case-selection gate")
        selection_gate = json.loads(selection_gate_path.read_text())
        if selection_gate.get("status") != "PASS":
            raise ValueError("case-selection gate is not PASS")
    run_specs = {run["run_id"]: run for run in experiment["runs"]}
    paths: dict[str, list[Path]] = {}
    for item in results["runs"]:
        values = item.get("history_files")
        if values is None:
            values = [item["history_file"]]
        if not isinstance(values, list) or not values:
            raise ValueError(f"run {item['run_id']} has no history files")
        paths[item["run_id"]] = [Path(value).resolve() for value in values]
    if set(paths) != set(run_specs):
        raise ValueError("results do not cover exactly the planned runs")

    reference_hours = int(experiment["reference_spinup_hours"])
    by_case_and_hours = {
        (run["case_id"], int(run["spinup_hours"])): run for run in experiment["runs"]
    }
    assessment_by_run: dict[str, dict[str, Any]] = {}
    physicality_by_run: dict[str, dict[str, Any]] = {}
    case_ids = list(dict.fromkeys(run["case_id"] for run in experiment["runs"]))
    for case_id in case_ids:
        reference_run = by_case_and_hours[(case_id, reference_hours)]
        reference = WindHistory(paths[reference_run["run_id"]])
        try:
            for run in (item for item in experiment["runs"] if item["case_id"] == case_id):
                hours = int(run["spinup_hours"])
                candidate = (
                    reference if hours == reference_hours else WindHistory(paths[run["run_id"]])
                )
                try:
                    times = expected_times(run)
                    physicality_by_run[run["run_id"]] = {
                        "run_id": run["run_id"],
                        "case_id": case_id,
                        "spinup_hours": hours,
                        **check_physicality(candidate, times),
                    }
                    if hours == reference_hours:
                        continue
                    metrics = [
                        compare_height(
                            candidate,
                            reference,
                            times,
                            height,
                            experiment["thresholds"],
                        )
                        for height in HEIGHTS
                    ]
                    assessment_by_run[run["run_id"]] = {
                        "run_id": run["run_id"],
                        "case_id": case_id,
                        "spinup_hours": hours,
                        "reference_run_id": reference_run["run_id"],
                        "status": (
                            "PASS" if all(item["status"] == "PASS" for item in metrics) else "FAIL"
                        ),
                        "heights": metrics,
                    }
                finally:
                    if candidate is not reference:
                        candidate.close()
        finally:
            reference.close()
    physicality = [physicality_by_run[run["run_id"]] for run in experiment["runs"]]
    assessments = [
        assessment_by_run[run["run_id"]]
        for run in experiment["runs"]
        if int(run["spinup_hours"]) != reference_hours
    ]

    candidates = experiment["candidate_spinup_hours"]
    physicality_pass_by_hours = {
        int(hours): all(
            item["status"] == "PASS" for item in physicality if item["spinup_hours"] == int(hours)
        )
        and any(item["spinup_hours"] == int(hours) for item in physicality)
        for hours in candidates
    }
    pass_by_hours = {
        int(hours): (
            physicality_pass_by_hours[int(hours)]
            if int(hours) == reference_hours
            else all(
                item["status"] == "PASS"
                for item in assessments
                if item["spinup_hours"] == int(hours)
            )
            and physicality_pass_by_hours[int(hours)]
            and any(item["spinup_hours"] == int(hours) for item in assessments)
        )
        for hours in candidates
    }
    selected: int | None = None
    for hours in candidates:
        if all(pass_by_hours[int(later)] for later in candidates if int(later) >= int(hours)):
            selected = int(hours)
            break
    lower_bound: int | None = None
    if selected is None:
        status = "HOLD"
        decision = "NO_SPINUP_SELECTED"
    elif selected == reference_hours:
        lower_bound = reference_hours
        selected = None
        status = "HOLD"
        decision = "MINIMUM_SPINUP_NOT_BRACKETED"
    else:
        status = "PASS"
        decision = "SELECT_MINIMUM_SPINUP"
    payload = {
        "schema_version": 1,
        "status": status,
        "decision": decision,
        "selected_spinup_hours": selected,
        "lower_bound_spinup_hours": lower_bound,
        "reference_spinup_hours": reference_hours,
        "pass_by_spinup_hours": pass_by_hours,
        "experiment": str(experiment_path),
        "experiment_sha256": sha256(experiment_path),
        "results": str(results_path),
        "results_sha256": sha256(results_path),
        "selection_gate": (
            {
                "path": str(selection_gate_path),
                "sha256": sha256(selection_gate_path),
                "status": selection_gate["status"],
                "decision": selection_gate.get("decision"),
            }
            if selection_gate_path is not None and selection_gate is not None
            else None
        ),
        "thresholds": experiment["thresholds"],
        "assessments": assessments,
        "physicality": physicality,
        "scope": (
            "cold-start convergence only; observational skill, gust validation, "
            "and stitched-chain equivalence remain separate scientific questions"
        ),
    }
    if report_path.exists() or Path(f"{report_path}.ready").exists():
        raise ValueError(f"refusing to replace existing assessment: {report_path}")
    write_json_atomic(report_path, payload)
    Path(f"{report_path}.ready").touch()
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--selection-gate", type=Path)
    args = parser.parse_args()
    payload = assess(
        experiment_path=args.experiment.resolve(),
        results_path=args.results.resolve(),
        report_path=args.report.resolve(),
        selection_gate_path=(
            args.selection_gate.resolve() if args.selection_gate is not None else None
        ),
    )
    print(
        f"wind spin-up assessment: {payload['status']} selected={payload['selected_spinup_hours']}"
    )
    return 0 if payload["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
