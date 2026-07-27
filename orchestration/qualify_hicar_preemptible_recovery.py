#!/usr/bin/env python3
"""Qualify real HICAR SIGTERM/SIGKILL recovery on two short segments."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import preemptible_campaign as controller


TERMINAL_STATES = {
    "BOOT_FAIL",
    "CANCELLED",
    "COMPLETED",
    "FAILED",
    "NODE_FAIL",
    "OUT_OF_MEMORY",
    "PREEMPTED",
    "TIMEOUT",
}


def published_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file() or not Path(f"{path}.ready").is_file():
        raise ValueError(f"{label} is not published: {path}")
    return json.loads(path.read_text())


def validate_campaign(campaign_path: Path) -> dict[str, Any]:
    campaign = published_json(campaign_path, "campaign plan")
    chains = campaign.get("chains", [])
    if campaign.get("purpose") != "qualification":
        raise ValueError("real recovery drill requires purpose=qualification")
    if len(chains) != 1 or len(chains[0].get("segments", [])) != 2:
        raise ValueError("real recovery drill requires one chain of two segments")
    if campaign.get("model", {}).get("partition") != "preemptible":
        raise ValueError("real recovery drill requires the preemptible partition")
    if int(campaign["model"].get("nodes", 0)) != 4:
        raise ValueError("real recovery drill requires the four-node topology")
    if campaign["model"].get("output_profile") != "routine":
        raise ValueError(
            "real recovery drill requires routine output; scientific "
            "qualification is a separate gate"
        )
    maximum_attempts = int(campaign["policy"].get("max_model_attempts", 0))
    if maximum_attempts != 0 and maximum_attempts < 3:
        raise ValueError("real recovery drill requires at least three attempts")
    first, second = chains[0]["segments"]
    if first["end"] != second["start"]:
        raise ValueError("real recovery drill segments are not adjacent")
    if not first.get("rea_l_land_initialization"):
        raise ValueError("first drill segment must initialize REA-L land state")
    if second.get("rea_l_land_initialization"):
        raise ValueError("continuation drill segment must not reinitialize land state")
    state_path = Path(campaign["controller"]["state"])
    if state_path.exists():
        state = json.loads(state_path.read_text())
        attempts = [
            attempt
            for chain in state.get("chains", {}).values()
            for segment in chain.get("segments", [])
            for attempt in segment.get("attempts", [])
        ]
        if (
            state.get("campaign_sha256") != controller.sha256(campaign_path)
            or attempts
            or state.get("cpu_batch")
        ):
            raise ValueError(
                "real recovery drill requires a fresh or dry-inspected campaign"
            )
    return campaign


def scheduler_terminal(job_id: str, timeout_seconds: int) -> dict[str, str]:
    scheduler = controller.Slurm()
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        record = scheduler.query([job_id]).get(job_id)
        if (
            record
            and controller.normalized_state(record["state"]) in TERMINAL_STATES
        ):
            return record
        time.sleep(3)
    raise TimeoutError(f"job {job_id} did not become terminal")


def cancel(job_id: str, signal_name: str) -> None:
    subprocess.run(
        ["scancel", f"--signal={signal_name}", "--batch", job_id],
        check=True,
        timeout=30,
    )


def model_started(attempt: dict[str, Any]) -> bool:
    model_log = Path(attempt["run_dir"]) / "model.out"
    if model_log.is_file() and model_log.stat().st_size > 0:
        return True
    job_id = str(attempt["job_id"])
    result = subprocess.run(
        ["squeue", "--steps", f"--jobs={job_id}", "--noheader", "--format=%i"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return f"{job_id}.0" in result.stdout.split()


def active_job_ids(state: dict[str, Any]) -> list[str]:
    values = []
    for job_id in controller.job_ids(state):
        if str(job_id).isdigit():
            values.append(str(job_id))
    return values


def wait_for_interruption(path: Path, timeout_seconds: int = 60) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if path.is_file():
            return
        time.sleep(1)
    raise ValueError(f"SIGTERM attempt did not publish interruption report: {path}")


def matching_restart_evidence(
    first_completion_path: Path,
    second_completion_path: Path,
) -> dict[str, Any]:
    first = published_json(first_completion_path, "predecessor completion")
    second = published_json(second_completion_path, "recovered completion")
    restart_input = second.get("restart_input")
    if not isinstance(restart_input, dict):
        raise ValueError("recovered completion has no restart input evidence")
    if (
        restart_input.get("path") != first.get("restart", {}).get("path")
        or restart_input.get("sha256") != first.get("restart", {}).get("sha256")
        or restart_input.get("publication")
        != str(first_completion_path.resolve())
        or restart_input.get("publication_sha256")
        != controller.sha256(first_completion_path)
    ):
        raise ValueError(
            "recovered completion is not bound to the predecessor restart"
        )
    return restart_input


def run_drill(
    *,
    campaign_path: Path,
    repo_root: Path,
    report: Path,
    poll_seconds: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    campaign = validate_campaign(campaign_path)
    if report.exists() or Path(f"{report}.ready").exists():
        raise ValueError(f"refusing to replace recovery report: {report}")
    deadline = time.monotonic() + timeout_seconds
    cancellations: list[dict[str, Any]] = []
    cancellation_plan = ("TERM", "KILL")
    state: dict[str, Any] | None = None
    try:
        while time.monotonic() < deadline:
            state, _ = controller.reconcile(
                campaign_path=campaign_path,
                repo_root=repo_root,
                scheduler=controller.Slurm(),
                execute=True,
            )
            if state["status"] == "BLOCKED":
                raise ValueError(
                    "recovery campaign blocked: "
                    + "; ".join(state.get("blockers", []))
                )
            chain_id = campaign["chains"][0]["chain_id"]
            first_runtime, second_runtime = state["chains"][chain_id]["segments"]
            attempts = second_runtime["attempts"]

            cancellation_index = len(cancellations)
            if cancellation_index < len(cancellation_plan):
                if attempts:
                    attempt = attempts[-1]
                    if attempt["status"] == "PUBLISHED":
                        raise ValueError(
                            "continuation completed before controlled cancellation"
                        )
                    if attempt["status"] == "RUNNING" and model_started(attempt):
                        signal_name = cancellation_plan[cancellation_index]
                        cancel(attempt["job_id"], signal_name)
                        terminal = scheduler_terminal(
                            attempt["job_id"], timeout_seconds=180
                        )
                        interruption = (
                            Path(attempt["run_dir"]) / "attempt_interrupted.json"
                        )
                        if signal_name == "TERM":
                            wait_for_interruption(interruption)
                            interruption_sha256 = controller.sha256(interruption)
                        else:
                            time.sleep(3)
                            if interruption.exists():
                                raise ValueError(
                                    "hard-killed HICAR attempt ran signal cleanup"
                                )
                            interruption_sha256 = None
                        cancellations.append(
                            {
                                "signal": signal_name,
                                "job_id": attempt["job_id"],
                                "attempt_id": attempt["attempt_id"],
                                "terminal": terminal,
                                "model_log_nonempty": True,
                                "interruption_report": (
                                    str(interruption)
                                    if interruption_sha256 is not None
                                    else None
                                ),
                                "interruption_report_sha256": (
                                    interruption_sha256
                                ),
                            }
                        )
                        continue

            if state["status"] == "COMPLETE":
                if len(cancellations) != 2 or len(attempts) < 3:
                    raise ValueError(
                        "campaign completed without both controlled cancellations"
                    )
                successful = attempts[-1]
                if successful["status"] != "PUBLISHED":
                    raise ValueError("final immutable retry is not published")
                first_completion = Path(first_runtime["model_completion"])
                second_completion = Path(second_runtime["model_completion"])
                restart_input = matching_restart_evidence(
                    first_completion, second_completion
                )
                completion = Path(state["completion_report"])
                published_json(completion, "campaign completion")
                result = {
                    "schema_version": 1,
                    "status": "PASS",
                    "assessment": "ENGINEERING_ONLY",
                    "promotion_eligible": False,
                    "scientific_authorization": False,
                    "completed_at": datetime.now(UTC).isoformat(),
                    "campaign": str(campaign_path),
                    "campaign_sha256": controller.sha256(campaign_path),
                    "runtime_release": campaign["runtime_release"],
                    "python_environment": campaign["python_environment"],
                    "hicar_commit": campaign["model"]["expected_hicar_commit"],
                    "topology": {
                        "partition": "preemptible",
                        "nodes": campaign["model"]["nodes"],
                        "segments": 2,
                    },
                    "cancellations": cancellations,
                    "successful_retry": {
                        "job_id": successful["job_id"],
                        "attempt_id": successful["attempt_id"],
                        "completion": str(second_completion),
                        "completion_sha256": controller.sha256(second_completion),
                        "restart_input": restart_input,
                    },
                    "predecessor": {
                        "completion": str(first_completion),
                        "completion_sha256": controller.sha256(first_completion),
                    },
                    "campaign_completion": str(completion),
                    "campaign_completion_sha256": controller.sha256(completion),
                }
                controller.write_json_atomic(report, result)
                Path(f"{report}.ready").touch()
                return result
            time.sleep(poll_seconds)
        raise TimeoutError("real HICAR recovery drill exceeded its timeout")
    except BaseException:
        if state is not None:
            try:
                controller.set_capacity(campaign_path, models=0, cpus=0)
            except Exception:
                pass
            for job_id in active_job_ids(state):
                subprocess.run(
                    ["scancel", job_id],
                    check=False,
                    timeout=30,
                )
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", required=True, type=Path)
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--poll-seconds", type=int, default=10)
    parser.add_argument("--timeout-seconds", type=int, default=14400)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        print(
            json.dumps(
                {
                    "status": "DRY_RUN",
                    "assessment": "ENGINEERING_ONLY",
                    "campaign": str(args.campaign.resolve()),
                    "actions": [
                        "complete predecessor HICAR segment",
                        "SIGTERM first continuation attempt after model output begins",
                        "SIGKILL second continuation attempt after model output begins",
                        "complete third immutable retry from predecessor restart",
                        "verify restart-input and campaign-completion hashes",
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    result = run_drill(
        campaign_path=args.campaign.resolve(),
        repo_root=args.repo_root.resolve(),
        report=args.report.resolve(),
        poll_seconds=max(1, args.poll_seconds),
        timeout_seconds=max(600, args.timeout_seconds),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
