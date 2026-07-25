#!/usr/bin/env python3
"""Publish the isolated HICAR restart-initialization source qualification."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_water_budget_source_qualification import (  # noqa: E402
    PARENT_COMMIT,
    compare_exact,
    compare_tolerant,
    gate_lines,
    git_clean,
    load_tolerances,
    only_file,
    require_log_pass,
    revision,
)


EXPECTED_CHANGED_FILES = {
    "src/physics/lsm_driver.F90",
    "src/physics/pbl_driver.F90",
}


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
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def artifact(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def changed_files(child_root: Path, child_commit: str) -> set[str]:
    output = subprocess.check_output(
        [
            "git",
            "-C",
            str(child_root),
            "diff",
            "--name-only",
            PARENT_COMMIT,
            child_commit,
        ],
        text=True,
    )
    return {line for line in output.splitlines() if line}


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
    ancestry = {
        "parent": revision(args.child_root, "HEAD^"),
        "merge_base": subprocess.check_output(
            [
                "git",
                "-C",
                str(args.child_root),
                "merge-base",
                PARENT_COMMIT,
                args.child_commit,
            ],
            text=True,
        ).strip(),
    }
    observed_changed_files = changed_files(args.child_root, args.child_commit)
    source_pass = (
        revision(args.parent_root) == PARENT_COMMIT
        and revision(args.child_root) == args.child_commit
        and ancestry["parent"] == PARENT_COMMIT
        and ancestry["merge_base"] == PARENT_COMMIT
        and observed_changed_files == EXPECTED_CHANGED_FILES
        and git_clean(args.child_root)
    )

    manifest_path = args.run_root / "model_runs.json"
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("status") != "MODEL_RUNS_COMPLETE":
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
    start_restart = only_file(
        continuous_run / "restart", "*_2020-07-01_01-00-00.nc"
    )
    segmented_restart = only_file(
        restart_run / "restart", "*_2020-07-01_02-00-00.nc"
    )
    parent_restart = only_file(
        parent_run / "restart", "*_2020-07-01_02-00-00.nc"
    )

    exact = compare_exact(
        parent_output,
        continuous_output,
        expected_candidate_only_fields=set(),
    )
    tolerances = load_tolerances(args.restart_tolerances)
    cold_tolerant = compare_tolerant(
        parent_output,
        continuous_output,
        tolerances,
        last_time=False,
    )
    output_restart = compare_tolerant(
        continuous_output,
        restart_output,
        tolerances,
        last_time=True,
    )
    state_restart = compare_tolerant(
        continuous_restart,
        segmented_restart,
        tolerances,
        last_time=False,
    )
    parent_gates = gate_lines(parent_run / "model.out")
    child_gates = gate_lines(continuous_run / "model.out")
    solver_gate_pass = bool(parent_gates) and parent_gates == child_gates

    bridge_log = args.bridge_run / "model.out"
    require_log_pass(bridge_log)
    bridge_output = only_file(args.bridge_run / "output", "*.nc")
    bridge_source = (args.bridge_run / "source_commit.txt").read_text().strip()
    build_executable = args.child_build / "HICAR_gpu"
    build_tester = args.child_build / "tests" / "HICAR-tester"

    gates = {
        "source_scope": source_pass,
        "clean_target_build": (
            args.build_log.is_file()
            and build_executable.is_file()
            and build_tester.is_file()
        ),
        "representative_bridge": (
            bridge_output.is_file() and bridge_source == args.child_commit
        ),
        "cold_start_exact_equivalence": exact["mismatch_count"] == 0,
        "cold_start_tolerant_equivalence": cold_tolerant["failure_count"] == 0,
        "solver_gate_equivalence": solver_gate_pass,
        "restart_output_equivalence": output_restart["failure_count"] == 0,
        "restart_state_equivalence": state_restart["failure_count"] == 0,
    }
    status = "PASS" if all(gates.values()) else "FAIL"
    payload = {
        "schema_version": 1,
        "status": status,
        "change_scope": "RESTART_INITIALIZATION_ONLY",
        "parent_commit": PARENT_COMMIT,
        "child_commit": args.child_commit,
        "source": {
            "status": "PASS" if source_pass else "FAIL",
            "parent_is_direct": ancestry["parent"] == PARENT_COMMIT,
            "merge_base": ancestry["merge_base"],
            "source_tree_clean": git_clean(args.child_root),
            "expected_changed_files": sorted(EXPECTED_CHANGED_FILES),
            "observed_changed_files": sorted(observed_changed_files),
        },
        "gates": gates,
        "build": {
            "job_id": args.build_job_id,
            "log": str(args.build_log),
            "log_sha256": sha256(args.build_log),
            "script": str(args.build_script),
            "script_sha256": sha256(args.build_script),
            "executable": str(build_executable),
            "executable_sha256": sha256(build_executable),
            "tester": str(build_tester),
            "tester_sha256": sha256(build_tester),
        },
        "representative_bridge": {
            "job_id": args.bridge_job_id,
            "source_commit": bridge_source,
            "run_root": str(args.bridge_run),
            "model_log_sha256": sha256(bridge_log),
            "output": str(bridge_output),
            "output_size_bytes": bridge_output.stat().st_size,
            "output_sha256": sha256(bridge_output),
        },
        "national_short_run": {
            "run_root": str(args.run_root),
            "manifest_sha256": sha256(manifest_path),
            "parent_output": artifact(parent_output),
            "parent_end_restart": artifact(parent_restart),
            "child_continuous_output": artifact(continuous_output),
            "child_restart_output": artifact(restart_output),
            "restart_source_checkpoint": artifact(start_restart),
            "continuous_end_restart": artifact(continuous_restart),
            "segmented_end_restart": artifact(segmented_restart),
        },
        "cold_start_exact_comparison": exact,
        "cold_start_tolerant_comparison": cold_tolerant,
        "solver_gate_comparison": {
            "status": "PASS" if solver_gate_pass else "FAIL",
            "compared_gate_count": len(parent_gates),
            "mismatch_count": 0 if solver_gate_pass else 1,
            "parent_gate_lines": parent_gates,
            "child_gate_lines": child_gates,
        },
        "restart_output_comparison": output_restart,
        "restart_state_comparison": state_restart,
        "interpretation": (
            "This qualification isolates restart initialization and adds no "
            "scientific output fields. It does not authorize the month stage; "
            "the cumulative water-budget observables remain a separate gate."
        ),
    }
    digest = publish(args.output, payload)
    print(f"{status} {args.output} sha256={digest}")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
