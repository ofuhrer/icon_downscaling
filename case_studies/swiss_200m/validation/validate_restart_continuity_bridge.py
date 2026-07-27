#!/usr/bin/env python3
"""Validate the Alpine bridge segmented-vs-uninterrupted restart gate."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from netCDF4 import Dataset
import numpy as np


sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_water_budget_source_qualification import (  # noqa: E402
    PARENT_COMMIT,
    compare_tolerant,
    git_clean,
    load_tolerances,
    require_log_pass,
    revision,
)


EXPECTED_CHANGED_FILES = {
    "src/constants/icar_constants.F90",
    "src/io/default_output_metadata.F90",
    "src/physics/lsm_driver.F90",
    "src/physics/pbl_driver.F90",
}
CADENCE_STATE_FIELDS = {
    "lsm_update_phase_offset",
    "lsm_next_update_offset",
    "radiation_update_phase_offset",
    "radiation_next_update_offset",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--expected-parent", default=PARENT_COMMIT)
    parser.add_argument("--expected-changed-file", action="append", default=[])
    parser.add_argument("--model-job-id", required=True)
    parser.add_argument(
        "--scope",
        default="701_X_701_RESTART_CONTINUITY_BRIDGE",
    )
    parser.add_argument("--runner", required=True, type=Path)
    parser.add_argument("--restart-tolerances", required=True, type=Path)
    parser.add_argument("--diagnostic-policy", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def changed_files(source_root: Path, parent: str, commit: str) -> set[str]:
    output = subprocess.check_output(
        [
            "git",
            "-C",
            str(source_root),
            "diff",
            "--name-only",
            parent,
            commit,
        ],
        text=True,
    )
    return {line for line in output.splitlines() if line}


def checked_artifact(identity: dict[str, Any]) -> Path:
    path = Path(identity["path"])
    if path.stat().st_size != identity["size_bytes"]:
        raise SystemExit(f"artifact size changed: {path}")
    if sha256(path) != identity["sha256"]:
        raise SystemExit(f"artifact checksum changed: {path}")
    return path


def load_diagnostic_policy(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    payload = json.loads(path.read_text())
    if payload.get("schema_version") != 1:
        raise SystemExit("unsupported restart diagnostic policy schema")
    if payload.get("status") != "ACTIVE":
        raise SystemExit("restart diagnostic policy is not active")
    if payload.get("policy") != "WARN_ONLY_WITHIN_ALL_BOUNDS":
        raise SystemExit("unsupported restart diagnostic policy mode")
    fields = payload.get("diagnostic_fields")
    if not isinstance(fields, dict) or not fields:
        raise SystemExit("restart diagnostic policy has no diagnostic fields")
    return payload


def _effective_shape(variable: Any, last_time: bool) -> tuple[int, ...]:
    shape = tuple(variable.shape)
    if last_time and variable.dimensions and variable.dimensions[0] == "time":
        return shape[1:]
    return shape


def _chunk_keys(variable: Any, last_time: bool):
    shape = _effective_shape(variable, last_time)
    time_prefix: tuple[Any, ...] = ()
    if last_time and variable.dimensions and variable.dimensions[0] == "time":
        time_prefix = (variable.shape[0] - 1,)
    if len(shape) <= 2:
        yield time_prefix + tuple(slice(None) for _ in shape)
        return
    for prefix in itertools.product(*(range(size) for size in shape[:-2])):
        yield time_prefix + prefix + (slice(None), slice(None))


def _valid_mask(values: np.ndarray, variable: Any) -> np.ndarray:
    mask = np.isfinite(values)
    fill_value = getattr(variable, "_FillValue", None)
    if fill_value is not None:
        mask &= values != fill_value
    return mask


def difference_stats(
    reference_path: Path,
    candidate_path: Path,
    name: str,
    *,
    last_time: bool,
) -> dict[str, Any]:
    with Dataset(reference_path) as reference, Dataset(candidate_path) as candidate:
        reference.set_auto_maskandscale(False)
        candidate.set_auto_maskandscale(False)
        left = reference.variables[name]
        right = candidate.variables[name]
        if (
            left.dimensions != right.dimensions
            or _effective_shape(left, last_time) != _effective_shape(right, last_time)
            or left.dtype != right.dtype
        ):
            raise SystemExit(f"cannot calculate diagnostic statistics for {name}")
        count = 0
        signed_sum = 0.0
        squared_sum = 0.0
        max_abs = 0.0
        for left_key, right_key in zip(
            _chunk_keys(left, last_time),
            _chunk_keys(right, last_time),
            strict=True,
        ):
            left_values = np.asarray(left[left_key], dtype=np.float64)
            right_values = np.asarray(right[right_key], dtype=np.float64)
            valid = _valid_mask(left_values, left) & _valid_mask(right_values, right)
            if not np.any(valid):
                continue
            difference = right_values[valid] - left_values[valid]
            count += difference.size
            signed_sum += float(np.sum(difference, dtype=np.float64))
            squared_sum += float(
                np.sum(np.square(difference), dtype=np.float64)
            )
            max_abs = max(max_abs, float(np.max(np.abs(difference))))
    if count == 0:
        raise SystemExit(f"diagnostic field has no finite pairs: {name}")
    return {
        "finite_pair_count": count,
        "max_abs_difference": max_abs,
        "mean_signed_difference": signed_sum / count,
        "rms_difference": (squared_sum / count) ** 0.5,
    }


def apply_diagnostic_policy(
    comparison: dict[str, Any],
    reference_path: Path,
    candidate_path: Path,
    policy: dict[str, Any] | None,
    *,
    last_time: bool,
) -> list[dict[str, Any]]:
    if policy is None:
        return []
    by_name = {result["variable"]: result for result in comparison["results"]}
    evaluations: list[dict[str, Any]] = []
    for name, bounds in policy["diagnostic_fields"].items():
        result = by_name.get(name)
        if result is None:
            evaluations.append(
                {"variable": name, "status": "FAIL", "reason": "MISSING_RESULT"}
            )
            continue
        if result["status"] in {"MISSING", "STRUCTURE_MISMATCH", "DIFF"}:
            evaluations.append(
                {
                    "variable": name,
                    "status": "FAIL",
                    "reason": result["status"],
                }
            )
            continue
        stats = difference_stats(
            reference_path,
            candidate_path,
            name,
            last_time=last_time,
        )
        finite_count = result.get("finite_count", 0)
        violation_fraction = (
            result.get("violations", 0) / finite_count if finite_count else 1.0
        )
        checks = {
            "no_introduced_nonfinite": result.get("introduced_nonfinite", 1) == 0,
            "no_fill_mismatch": result.get("fill_mismatch", 1) == 0,
            "violation_fraction": (
                violation_fraction <= bounds["max_violation_fraction"]
            ),
            "max_abs_difference": (
                stats["max_abs_difference"] <= bounds["max_abs_difference"]
            ),
            "rms_difference": (
                stats["rms_difference"] <= bounds["max_rms_difference"]
            ),
            "mean_signed_difference": (
                abs(stats["mean_signed_difference"])
                <= bounds["max_abs_mean_signed_difference"]
            ),
        }
        passed = all(checks.values())
        evaluation = {
            "variable": name,
            "status": "PASS" if passed else "FAIL",
            "checks": checks,
            "observed": {
                **stats,
                "violation_fraction": violation_fraction,
            },
            "bounds": bounds,
            "original_comparison_status": result["status"],
        }
        evaluations.append(evaluation)
        if result["status"] == "FAIL" and passed:
            result["status"] = "WARN"
            result["bounded_diagnostic_policy"] = "PASS"
    comparison["failure_count"] = sum(
        item["status"] not in {"PASS", "WARN"} for item in comparison["results"]
    )
    comparison["warning_count"] = sum(
        item["status"] == "WARN" for item in comparison["results"]
    )
    return evaluations


def publish(path: Path, payload: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)
    digest = sha256(path)
    ready = Path(f"{path}.ready")
    if payload["status"] == "PASS":
        ready_tmp = ready.with_name(f".{ready.name}.tmp")
        ready_tmp.write_text(digest + "\n")
        ready_tmp.replace(ready)
    elif ready.exists():
        ready.unlink()
    return digest


def main() -> int:
    args = parse_args()
    manifest_path = args.run_root / "model_runs.json"
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("status") != "MODEL_RUNS_COMPLETE":
        raise SystemExit("bridge model-run manifest is incomplete")
    if manifest.get("source_commit") != args.expected_commit:
        raise SystemExit("bridge manifest source commit does not match")

    expected_files = (
        set(args.expected_changed_file)
        if args.expected_changed_file
        else EXPECTED_CHANGED_FILES
    )
    observed_files = changed_files(
        args.source_root,
        args.expected_parent,
        args.expected_commit,
    )
    source_pass = (
        revision(args.source_root) == args.expected_commit
        and revision(args.source_root, "HEAD^") == args.expected_parent
        and git_clean(args.source_root)
        and observed_files == expected_files
    )
    require_log_pass(args.run_root / "continuous/model.out")
    require_log_pass(args.run_root / "restart/model.out")

    continuous_output = checked_artifact(manifest["continuous_output"])
    restart_output = checked_artifact(manifest["restart_output"])
    checked_artifact(manifest["source_restart"])
    continuous_end = checked_artifact(manifest["continuous_end_restart"])
    segmented_end = checked_artifact(manifest["segmented_end_restart"])
    checked_artifact(manifest["executable"])
    checked_artifact(manifest["forcing_list"])
    checked_artifact(manifest["static_file"])

    tolerances = load_tolerances(args.restart_tolerances)
    diagnostic_policy = load_diagnostic_policy(args.diagnostic_policy)
    output_comparison = compare_tolerant(
        continuous_output,
        restart_output,
        tolerances,
        last_time=True,
    )
    state_comparison = compare_tolerant(
        continuous_end,
        segmented_end,
        tolerances,
        last_time=False,
    )
    output_diagnostic_evaluations = apply_diagnostic_policy(
        output_comparison,
        continuous_output,
        restart_output,
        diagnostic_policy,
        last_time=True,
    )
    state_diagnostic_evaluations = apply_diagnostic_policy(
        state_comparison,
        continuous_end,
        segmented_end,
        diagnostic_policy,
        last_time=False,
    )
    counter_results = [
        result
        for result in state_comparison["results"]
        if result["variable"] == "lsm_timestep_counter"
    ]
    counter_pass = (
        len(counter_results) == 1
        and counter_results[0]["status"] == "PASS"
        and counter_results[0]["max_abs_difference"] == 0.0
    )
    cadence_results = [
        result
        for result in state_comparison["results"]
        if result["variable"] in CADENCE_STATE_FIELDS
    ]
    cadence_required = "src/physics/ra_driver.F90" in expected_files
    cadence_tolerance = (
        diagnostic_policy["cadence_state_max_abs_difference_seconds"]
        if diagnostic_policy is not None
        else 0.0
    )
    cadence_pass = (
        not cadence_required
        or (
            {result["variable"] for result in cadence_results}
            == CADENCE_STATE_FIELDS
            and all(
                result["status"] == "PASS"
                and result["max_abs_difference"] <= cadence_tolerance
                for result in cadence_results
            )
        )
    )
    diagnostic_policy_pass = (
        diagnostic_policy is None
        or all(
            item["status"] == "PASS"
            for item in (
                output_diagnostic_evaluations + state_diagnostic_evaluations
            )
        )
    )
    gates = {
        "source_scope": source_pass,
        "model_runs_complete": True,
        "counter_persisted_exactly": counter_pass,
        "cadence_state_within_declared_tolerance": cadence_pass,
        "bounded_diagnostic_policy": diagnostic_policy_pass,
        "restart_output_equivalence": output_comparison["failure_count"] == 0,
        "restart_state_equivalence": state_comparison["failure_count"] == 0,
    }
    status = "PASS" if all(gates.values()) else "FAIL"
    payload = {
        "schema_version": 1,
        "status": status,
        "scope": args.scope,
        "parent_commit": args.expected_parent,
        "child_commit": args.expected_commit,
        "model_job_id": args.model_job_id,
        "run_root": str(args.run_root),
        "source": {
            "status": "PASS" if source_pass else "FAIL",
            "expected_changed_files": sorted(expected_files),
            "observed_changed_files": sorted(observed_files),
            "source_tree_clean": git_clean(args.source_root),
        },
        "runner": {
            "path": str(args.runner),
            "sha256": sha256(args.runner),
        },
        "restart_tolerances": {
            "path": str(args.restart_tolerances),
            "sha256": sha256(args.restart_tolerances),
        },
        "diagnostic_policy": (
            {
                "path": str(args.diagnostic_policy),
                "sha256": sha256(args.diagnostic_policy),
                "cadence_state_max_abs_difference_seconds": cadence_tolerance,
                "output_evaluations": output_diagnostic_evaluations,
                "state_evaluations": state_diagnostic_evaluations,
            }
            if args.diagnostic_policy is not None
            else None
        ),
        "model_manifest": {
            "path": str(manifest_path),
            "sha256": sha256(manifest_path),
        },
        "restart_source_checkpoint": manifest["source_restart"],
        "gates": gates,
        "counter_comparison": counter_results,
        "cadence_state_comparison": cadence_results,
        "restart_output_comparison": output_comparison,
        "restart_state_comparison": state_comparison,
        "interpretation": (
            "This bridge is a bounded restart-continuity gate. A PASS is "
            "required before a new Swiss national qualification, but does "
            "not itself authorize month or 100 m production. Diagnostic "
            "exceptions are non-fatal only when every hash-bound quantitative "
            "bound passes; structural, prognostic, conservation, and finite-"
            "value failures remain fatal."
        ),
    }
    digest = publish(args.output, payload)
    print(f"{status} {args.output} sha256={digest}")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
