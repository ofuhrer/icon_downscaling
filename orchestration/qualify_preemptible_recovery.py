#!/usr/bin/env python3
"""Run an engineering-only Balfrin cancellation and hard-kill recovery drill."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import preemptible_campaign as controller
from runtime_contract import (
    sha256,
    validate_python_environment,
    validate_runtime_release,
)


def publish(path: Path, payload: dict[str, Any]) -> None:
    controller.write_json_atomic(path, payload)
    Path(f"{path}.ready").touch()


def scheduler_state(job_id: str) -> dict[str, str] | None:
    return controller.Slurm().query([job_id]).get(job_id)


def wait_for(
    job_id: str,
    accepted: set[str],
    timeout_seconds: int,
) -> dict[str, str]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        record = scheduler_state(job_id)
        if record and controller.normalized_state(record["state"]) in accepted:
            return record
        time.sleep(5)
    raise TimeoutError(
        f"job {job_id} did not reach {sorted(accepted)} within {timeout_seconds} seconds"
    )


def wait_for_path(path: Path, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if path.is_file():
            return
        time.sleep(1)
    raise TimeoutError(f"probe did not publish startup marker: {path}")


def cancel(job_id: str, signal_name: str) -> None:
    subprocess.run(
        ["scancel", f"--signal={signal_name}", "--batch", job_id],
        check=True,
        timeout=30,
    )


def make_campaign(
    repo_root: Path,
    work_root: Path,
    python_report: Path,
) -> Path:
    runtime_manifest = repo_root / "runtime_release.json"
    runtime = validate_runtime_release(runtime_manifest, expected_root=repo_root)
    python_environment = validate_python_environment(
        python_report,
        runtime_manifest,
        smoke=True,
    )
    forcing_root = work_root / "forcing"
    forcing_root.mkdir(parents=True)
    cache_root = work_root / "forcing_cache"
    records_root = cache_root / "records"
    producer_root = cache_root / "producer"
    cache_index = cache_root / "index.json"
    publish(
        cache_index,
        {
            "schema_version": 1,
            "status": "PLANNED",
            "campaign_id": "preemption-recovery-probe",
            "shared": True,
            "records_root": str(records_root),
            "producer_root": str(producer_root),
            "static_file": str(work_root / "unused-static.nc"),
            "record_count": 0,
            "records": [],
        },
    )
    plan = forcing_root / "chunk_plan.json"
    publish(
        plan,
        {
            "schema_version": 1,
            "status": "PLANNED",
            "chunk_id": "preemption-recovery-probe",
            "chunk_root": str(forcing_root),
            "producer_root": str(producer_root),
            "forcing_cache": {
                "shared": True,
                "records_root": str(records_root),
                "producer_root": str(producer_root),
            },
            "start": "2000-01-01T00:00:00",
            "end": "2000-01-01T01:00:00",
            "hours": 1,
            "records": [],
        },
    )
    forcing_publication = forcing_root / "forcing_publication.json"
    publish(
        forcing_publication,
        {
            "schema_version": 1,
            "status": "PASS",
            "chunk_id": "preemption-recovery-probe",
            "plan_sha256": sha256(plan),
            "entries": [],
        },
    )
    segment_root = work_root / "chains/probe/segments/00001"
    campaign_path = work_root / "campaign_plan.json"
    campaign = {
        "schema_version": 1,
        "status": "PLANNED",
        "campaign_id": "preemption-recovery-probe",
        "purpose": "qualification",
        "campaign_root": str(work_root),
        "definition": None,
        "definition_sha256": None,
        "runtime_release": {
            "path": str(runtime_manifest),
            "sha256": sha256(runtime_manifest),
            "release_root": str(repo_root),
            "purpose": runtime["purpose"],
            "source_commit": runtime["source_commit"],
            "source_dirty": runtime["source_dirty"],
        },
        "python_environment": {
            "path": str(python_report),
            "sha256": sha256(python_report),
            "python": python_environment["python"],
            "python_version": python_environment["python_version"],
            "requirements_sha256": python_environment["requirements_sha256"],
        },
        "goal": {
            "outcome": "Exercise cancellation recovery without running HICAR.",
            "why_now": "Scheduler recovery must work before model resources rely on it.",
            "evidence_needed": [
                "A classified graceful interruption",
                "A classified hard kill and a fresh retry",
            ],
            "stop_conditions": [
                "Stop after the recovery sequence is observed",
                "Stop if an outcome cannot be classified safely",
            ],
            "resource_rationale": "The scheduler probe needs one node and no scientific model integration.",
        },
        "model": {
            "partition": "preemptible",
            "nodes": 1,
            "time_limit": "00:10:00",
            "case_root": str(work_root / "unused-case"),
            "hicar_root": str(work_root / "unused-HICAR"),
            "static_file": str(work_root / "unused-static.nc"),
            "expected_hicar_commit": "0" * 40,
            "output_interval_seconds": 3600,
            "output_profile": "routine",
            "script": str(
                repo_root / "case_studies/swiss_200m/scripts/"
                "run_preemptible_recovery_probe_balfrin.sbatch"
            ),
        },
        "policy": {
            "segment_hours": 1,
            "model_node_budget": 1,
            "model_slots": 1,
            "cpu_slots": 1,
            "shared_forcing_cache": True,
            "input_task_weight": 3,
            "post_task_weight": 1,
            "prefetch_segments_per_chain": 0,
            "max_model_attempts": 0,
            "max_cpu_attempts": 1,
            "lease_seconds": 60,
            "rolling_retirement": True,
            "preserve_restart_every_segments": 0,
            "max_unretired_segments_per_chain": 1,
        },
        "forcing_cache": {
            "shared": True,
            "root": str(cache_root),
            "records_root": str(records_root),
            "producer_root": str(producer_root),
            "static_file": str(work_root / "unused-static.nc"),
            "index": str(cache_index),
            "index_sha256": sha256(cache_index),
        },
        "chains": [
            {
                "chain_id": "probe",
                "segments": [
                    {
                        "sequence": 1,
                        "segment_id": "preemption-recovery-probe",
                        "start": "2000-01-01T00:00:00",
                        "end": "2000-01-01T01:00:00",
                        "hours": 1,
                        "plan": str(plan),
                        "plan_sha256": sha256(plan),
                        "forcing_publication": str(forcing_publication),
                        "attempt_root": str(segment_root / "attempts"),
                        "compressed_root": str(segment_root / "compressed"),
                        "lifecycle_root": str(segment_root / "lifecycle"),
                        "rea_l_land_initialization": True,
                    }
                ],
            }
        ],
        "controller": {
            "state": str(work_root / "controller_state.json"),
            "lease": str(work_root / "controller_state.lease"),
            "cpu_task_root": str(work_root / "cpu_tasks"),
        },
    }
    publish(campaign_path, campaign)
    return campaign_path


def drill(
    repo_root: Path,
    work_root: Path,
    python_report: Path,
    start_timeout: int,
    terminal_timeout: int,
) -> dict[str, Any]:
    if work_root.exists():
        raise ValueError(f"probe work root already exists: {work_root}")
    scratch_value = os.environ.get("SCRATCH")
    if not scratch_value:
        raise ValueError("$SCRATCH is not set")
    scratch = Path(scratch_value).resolve()
    if scratch not in work_root.resolve().parents:
        raise ValueError("probe work root must be a new directory below $SCRATCH")
    campaign_path = make_campaign(repo_root, work_root, python_report)
    submitted: list[str] = []
    try:
        state, _ = controller.reconcile(
            campaign_path=campaign_path,
            repo_root=repo_root,
            scheduler=controller.Slurm(),
            execute=True,
        )
        runtime = state["chains"]["probe"]["segments"][0]
        first = runtime["attempts"][-1]
        submitted.append(first["job_id"])
        wait_for(first["job_id"], {"RUNNING"}, start_timeout)
        wait_for_path(
            Path(first["run_dir"]) / "probe_started.ready",
            timeout_seconds=60,
        )
        cancel(first["job_id"], "TERM")
        first_terminal = wait_for(
            first["job_id"],
            {
                "CANCELLED",
                "FAILED",
                "PREEMPTED",
            },
            terminal_timeout,
        )
        interruption = Path(first["run_dir"]) / "attempt_interrupted.json"
        deadline = time.monotonic() + 30
        while not interruption.is_file() and time.monotonic() < deadline:
            time.sleep(1)
        if not interruption.is_file():
            raise ValueError("SIGTERM attempt did not publish interruption evidence")

        state, _ = controller.reconcile(
            campaign_path=campaign_path,
            repo_root=repo_root,
            scheduler=controller.Slurm(),
            execute=True,
        )
        runtime = state["chains"]["probe"]["segments"][0]
        second = runtime["attempts"][-1]
        if second["attempt_id"] == first["attempt_id"]:
            raise ValueError("controller did not create an immutable retry")
        submitted.append(second["job_id"])
        wait_for(second["job_id"], {"RUNNING"}, start_timeout)
        wait_for_path(
            Path(second["run_dir"]) / "probe_started.ready",
            timeout_seconds=60,
        )
        cancel(second["job_id"], "KILL")
        second_terminal = wait_for(
            second["job_id"],
            {
                "CANCELLED",
                "FAILED",
                "PREEMPTED",
            },
            terminal_timeout,
        )
        hard_kill_interruption = Path(second["run_dir"]) / "attempt_interrupted.json"
        if hard_kill_interruption.exists():
            raise ValueError("hard-killed attempt unexpectedly ran signal cleanup")

        state, _ = controller.reconcile(
            campaign_path=campaign_path,
            repo_root=repo_root,
            scheduler=controller.Slurm(),
            execute=True,
        )
        runtime = state["chains"]["probe"]["segments"][0]
        third = runtime["attempts"][-1]
        if third["attempt_id"] in {first["attempt_id"], second["attempt_id"]}:
            raise ValueError("hard kill did not create another immutable retry")
        submitted.append(third["job_id"])
        controller.set_capacity(campaign_path, models=0, cpus=0)
        subprocess.run(["scancel", third["job_id"]], check=False, timeout=30)

        return {
            "schema_version": 1,
            "status": "PASS",
            "assessment": "ENGINEERING_ONLY",
            "scope": "scheduler_and_controller_recovery",
            "observed_capability": (
                "The controller classifies graceful and hard interruptions "
                "and creates a fresh immutable retry."
            ),
            "completed_at": datetime.now(UTC).isoformat(),
            "campaign": str(campaign_path),
            "campaign_sha256": sha256(campaign_path),
            "runtime_release": str(repo_root / "runtime_release.json"),
            "runtime_release_sha256": sha256(repo_root / "runtime_release.json"),
            "sigterm": {
                "job_id": first["job_id"],
                "attempt_id": first["attempt_id"],
                "terminal": first_terminal,
                "interruption_report": str(interruption),
                "interruption_report_sha256": sha256(interruption),
            },
            "hard_kill": {
                "job_id": second["job_id"],
                "attempt_id": second["attempt_id"],
                "terminal": second_terminal,
                "interruption_report_absent": True,
            },
            "recovery": {
                "job_id": third["job_id"],
                "attempt_id": third["attempt_id"],
                "immutable_attempt_directories": len(
                    {
                        first["attempt_dir"],
                        second["attempt_dir"],
                        third["attempt_dir"],
                    }
                )
                == 3,
                "capacity_paused_after_probe": True,
            },
        }
    finally:
        for job_id in submitted:
            subprocess.run(["scancel", job_id], check=False, timeout=30)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument("--python-environment", required=True, type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--start-timeout", type=int, default=1800)
    parser.add_argument("--terminal-timeout", type=int, default=180)
    args = parser.parse_args()
    if not args.execute:
        print(
            json.dumps(
                {
                    "status": "DRY_RUN",
                    "assessment": "ENGINEERING_ONLY",
                    "repo_root": str(args.repo_root.resolve()),
                    "work_root": str(args.work_root.resolve()),
                    "python_environment": str(args.python_environment.resolve()),
                    "actions": [
                        "submit one-node preemptible sleep attempt",
                        "send SIGTERM and verify interruption report",
                        "verify immutable retry",
                        "send SIGKILL and verify recovery without signal report",
                        "pause and cancel the final probe retry",
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    result = drill(
        args.repo_root.resolve(),
        args.work_root.resolve(),
        args.python_environment.resolve(),
        args.start_timeout,
        args.terminal_timeout,
    )
    report = args.work_root.resolve() / "preemption_recovery_engineering.json"
    publish(report, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
