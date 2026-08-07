#!/usr/bin/env python3
"""Assess the minimal HICAR horizontal-wind tendency correction."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from pathlib import Path
from typing import Any, Iterator

import netCDF4
import numpy as np
import yaml


EXPECTED_COMMIT = "86d6f1dd771d404a0a4a42f2b8868c14c8b97601"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qualification-root", required=True, type=Path)
    parser.add_argument("--continuous-job-id", required=True)
    parser.add_argument("--cross-node-continuation-job-id", required=True)
    parser.add_argument("--tolerance-spec", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_pass(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    if payload.get("status") != "PASS":
        raise SystemExit(f"publication is not PASS: {path}")
    if not Path(f"{path}.ready").is_file():
        raise SystemExit(f"publication has no ready marker: {path}")
    return payload


def only_file(directory: Path, pattern: str) -> Path:
    paths = sorted(directory.glob(pattern))
    if len(paths) != 1:
        raise SystemExit(
            f"expected one {pattern!r} below {directory}, found {len(paths)}"
        )
    return paths[0]


def chunk_keys(variable: Any, *, last_time: bool) -> Iterator[tuple[Any, ...]]:
    shape = variable.shape
    prefix: tuple[Any, ...] = ()
    if last_time and variable.dimensions and variable.dimensions[0] == "time":
        prefix = (shape[0] - 1,)
        shape = shape[1:]
    if len(shape) <= 2:
        yield prefix + tuple(slice(None) for _ in shape)
        return
    for indices in itertools.product(*(range(size) for size in shape[:-2])):
        yield prefix + indices + (slice(None), slice(None))


def effective_shape(variable: Any, *, last_time: bool) -> tuple[int, ...]:
    if last_time and variable.dimensions and variable.dimensions[0] == "time":
        return variable.shape[1:]
    return variable.shape


def equal_mask(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    if np.issubdtype(left.dtype, np.number):
        return (left == right) | (np.isnan(left) & np.isnan(right))
    return left == right


def compare_exact(
    reference_path: Path,
    candidate_path: Path,
    *,
    last_time: bool,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    with netCDF4.Dataset(reference_path) as reference, netCDF4.Dataset(
        candidate_path
    ) as candidate:
        reference.set_auto_maskandscale(False)
        candidate.set_auto_maskandscale(False)
        names = sorted(set(reference.variables) | set(candidate.variables))
        for name in names:
            if name not in reference.variables or name not in candidate.variables:
                results.append({"variable": name, "status": "MISSING"})
                continue
            left = reference.variables[name]
            right = candidate.variables[name]
            if (
                left.dimensions != right.dimensions
                or effective_shape(left, last_time=last_time)
                != effective_shape(right, last_time=last_time)
                or left.dtype != right.dtype
            ):
                results.append({"variable": name, "status": "STRUCTURE_MISMATCH"})
                continue
            mismatch_count = 0
            max_abs_difference = 0.0
            for left_key, right_key in zip(
                chunk_keys(left, last_time=last_time),
                chunk_keys(right, last_time=last_time),
                strict=True,
            ):
                left_values = np.asarray(left[left_key])
                right_values = np.asarray(right[right_key])
                equal = equal_mask(left_values, right_values)
                mismatch_count += int(np.count_nonzero(~equal))
                if np.issubdtype(left_values.dtype, np.number) and np.any(~equal):
                    finite = np.isfinite(left_values) & np.isfinite(right_values)
                    if np.any(finite):
                        max_abs_difference = max(
                            max_abs_difference,
                            float(
                                np.max(
                                    np.abs(
                                        right_values[finite].astype(np.float64)
                                        - left_values[finite].astype(np.float64)
                                    )
                                )
                            ),
                        )
            results.append(
                {
                    "variable": name,
                    "status": "PASS" if mismatch_count == 0 else "DIFF",
                    "mismatch_count": mismatch_count,
                    "max_abs_difference": max_abs_difference,
                }
            )
    failures = [item for item in results if item["status"] != "PASS"]
    return {
        "status": "PASS" if not failures else "FAIL",
        "compared_variable_count": len(results),
        "failure_count": len(failures),
        "failures": failures,
    }


def load_tolerances(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text()) or {}
    defaults = payload.get("defaults", {}) or {}
    variables = dict(payload.get("variables", {}) or {})
    aggregate = dict(payload.get("aggregate", {}) or {})
    variables.update(aggregate)
    return {
        "defaults": {
            "rtol": float(defaults.get("rtol", 0.0)),
            "atol": float(defaults.get("atol", 0.0)),
            "frac": float(defaults.get("frac", 0.0)),
        },
        "variables": variables,
        "aggregate": set(aggregate),
        "warn_only": set(payload.get("warn_only", []) or []),
    }


def valid_mask(values: np.ndarray, variable: Any) -> np.ndarray:
    valid = np.isfinite(values)
    fill_value = getattr(variable, "_FillValue", None)
    if fill_value is not None:
        valid &= values != fill_value
    return valid


def compare_tolerant(
    reference_path: Path,
    candidate_path: Path,
    spec: dict[str, Any],
    *,
    last_time: bool,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    with netCDF4.Dataset(reference_path) as reference, netCDF4.Dataset(
        candidate_path
    ) as candidate:
        reference.set_auto_maskandscale(False)
        candidate.set_auto_maskandscale(False)
        names = sorted(set(reference.variables) | set(candidate.variables))
        for name in names:
            if name not in reference.variables or name not in candidate.variables:
                results.append({"variable": name, "status": "MISSING"})
                continue
            left = reference.variables[name]
            right = candidate.variables[name]
            if (
                left.dimensions != right.dimensions
                or effective_shape(left, last_time=last_time)
                != effective_shape(right, last_time=last_time)
                or left.dtype != right.dtype
            ):
                results.append({"variable": name, "status": "STRUCTURE_MISMATCH"})
                continue
            if not np.issubdtype(left.dtype, np.number):
                exact = all(
                    np.array_equal(np.asarray(left[key]), np.asarray(right[rkey]))
                    for key, rkey in zip(
                        chunk_keys(left, last_time=last_time),
                        chunk_keys(right, last_time=last_time),
                        strict=True,
                    )
                )
                results.append(
                    {"variable": name, "status": "PASS" if exact else "DIFF"}
                )
                continue

            rule = dict(spec["defaults"])
            rule.update(spec["variables"].get(name, {}) or {})
            rtol = float(rule["rtol"])
            atol = float(rule["atol"])
            fraction = float(rule["frac"])
            aggregate = name in spec["aggregate"]
            finite_count = 0
            violations = 0
            fill_mismatch = 0
            introduced_nonfinite = 0
            max_abs = 0.0
            left_sum = 0.0
            right_sum = 0.0
            for key, rkey in zip(
                chunk_keys(left, last_time=last_time),
                chunk_keys(right, last_time=last_time),
                strict=True,
            ):
                a = np.asarray(left[key], dtype=np.float64)
                b = np.asarray(right[rkey], dtype=np.float64)
                left_valid = valid_mask(a, left)
                right_valid = valid_mask(b, right)
                fill_mismatch += int(np.count_nonzero(left_valid != right_valid))
                introduced_nonfinite += int(
                    np.count_nonzero(~np.isfinite(b) & np.isfinite(a))
                )
                valid = left_valid & right_valid
                if not np.any(valid):
                    continue
                finite_count += int(np.count_nonzero(valid))
                difference = np.abs(b - a)
                max_abs = max(max_abs, float(np.max(difference[valid])))
                if aggregate:
                    left_sum += float(np.sum(a[valid], dtype=np.float64))
                    right_sum += float(np.sum(b[valid], dtype=np.float64))
                else:
                    threshold = atol + rtol * np.abs(a)
                    violations += int(np.count_nonzero(valid & (difference > threshold)))
            if aggregate and finite_count:
                left_mean = left_sum / finite_count
                right_mean = right_sum / finite_count
                violations = int(
                    abs(right_mean - left_mean) > atol + rtol * abs(left_mean)
                )
            allowed = int(fraction * finite_count)
            failed = (
                fill_mismatch > 0
                or introduced_nonfinite > 0
                or violations > allowed
            )
            status = "PASS"
            if failed:
                status = "WARN" if name in spec["warn_only"] else "FAIL"
            results.append(
                {
                    "variable": name,
                    "status": status,
                    "aggregate": aggregate,
                    "rtol": rtol,
                    "atol": atol,
                    "allowed_violation_fraction": fraction,
                    "finite_count": finite_count,
                    "violations": violations,
                    "allowed_violations": allowed,
                    "fill_mismatch": fill_mismatch,
                    "introduced_nonfinite": introduced_nonfinite,
                    "max_abs_difference": max_abs,
                }
            )
    failures = [item for item in results if item["status"] not in {"PASS", "WARN"}]
    warnings = [item for item in results if item["status"] == "WARN"]
    return {
        "status": "PASS" if not failures else "FAIL",
        "compared_variable_count": len(results),
        "failure_count": len(failures),
        "warning_count": len(warnings),
        "failures": failures,
        "warnings": warnings,
    }


def temporal_change(path: Path, name: str) -> dict[str, Any]:
    with netCDF4.Dataset(path) as dataset:
        dataset.set_auto_maskandscale(False)
        variable = dataset.variables[name]
        if not variable.dimensions or variable.dimensions[0] != "time":
            raise SystemExit(f"{name} has no leading time dimension in {path}")
        if variable.shape[0] < 2:
            raise SystemExit(f"{name} has fewer than two records in {path}")
        changed = 0
        finite_count = 0
        max_abs = 0.0
        shape = variable.shape[1:]
        keys: Iterator[tuple[Any, ...]]
        if len(shape) <= 2:
            keys = iter((tuple(slice(None) for _ in shape),))
        else:
            keys = (
                indices + (slice(None), slice(None))
                for indices in itertools.product(*(range(size) for size in shape[:-2]))
            )
        for key in keys:
            first = np.asarray(variable[(0,) + key], dtype=np.float64)
            last = np.asarray(variable[(-1,) + key], dtype=np.float64)
            finite = np.isfinite(first) & np.isfinite(last)
            finite_count += int(np.count_nonzero(finite))
            if np.any(finite):
                difference = np.abs(last[finite] - first[finite])
                changed += int(np.count_nonzero(difference > 0.0))
                max_abs = max(max_abs, float(np.max(difference)))
    return {
        "finite_pair_count": finite_count,
        "changed_cell_count": changed,
        "changed_fraction": changed / finite_count if finite_count else 0.0,
        "max_abs_difference": max_abs,
        "status": "PASS" if changed > 0 and max_abs > 1.0e-6 else "FAIL",
    }


def restart_change(first_path: Path, last_path: Path, name: str) -> dict[str, Any]:
    with netCDF4.Dataset(first_path) as first, netCDF4.Dataset(last_path) as last:
        first.set_auto_maskandscale(False)
        last.set_auto_maskandscale(False)
        left = first.variables[name]
        right = last.variables[name]
        if left.dimensions != right.dimensions or left.shape != right.shape:
            raise SystemExit(f"restart structure changed for {name}")
        changed = 0
        finite_count = 0
        max_abs = 0.0
        for left_key, right_key in zip(
            chunk_keys(left, last_time=False),
            chunk_keys(right, last_time=False),
            strict=True,
        ):
            a = np.asarray(left[left_key], dtype=np.float64)
            b = np.asarray(right[right_key], dtype=np.float64)
            finite = np.isfinite(a) & np.isfinite(b)
            finite_count += int(np.count_nonzero(finite))
            if np.any(finite):
                difference = np.abs(b[finite] - a[finite])
                changed += int(np.count_nonzero(difference > 0.0))
                max_abs = max(max_abs, float(np.max(difference)))
    return {
        "finite_pair_count": finite_count,
        "changed_cell_count": changed,
        "changed_fraction": changed / finite_count if finite_count else 0.0,
        "max_abs_difference": max_abs,
        "status": "PASS" if changed > 0 and max_abs > 1.0e-6 else "FAIL",
    }


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
    root = args.qualification_root
    source = load_pass(root / "source_provenance.json")
    halo = load_pass(root / "halo_qualification.json")
    if source.get("source_commit") != EXPECTED_COMMIT:
        raise SystemExit("unexpected corrected source commit")
    if halo.get("source_commit") != EXPECTED_COMMIT:
        raise SystemExit("halo qualification used another source commit")

    runs = {
        name: load_pass(root / "runs" / name / "run/model_chunk_completion.json")
        for name in ("checkpoints", "continuation-crossnodes")
    }
    continuous_output = only_file(root / "runs/checkpoints/run/output", "*.nc")
    continuation_output = only_file(
        root / "runs/continuation-crossnodes/run/output", "*.nc"
    )
    continuous_restarts = sorted((root / "runs/checkpoints/restart").glob("*.nc"))
    branch_restart = [path for path in continuous_restarts if "02-00-00" in path.name]
    continuous_restart = [path for path in continuous_restarts if "03-00-00" in path.name]
    continuation_restarts = sorted(
        (root / "runs/continuation-crossnodes/restart").glob("*.nc")
    )
    continuation_restart = [
        path for path in continuation_restarts if "03-00-00" in path.name
    ]
    if not all(
        len(paths) == 1
        for paths in (branch_restart, continuous_restart, continuation_restart)
    ):
        raise SystemExit("could not identify branch and final restart checkpoints")
    branch_restart = branch_restart[0]
    continuous_restart = continuous_restart[0]
    continuation_restart = continuation_restart[0]

    history_evolution = {
        name: temporal_change(continuous_output, name)
        for name in ("u_agl", "v_agl", "u10m", "v10m")
    }
    native_evolution = {
        name: restart_change(branch_restart, continuous_restart, name)
        for name in ("u", "v")
    }
    tolerances = load_tolerances(args.tolerance_spec)
    restart_exact = compare_exact(
        continuous_restart, continuation_restart, last_time=False
    )
    output_exact = compare_exact(
        continuous_output, continuation_output, last_time=True
    )
    restart_equivalence = compare_tolerant(
        continuous_restart,
        continuation_restart,
        tolerances,
        last_time=False,
    )
    output_equivalence = compare_tolerant(
        continuous_output,
        continuation_output,
        tolerances,
        last_time=True,
    )
    build_report = root / "build-gpu-nccl/hicar_build_provenance.txt"
    build_ready = Path(f"{build_report}.ready")
    gates = {
        "source_provenance": source["status"] == "PASS",
        "build_provenance": build_report.is_file() and build_ready.is_file(),
        "four_gpu_halo": halo["status"] == "PASS",
        "all_model_chunks": all(run.get("status") == "PASS" for run in runs.values()),
        "fixed_height_and_10m_wind_evolve": all(
            result["status"] == "PASS" for result in history_evolution.values()
        ),
        "native_horizontal_wind_evolves": all(
            result["status"] == "PASS" for result in native_evolution.values()
        ),
        "split_restart_within_declared_tolerance": (
            restart_equivalence["status"] == "PASS"
        ),
        "split_final_output_within_declared_tolerance": (
            output_equivalence["status"] == "PASS"
        ),
    }
    status = "PASS" if all(gates.values()) else "FAIL"
    payload = {
        "schema_version": 1,
        "status": status,
        "purpose": "minimal-horizontal-wind-tendency-correction-qualification",
        "source_commit": EXPECTED_COMMIT,
        "job_ids": {
            "continuous_with_hourly_checkpoints": args.continuous_job_id,
            "cross_node_continuation_from_own_checkpoint": (
                args.cross_node_continuation_job_id
            ),
            "halo": halo["job_id"],
        },
        "artifacts": {
            "source_provenance": str(root / "source_provenance.json"),
            "source_provenance_sha256": sha256(root / "source_provenance.json"),
            "build_provenance": str(build_report),
            "build_provenance_sha256": sha256(build_report),
            "halo_qualification": str(root / "halo_qualification.json"),
            "halo_qualification_sha256": sha256(root / "halo_qualification.json"),
            "restart_tolerance_spec": str(args.tolerance_spec),
            "restart_tolerance_spec_sha256": sha256(args.tolerance_spec),
            "branch_restart": str(branch_restart),
            "continuous_final_restart": str(continuous_restart),
            "continuation_final_restart": str(continuation_restart),
        },
        "gates": gates,
        "history_wind_evolution": history_evolution,
        "native_restart_wind_evolution": native_evolution,
        "split_restart_equivalence": restart_equivalence,
        "split_final_output_equivalence": output_equivalence,
        "split_restart_exact_diagnostic": restart_exact,
        "split_final_output_exact_diagnostic": output_exact,
        "interpretation": (
            "A PASS establishes that the existing adjusted horizontal-wind "
            "tendencies advance native and fixed-height wind and that continuation "
            "from a run's own one-hour checkpoint on a different node allocation "
            "remains within HICAR's declared restart integration tolerance. Exact "
            "differences remain diagnostic and define an allocation/restart noise floor. "
            "It authorizes bounded corrected science experiments, not production."
        ),
    }
    digest = publish(args.output, payload)
    print(f"{status} {args.output} sha256={digest}")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
