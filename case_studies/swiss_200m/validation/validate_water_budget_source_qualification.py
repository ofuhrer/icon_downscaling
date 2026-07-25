#!/usr/bin/env python3
"""Publish the evidence bundle for the HICAR water-budget source child."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Iterator

from netCDF4 import Dataset
import numpy as np
import yaml


PARENT_COMMIT = "2ea31109801a2477a946840693934318f8d50c95"
NEW_FIELDS = {
    "runoff_surface_cumulative",
    "runoff_subsurface_cumulative",
    "evaporation_net_cumulative",
}
CUMULATIVE_FIELDS = {"precipitation", *NEW_FIELDS}
GATE_PATTERN = re.compile(
    r"(HICAR SLEVE geometry gate:|"
    r"HICAR terminal collective solve gate:|"
    r"HICAR exact Galerkin hierarchy ready:|"
    r"HICAR terminal physical solve:|"
    r"HICAR native FGMRES\+line:|"
    r"HICAR adjoint conservation:)"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--parent-root", required=True, type=Path)
    parser.add_argument("--child-root", required=True, type=Path)
    parser.add_argument("--child-build", required=True, type=Path)
    parser.add_argument("--child-commit", required=True)
    parser.add_argument("--build-job-id", required=True)
    parser.add_argument("--build-log", required=True, type=Path)
    parser.add_argument("--build-script", required=True, type=Path)
    parser.add_argument("--bridge-job-id", required=True)
    parser.add_argument("--bridge-run", required=True, type=Path)
    parser.add_argument("--restart-tolerances", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args()


def sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def revision(root: Path, ref: str = "HEAD") -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", ref], text=True
    ).strip()


def git_clean(root: Path) -> bool:
    tracked_clean = (
        subprocess.run(
            ["git", "-C", str(root), "diff", "--quiet"], check=False
        ).returncode
        == 0
        and subprocess.run(
            ["git", "-C", str(root), "diff", "--cached", "--quiet"], check=False
        ).returncode
        == 0
    )
    untracked_inputs = subprocess.check_output(
        [
            "git",
            "-C",
            str(root),
            "ls-files",
            "--others",
            "--exclude-standard",
            "--",
            "src",
            "cmake",
            "external",
            "tools",
            "CMakeLists.txt",
            "CMakePresets.json",
        ],
        text=True,
    )
    return tracked_clean and not untracked_inputs.strip()


def publish(path: Path, payload: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)
    digest = sha256(path)
    ready = Path(f"{path}.ready")
    ready_tmp = ready.with_name(f".{ready.name}.tmp")
    ready_tmp.write_text("")
    ready_tmp.replace(ready)
    return digest


def only_file(directory: Path, pattern: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one {pattern} under {directory}, found {len(matches)}"
        )
    return matches[0]


def _effective_shape(variable: Any, last_time: bool) -> tuple[int, ...]:
    shape = tuple(variable.shape)
    if last_time and variable.dimensions and variable.dimensions[0] == "time":
        return shape[1:]
    return shape


def _chunk_keys(variable: Any, last_time: bool) -> Iterator[tuple[Any, ...]]:
    shape = _effective_shape(variable, last_time)
    time_prefix: tuple[Any, ...] = ()
    if last_time and variable.dimensions and variable.dimensions[0] == "time":
        time_prefix = (variable.shape[0] - 1,)
    if len(shape) <= 2:
        yield time_prefix + tuple(slice(None) for _ in shape)
        return
    for prefix in itertools.product(*(range(size) for size in shape[:-2])):
        yield time_prefix + prefix + (slice(None), slice(None))


def _paired_chunk_keys(
    reference: Any, candidate: Any, last_time: bool
) -> Iterator[tuple[tuple[Any, ...], tuple[Any, ...]]]:
    reference_keys = _chunk_keys(reference, last_time)
    candidate_keys = _chunk_keys(candidate, last_time)
    yield from zip(reference_keys, candidate_keys, strict=True)


def _fill_value(variable: Any) -> Any:
    return getattr(variable, "_FillValue", None)


def _valid_mask(values: np.ndarray, fill_value: Any) -> np.ndarray:
    if not np.issubdtype(values.dtype, np.number):
        return np.ones(values.shape, dtype=bool)
    mask = np.isfinite(values)
    if fill_value is not None:
        mask &= values != fill_value
    return mask


def compare_exact(
    reference_path: Path,
    candidate_path: Path,
    *,
    expected_candidate_only_fields: set[str] = NEW_FIELDS,
) -> dict[str, Any]:
    mismatches: list[dict[str, Any]] = []
    compared: list[str] = []
    with Dataset(reference_path) as reference, Dataset(candidate_path) as candidate:
        reference.set_auto_maskandscale(False)
        candidate.set_auto_maskandscale(False)
        missing = sorted(set(reference.variables) - set(candidate.variables))
        extra = sorted(set(candidate.variables) - set(reference.variables))
        if missing:
            mismatches.append({"kind": "missing_variables", "variables": missing})
        if set(extra) != expected_candidate_only_fields:
            mismatches.append(
                {
                    "kind": "unexpected_candidate_variables",
                    "expected": sorted(expected_candidate_only_fields),
                    "actual": extra,
                }
            )
        for name, left in reference.variables.items():
            if name not in candidate.variables:
                continue
            right = candidate.variables[name]
            if not (
                np.issubdtype(left.dtype, np.number)
                and np.issubdtype(right.dtype, np.number)
            ):
                continue
            compared.append(name)
            if left.dimensions != right.dimensions or left.shape != right.shape:
                mismatches.append(
                    {
                        "kind": "shape",
                        "variable": name,
                        "reference_dimensions": left.dimensions,
                        "candidate_dimensions": right.dimensions,
                        "reference_shape": left.shape,
                        "candidate_shape": right.shape,
                    }
                )
                continue
            for key in _chunk_keys(left, last_time=False):
                left_values = np.asarray(left[key])
                right_values = np.asarray(right[key])
                if not np.array_equal(left_values, right_values, equal_nan=True):
                    difference = np.abs(
                        left_values.astype(np.float64)
                        - right_values.astype(np.float64)
                    )
                    mismatches.append(
                        {
                            "kind": "data",
                            "variable": name,
                            "chunk": repr(key),
                            "max_abs_difference": float(np.nanmax(difference)),
                        }
                    )
                    break
    return {
        "compared_fields": sorted(compared),
        "compared_field_count": len(compared),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
        "candidate_only_fields": sorted(expected_candidate_only_fields),
    }


def load_tolerances(path: Path) -> dict[str, Any]:
    spec = yaml.safe_load(path.read_text()) or {}
    defaults = spec.get("defaults", {}) or {}
    per_variable = dict(spec.get("variables", {}) or {})
    aggregate = spec.get("aggregate", {}) or {}
    if isinstance(aggregate, dict):
        per_variable.update(aggregate)
        aggregate_names = set(aggregate)
    else:
        aggregate_names = set(aggregate)
    aggregate_names.update(CUMULATIVE_FIELDS)
    for name in NEW_FIELDS:
        per_variable[name] = {"rtol": 2.0e-2, "atol": 1.0e-9, "frac": 0.0}
    return {
        "defaults": {
            "rtol": float(defaults.get("rtol", 0.0)),
            "atol": float(defaults.get("atol", 0.0)),
            "frac": float(defaults.get("frac", 0.0)),
        },
        "variables": per_variable,
        "aggregate": aggregate_names,
        "warn_only": set(spec.get("warn_only", []) or []),
        "ignore": set(spec.get("ignore", []) or []),
    }


def _rule(spec: dict[str, Any], name: str) -> tuple[float, float, float]:
    defaults = spec["defaults"]
    override = spec["variables"].get(name, {}) or {}
    return (
        float(override.get("rtol", defaults["rtol"])),
        float(override.get("atol", defaults["atol"])),
        float(override.get("frac", defaults["frac"])),
    )


def compare_tolerant(
    reference_path: Path,
    candidate_path: Path,
    spec: dict[str, Any],
    *,
    last_time: bool,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    with Dataset(reference_path) as reference, Dataset(candidate_path) as candidate:
        reference.set_auto_maskandscale(False)
        candidate.set_auto_maskandscale(False)
        all_names = sorted(set(reference.variables) | set(candidate.variables))
        for name in all_names:
            if name in spec["ignore"]:
                continue
            if name not in reference.variables or name not in candidate.variables:
                results.append({"variable": name, "status": "MISSING"})
                continue
            left = reference.variables[name]
            right = candidate.variables[name]
            left_shape = _effective_shape(left, last_time)
            right_shape = _effective_shape(right, last_time)
            if (
                left.dimensions != right.dimensions
                or left_shape != right_shape
                or left.dtype != right.dtype
            ):
                results.append(
                    {
                        "variable": name,
                        "status": "STRUCTURE_MISMATCH",
                        "reference_shape": left_shape,
                        "candidate_shape": right_shape,
                    }
                )
                continue
            if not np.issubdtype(left.dtype, np.number):
                exact = all(
                    np.array_equal(
                        np.asarray(left[left_key]), np.asarray(right[right_key])
                    )
                    for left_key, right_key in _paired_chunk_keys(
                        left, right, last_time
                    )
                )
                results.append(
                    {"variable": name, "status": "PASS" if exact else "DIFF"}
                )
                continue

            rtol, atol, frac = _rule(spec, name)
            aggregate = name in spec["aggregate"]
            violations = 0
            finite_count = 0
            introduced_nonfinite = 0
            fill_mismatch = 0
            max_abs = 0.0
            left_sum = 0.0
            right_sum = 0.0
            aggregate_count = 0
            for left_key, right_key in _paired_chunk_keys(left, right, last_time):
                left_values = np.asarray(left[left_key], dtype=np.float64)
                right_values = np.asarray(right[right_key], dtype=np.float64)
                left_valid = _valid_mask(left_values, _fill_value(left))
                right_valid = _valid_mask(right_values, _fill_value(right))
                fill_mismatch += int(np.count_nonzero(left_valid != right_valid))
                introduced_nonfinite += int(
                    np.count_nonzero(~np.isfinite(right_values) & np.isfinite(left_values))
                )
                valid = left_valid & right_valid
                if not np.any(valid):
                    continue
                finite_count += int(np.count_nonzero(valid))
                difference = np.abs(right_values - left_values)
                max_abs = max(max_abs, float(np.max(difference[valid])))
                if aggregate:
                    left_sum += float(np.sum(left_values[valid], dtype=np.float64))
                    right_sum += float(np.sum(right_values[valid], dtype=np.float64))
                    aggregate_count += int(np.count_nonzero(valid))
                else:
                    threshold = atol + rtol * np.abs(left_values)
                    violations += int(np.count_nonzero(valid & (difference > threshold)))

            if aggregate and aggregate_count:
                left_mean = left_sum / aggregate_count
                right_mean = right_sum / aggregate_count
                violations = int(
                    abs(right_mean - left_mean) > atol + rtol * abs(left_mean)
                )
            allowed = int(frac * finite_count)
            failed = (
                fill_mismatch > 0
                or introduced_nonfinite > 0
                or violations > allowed
            )
            status = "PASS"
            if failed:
                status = "WARN" if name in spec["warn_only"] else "FAIL"
            result = {
                "variable": name,
                "status": status,
                "aggregate": aggregate,
                "rtol": rtol,
                "atol": atol,
                "frac": frac,
                "finite_count": finite_count,
                "violations": violations,
                "allowed_violations": allowed,
                "introduced_nonfinite": introduced_nonfinite,
                "fill_mismatch": fill_mismatch,
                "max_abs_difference": max_abs,
            }
            if aggregate and aggregate_count:
                result.update(
                    {
                        "reference_mean": left_mean,
                        "candidate_mean": right_mean,
                    }
                )
            results.append(result)
    return {
        "compared_fields": [item["variable"] for item in results],
        "compared_field_count": len(results),
        "failure_count": sum(item["status"] not in {"PASS", "WARN"} for item in results),
        "warning_count": sum(item["status"] == "WARN" for item in results),
        "results": results,
    }


def field_stats(path: Path, name: str, time_index: int) -> dict[str, Any]:
    with Dataset(path) as dataset:
        dataset.set_auto_maskandscale(False)
        variable = dataset.variables[name]
        total = 0.0
        count = 0
        positive = 0
        minimum = np.inf
        maximum = -np.inf
        for key in _chunk_keys(variable, last_time=False):
            if variable.dimensions and variable.dimensions[0] == "time":
                if not key or key[0] != time_index:
                    continue
            values = np.asarray(variable[key], dtype=np.float64)
            valid = _valid_mask(values, _fill_value(variable))
            if not np.any(valid):
                continue
            selected = values[valid]
            total += float(np.sum(selected, dtype=np.float64))
            count += selected.size
            positive += int(np.count_nonzero(selected > 0))
            minimum = min(minimum, float(np.min(selected)))
            maximum = max(maximum, float(np.max(selected)))
        return {
            "minimum": minimum,
            "maximum": maximum,
            "mean": total / count,
            "positive_count": positive,
            "finite_count": count,
        }


def gate_lines(path: Path) -> list[str]:
    return [
        " ".join(line.split())
        for line in path.read_text(errors="replace").splitlines()
        if GATE_PATTERN.search(line)
    ]


def require_log_pass(path: Path) -> None:
    text = path.read_text(errors="replace")
    required = (
        "Simulation completed successfully!",
        "HICAR SLEVE geometry gate:",
        "HICAR discretely adjoint wind projection enabled",
        "Timing across all compute images:",
    )
    missing = [marker for marker in required if marker not in text]
    if missing:
        raise RuntimeError(f"{path} lacks completion markers: {missing}")
    if "Accelerator Fatal Error" in text:
        raise RuntimeError(f"{path} contains an accelerator fatal error")


def main() -> int:
    args = parse_args()
    if revision(args.parent_root) != PARENT_COMMIT:
        raise SystemExit("parent source identity changed")
    if revision(args.child_root) != args.child_commit:
        raise SystemExit("child source identity changed")
    if revision(args.child_root, "HEAD^") != PARENT_COMMIT:
        raise SystemExit("child is not the direct output-diagnostic-only child")
    merge_base = subprocess.check_output(
        [
            "git",
            "-C",
            str(args.child_root),
            "merge-base",
            PARENT_COMMIT,
            args.child_commit,
        ],
        text=True,
    ).strip()
    if merge_base != PARENT_COMMIT:
        raise SystemExit("parent ancestry is not proven")

    model_manifest = json.loads(
        (args.run_root / "model_runs.json").read_text(encoding="utf-8")
    )
    if model_manifest.get("status") != "MODEL_RUNS_COMPLETE":
        raise SystemExit("national model-run manifest is not complete")
    parent_run = args.run_root / "parent"
    continuous_run = args.run_root / "child_continuous"
    restart_run = args.run_root / "child_restart"
    for run in (parent_run, continuous_run, restart_run):
        require_log_pass(run / "model.out")

    parent_output = only_file(parent_run / "output", "*.nc")
    continuous_output = only_file(continuous_run / "output", "*.nc")
    restart_output = only_file(restart_run / "output", "*.nc")
    continuous_restart = only_file(
        continuous_run / "restart", "*_2020-07-01_02-00-00.nc"
    )
    segmented_restart = only_file(
        restart_run / "restart", "*_2020-07-01_02-00-00.nc"
    )

    exact = compare_exact(parent_output, continuous_output)
    exact_payload = {
        "schema_version": 1,
        "status": "PASS" if exact["mismatch_count"] == 0 else "FAIL",
        "reference": str(parent_output),
        "candidate": str(continuous_output),
        **exact,
    }

    parent_gates = gate_lines(parent_run / "model.out")
    child_gates = gate_lines(continuous_run / "model.out")
    solver_mismatches = 0 if parent_gates == child_gates else 1
    solver_payload = {
        "schema_version": 1,
        "status": "PASS" if solver_mismatches == 0 and parent_gates else "FAIL",
        "compared_gate_count": len(parent_gates),
        "mismatch_count": solver_mismatches,
        "parent_gate_lines": parent_gates,
        "child_gate_lines": child_gates,
    }

    tolerance_spec = load_tolerances(args.restart_tolerances)
    output_restart = compare_tolerant(
        continuous_output, restart_output, tolerance_spec, last_time=True
    )
    state_restart = compare_tolerant(
        continuous_restart, segmented_restart, tolerance_spec, last_time=False
    )
    initial_stats = {
        name: field_stats(continuous_output, name, 0) for name in NEW_FIELDS
    }
    final_stats = {
        name: field_stats(continuous_output, name, 2) for name in NEW_FIELDS
    }
    nonzero_runoff = (
        final_stats["runoff_surface_cumulative"]["maximum"] > 0
        or final_stats["runoff_subsurface_cumulative"]["maximum"] > 0
    ) and (
        final_stats["runoff_surface_cumulative"]["mean"]
        + final_stats["runoff_subsurface_cumulative"]["mean"]
        > initial_stats["runoff_surface_cumulative"]["mean"]
        + initial_stats["runoff_subsurface_cumulative"]["mean"]
    )
    with Dataset(continuous_output) as dataset:
        metadata = {
            name: {
                "units": getattr(dataset.variables[name], "units", None),
                "accumulation_semantics": getattr(
                    dataset.variables[name], "accumulation_semantics", None
                ),
                "interval_semantics": getattr(
                    dataset.variables[name], "interval_semantics", None
                ),
            }
            for name in CUMULATIVE_FIELDS
        }
    metadata_pass = all(
        item["units"] == "kg m-2"
        and item["accumulation_semantics"]
        == "cumulative since simulation start; no output reset; restart-persistent"
        and item["interval_semantics"]
        == "difference consecutive records gives amount over (previous_time, time]"
        for item in metadata.values()
    )
    restart_failures = (
        output_restart["failure_count"] + state_restart["failure_count"]
    )
    restart_payload = {
        "schema_version": 1,
        "status": (
            "PASS"
            if restart_failures == 0 and nonzero_runoff and metadata_pass
            else "FAIL"
        ),
        "source_commit": args.child_commit,
        "continuous_output": str(continuous_output),
        "segmented_output": str(restart_output),
        "continuous_end_restart": str(continuous_restart),
        "segmented_end_restart": str(segmented_restart),
        "compared_fields": sorted(
            set(state_restart["compared_fields"]) | CUMULATIVE_FIELDS
        ),
        "nonzero_runoff_observed": nonzero_runoff,
        "initial_cumulative_stats": initial_stats,
        "final_cumulative_stats": final_stats,
        "cumulative_metadata": metadata,
        "cumulative_metadata_pass": metadata_pass,
        "output_comparison": output_restart,
        "restart_state_comparison": state_restart,
        "failure_count": restart_failures,
    }

    build_exe = args.child_build / "HICAR_gpu"
    build_tester = args.child_build / "tests" / "HICAR-tester"
    build_payload = {
        "schema_version": 1,
        "status": "PASS",
        "job_id": args.build_job_id,
        "source_commit": args.child_commit,
        "parent_commit": PARENT_COMMIT,
        "source_tree_clean": git_clean(args.child_root),
        "target": "Balfrin NVHPC 24.5 OpenACC/NCCL release HICAR and HICAR-tester",
        "executable": {
            "path": str(build_exe),
            "sha256": sha256(build_exe),
        },
        "tester": {
            "path": str(build_tester),
            "sha256": sha256(build_tester),
        },
        "build_log": {
            "path": str(args.build_log),
            "sha256": sha256(args.build_log),
        },
        "build_script": {
            "path": str(args.build_script),
            "sha256": sha256(args.build_script),
        },
    }
    if not build_payload["source_tree_clean"]:
        build_payload["status"] = "FAIL"

    bridge_log = args.bridge_run / "model.out"
    require_log_pass(bridge_log)
    bridge_output = only_file(args.bridge_run / "output", "*.nc")
    bridge_payload = {
        "schema_version": 1,
        "status": "PASS",
        "job_id": args.bridge_job_id,
        "source_commit": args.child_commit,
        "completion_status": "PASS",
        "run_root": str(args.bridge_run),
        "model_log_sha256": sha256(bridge_log),
        "output": {
            "path": str(bridge_output),
            "size_bytes": bridge_output.stat().st_size,
            "sha256": sha256(bridge_output),
        },
    }

    national_payload = {
        "schema_version": 1,
        "status": (
            "PASS"
            if exact_payload["status"] == "PASS"
            and solver_payload["status"] == "PASS"
            and restart_payload["status"] == "PASS"
            else "FAIL"
        ),
        "source_commit": args.child_commit,
        "completion_status": "PASS",
        "run_root": str(args.run_root),
        "model_manifest_sha256": sha256(args.run_root / "model_runs.json"),
        "parent_output_size_bytes": parent_output.stat().st_size,
        "child_output_size_bytes": continuous_output.stat().st_size,
        "continuous_restart_size_bytes": continuous_restart.stat().st_size,
        "segmented_restart_size_bytes": segmented_restart.stat().st_size,
    }

    payloads = {
        "clean_target_build": build_payload,
        "restart_continuity": restart_payload,
        "representative_bridge_run": bridge_payload,
        "national_short_run": national_payload,
        "preexisting_field_equivalence": exact_payload,
        "solver_gate_equivalence": solver_payload,
    }
    evidence: dict[str, dict[str, Any]] = {}
    for name, payload in payloads.items():
        artifact = args.output_dir / f"{name}.json"
        digest = publish(artifact, payload)
        evidence[name] = {
            "status": payload["status"],
            "artifact": str(artifact),
            "artifact_sha256": digest,
        }
        for key in (
            "source_tree_clean",
            "source_commit",
            "target",
            "compared_fields",
            "nonzero_runoff_observed",
            "completion_status",
            "compared_field_count",
            "mismatch_count",
            "compared_gate_count",
        ):
            if key in payload:
                evidence[name][key] = payload[key]

    overall_pass = all(item["status"] == "PASS" for item in evidence.values())
    final = {
        "schema_version": 1,
        "status": "PASS" if overall_pass else "FAIL",
        "change_scope": "OUTPUT_DIAGNOSTIC_ONLY",
        "child_commit": args.child_commit,
        "parent_commit": PARENT_COMMIT,
        "parent_ancestry": {
            "status": "PASS",
            "parent_is_ancestor": True,
            "merge_base": merge_base,
        },
        "evidence": evidence,
    }
    final_path = args.output_dir / "month_source_qualification.json"
    publish(final_path, final)
    print(final_path)
    return 0 if overall_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
