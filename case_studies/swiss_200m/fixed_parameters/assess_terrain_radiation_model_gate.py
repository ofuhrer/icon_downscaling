#!/usr/bin/env python3
"""Assess the synthetic terrain-radiation component and restart experiment."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import tempfile

import netCDF4
import numpy as np


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def output_files(root: Path, case: str) -> list[Path]:
    outputs = sorted((root / case / "output").glob("*.nc"))
    if not outputs:
        raise ValueError(f"found no output for {case}")
    return outputs


class Output:
    def __init__(self, paths: list[Path]):
        self.paths = paths
        self.datasets = [netCDF4.Dataset(path) for path in paths]
        self.time_index: dict[datetime, tuple[int, int]] = {}
        for dataset_index, dataset in enumerate(self.datasets):
            time = dataset.variables["time"]
            decoded = netCDF4.num2date(
                time[:], time.units, calendar=getattr(time, "calendar", "standard"),
                only_use_cftime_datetimes=False,
            )
            for record_index, value in enumerate(decoded):
                when = datetime(value.year, value.month, value.day, value.hour, value.minute, value.second)
                if when in self.time_index:
                    raise ValueError(f"duplicate output timestamp {when} in {paths}")
                self.time_index[when] = (dataset_index, record_index)
        self.times = sorted(self.time_index)

    def close(self) -> None:
        for dataset in self.datasets:
            dataset.close()

    def at(self, name: str, when: datetime) -> np.ndarray:
        dataset_index, record_index = self.time_index[when]
        variable = self.datasets[dataset_index].variables[name]
        axis = variable.dimensions.index("time")
        values = np.asarray(variable[:], dtype=np.float64)
        return np.take(values, record_index, axis=axis)

    def timed_variables(self) -> list[str]:
        return sorted(
            name for name, variable in self.datasets[0].variables.items()
            if "time" in variable.dimensions and name != "time" and np.issubdtype(variable.dtype, np.number)
        )


def error_metrics(left: np.ndarray, right: np.ndarray) -> dict[str, float]:
    difference = np.abs(left - right)
    scale = np.maximum(np.maximum(np.abs(left), np.abs(right)), 1.0)
    return {
        "max_abs": float(np.max(difference)),
        "max_scaled": float(np.max(difference / scale)),
    }


def center(values: np.ndarray) -> float:
    if values.ndim != 2:
        raise ValueError(f"expected a two-dimensional surface field, got {values.shape}")
    return float(values[values.shape[0] // 2, values.shape[1] // 2])


def atomic_json(path: Path, payload: dict) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        Path(f"{path}.ready").touch()
    finally:
        Path(temporary).unlink(missing_ok=True)


def assess(run_root: Path, plan_path: Path, output: Path) -> dict:
    if not (run_root / "run_manifest.json.ready").is_file():
        raise ValueError("run manifest is not published")
    if not Path(f"{plan_path}.ready").is_file():
        raise ValueError("execution plan is not published")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    labels = (
        "flat_off", "flat_direct", "flat_direct_diffuse",
        "blocked_direct", "blocked_direct_diffuse", "restart_second",
    )
    datasets = {label: Output(output_files(run_root, label)) for label in labels}
    try:
        terrain_failures: list[str] = []
        restart_failures: list[str] = []
        flat_metrics = {}
        flat_cross_run_metrics = {}
        for candidate in ("flat_direct", "flat_direct_diffuse"):
            per_variable = {}
            expected = {
                "swtb": lambda when: datasets[candidate].at(
                    "shortwave_direct_horizontal", when
                ),
                "swtd": lambda when: datasets[candidate].at(
                    "shortwave_diffuse_horizontal", when
                ),
                "rsds": lambda when: (
                    datasets[candidate].at("shortwave_direct_horizontal", when)
                    + datasets[candidate].at("shortwave_diffuse_horizontal", when)
                ),
            }
            # Test the terrain-radiation operation against the raw RRTMGP
            # components from the same coupled trajectory.  Comparing an
            # enabled run with a separate ``off`` run confounds component
            # identity with any later feedback between independently evolving
            # simulations.
            for name, reference in expected.items():
                metrics = {"max_abs": 0.0, "max_scaled": 0.0}
                for when in datasets[candidate].times:
                    current = error_metrics(
                        datasets[candidate].at(name, when), reference(when)
                    )
                    metrics = {key: max(metrics[key], current[key]) for key in metrics}
                per_variable[name] = metrics
                if metrics["max_abs"] > 1.0e-3 and metrics["max_scaled"] > 1.0e-6:
                    terrain_failures.append(f"flat identity failed for {candidate}:{name}")
            flat_metrics[candidate] = per_variable

            cross_run = {}
            common_flat_times = sorted(
                set(datasets["flat_off"].times) & set(datasets[candidate].times)
            )
            for name in ("rsds", "swtb", "swtd"):
                metrics = {"max_abs": 0.0, "max_scaled": 0.0}
                for when in common_flat_times:
                    current = error_metrics(
                        datasets["flat_off"].at(name, when),
                        datasets[candidate].at(name, when),
                    )
                    metrics = {key: max(metrics[key], current[key]) for key in metrics}
                cross_run[name] = metrics
            flat_cross_run_metrics[candidate] = cross_run

        blocked_samples = []
        svf = 1.0 - np.sin(np.deg2rad(30.0)) ** 2 / 90.0
        for sample in plan["blocked_sector_samples"]:
            when = datetime.fromisoformat(sample["valid_time"]).replace(tzinfo=None)
            direct = center(datasets["blocked_direct"].at("swtb", when))
            direct_raw = center(datasets["blocked_direct"].at("shortwave_direct_horizontal", when))
            diffuse = center(datasets["blocked_direct"].at("swtd", when))
            diffuse_raw = center(datasets["blocked_direct"].at("shortwave_diffuse_horizontal", when))
            diffuse_scaled = center(datasets["blocked_direct_diffuse"].at("swtd", when))
            diffuse_scaled_raw = center(
                datasets["blocked_direct_diffuse"].at("shortwave_diffuse_horizontal", when)
            )
            visible = bool(sample["visible_in_blocked_sector"])
            item = {
                **sample,
                "direct": direct,
                "direct_horizontal": direct_raw,
                "diffuse_direct_only": diffuse,
                "diffuse_horizontal": diffuse_raw,
                "diffuse_direct_diffuse": diffuse_scaled,
                "diffuse_direct_diffuse_horizontal": diffuse_scaled_raw,
            }
            blocked_samples.append(item)
            if direct_raw > 1.0:
                if visible and direct <= 1.0:
                    terrain_failures.append(f"visible direct beam is absent at {when.isoformat()}")
                if not visible and abs(direct) > 1.0e-3:
                    terrain_failures.append(f"shadowed direct beam is nonzero at {when.isoformat()}")
            if abs(diffuse - diffuse_raw) > max(1.0e-3, 1.0e-6 * abs(diffuse_raw)):
                terrain_failures.append(f"direct-only profile changed diffuse flux at {when.isoformat()}")
            expected_diffuse = diffuse_scaled_raw * svf
            if abs(diffuse_scaled - expected_diffuse) > max(2.0e-3, 2.0e-6 * abs(expected_diffuse)):
                terrain_failures.append(f"direct-diffuse profile does not apply SVF at {when.isoformat()}")

        continuous = datasets["blocked_direct_diffuse"]
        restarted = datasets["restart_second"]
        common_times = sorted(set(continuous.times) & set(restarted.times))
        split_time = datetime.fromisoformat(plan["timeline"]["split"]).replace(tzinfo=None)
        expected_start = split_time + timedelta(seconds=plan["timeline"]["output_interval_seconds"])
        common_times = [when for when in common_times if when >= expected_start]
        if not common_times or common_times[0] != expected_start:
            restart_failures.append("restart outputs do not begin at the first post-split output timestamp")
        restart_metrics = {}
        variables = sorted(set(continuous.timed_variables()) & set(restarted.timed_variables()))
        for name in variables:
            metrics = {"max_abs": 0.0, "max_scaled": 0.0}
            for when in common_times:
                current = error_metrics(continuous.at(name, when), restarted.at(name, when))
                metrics = {key: max(metrics[key], current[key]) for key in metrics}
            if common_times:
                first = error_metrics(
                    continuous.at(name, common_times[0]), restarted.at(name, common_times[0])
                )
                final = error_metrics(
                    continuous.at(name, common_times[-1]), restarted.at(name, common_times[-1])
                )
                metrics.update(
                    first_max_abs=first["max_abs"], first_max_scaled=first["max_scaled"],
                    final_max_abs=final["max_abs"], final_max_scaled=final["max_scaled"],
                )
            restart_metrics[name] = metrics
            if metrics["max_abs"] > 2.0e-5 and metrics["max_scaled"] > 2.0e-6:
                restart_failures.append(f"restart mismatch for {name}")

        failures = terrain_failures + restart_failures

        report = {
            "schema": "hicar-terrain-radiation-assessment/v1",
            "status": "PASS" if not failures else "FAIL",
            "terrain_component_status": "PASS" if not terrain_failures else "FAIL",
            "restart_status": "PASS" if not restart_failures else "FAIL",
            "gates": {
                "flat_identity": flat_metrics,
                "flat_off_cross_run_drift_diagnostic": flat_cross_run_metrics,
                "blocked_sector": blocked_samples,
                "blocked_svf": svf,
                "restart": {
                    "times": [when.isoformat() for when in common_times],
                    "variables": restart_metrics,
                },
            },
            "failures": failures,
            "terrain_failures": terrain_failures,
            "restart_failures": restart_failures,
            "inputs": {
                "execution_plan": {"path": str(plan_path), "sha256": digest(plan_path)},
                "run_manifest": {
                    "path": str(run_root / "run_manifest.json"),
                    "sha256": digest(run_root / "run_manifest.json"),
                },
            },
            "decision": (
                "SYNTHETIC_TERRAIN_RADIATION_GATE_PASS_VALLEY_CASE_REQUIRED"
                if not failures else
                "TERRAIN_COMPONENT_PASS_RESTART_GATE_FAIL"
                if not terrain_failures else
                "SYNTHETIC_TERRAIN_RADIATION_GATE_FAIL"
            ),
            "promotion_limit": "A pass permits a bounded real-valley qualification only, not national production.",
        }
        atomic_json(output, report)
        return report
    finally:
        for dataset in datasets.values():
            dataset.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--execution-plan", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = assess(args.run_root.resolve(), args.execution_plan.resolve(), args.output.resolve())
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps({"status": report["status"], "decision": report["decision"], "failures": report["failures"]}))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
