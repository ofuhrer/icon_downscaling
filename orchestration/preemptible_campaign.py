#!/usr/bin/env python3
"""Reconcile short, immutable HICAR attempts on Balfrin preemptible."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

try:
    from runtime_contract import (
        explicit_sbatch_partition,
        validate_python_environment,
        validate_runtime_release,
        validate_s83_partition_live,
    )
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from runtime_contract import (
        explicit_sbatch_partition,
        validate_python_environment,
        validate_runtime_release,
        validate_s83_partition_live,
    )

ACTIVE_STATES = {
    "CONFIGURING",
    "PENDING",
    "REQUEUED",
    "RUNNING",
    "SUSPENDED",
    "COMPLETING",
}
RETRYABLE_STATES = {
    "BOOT_FAIL",
    "CANCELLED",
    "NODE_FAIL",
    "PREEMPTED",
}
SUCCESS_STATE = "COMPLETED"
PREEMPTED_EXIT_CODE = 75
SUBMISSION_RECOVERY_SECONDS = 300


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


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


def json_payload_sha256(payload: dict[str, Any]) -> str:
    content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    return hashlib.sha256(content.encode()).hexdigest()


def published_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file() or not Path(f"{path}.ready").is_file():
        raise ValueError(f"{label} is not published: {path}")
    return load_json(path)


def normalized_state(value: str) -> str:
    return value.strip().split()[0].split("+")[0].upper()


def parse_exit_code(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value.split(":", 1)[0])
    except ValueError:
        return None


def parse_exit_signal(value: str | None) -> int | None:
    if not value or ":" not in value:
        return None
    try:
        return int(value.split(":", 1)[1])
    except ValueError:
        return None


def event(state: dict[str, Any], kind: str, **details: Any) -> None:
    state.setdefault("events", []).append({"time": utc_now(), "kind": kind, **details})
    state["events"] = state["events"][-500:]


@contextmanager
def lease(path: Path, seconds: int) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    payload = {
        "token": token,
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "acquired_at": utc_now(),
        "expires_epoch": time.time() + seconds,
    }
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(descriptor)
        try:
            existing = load_json(path)
        except (OSError, ValueError, json.JSONDecodeError):
            existing = {}
        raise RuntimeError(
            "campaign controller lease is active: "
            f"{existing.get('hostname', 'unknown')} "
            f"pid={existing.get('pid', 'unknown')}"
        ) from exc
    stream = os.fdopen(descriptor, "r+", encoding="utf-8")
    stream.seek(0)
    stream.truncate()
    json.dump(payload, stream, indent=2, sort_keys=True)
    stream.write("\n")
    stream.flush()
    os.fsync(stream.fileno())
    try:
        yield
    finally:
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


class Slurm:
    def query(self, job_ids: list[str]) -> dict[str, dict[str, str]]:
        numeric = sorted({job_id for job_id in job_ids if job_id.isdigit()})
        if not numeric:
            return {}
        grouped: dict[str, list[dict[str, str]]] = {job_id: [] for job_id in numeric}
        # Query each submitted identifier separately. On Balfrin, sacct returns
        # distinct numeric JobIDRaw values for array elements instead of the
        # conventional ``<array-job>_<task>`` spelling. A combined query cannot
        # therefore associate those rows with their array parent, whereas every
        # row returned by a single ``-j <parent>`` query belongs to that parent.
        for parent in numeric:
            result = subprocess.run(
                [
                    "sacct",
                    "-n",
                    "-X",
                    "-P",
                    "-j",
                    parent,
                    "--format=JobIDRaw,State,ExitCode",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
            for line in result.stdout.splitlines():
                fields = line.split("|")
                if len(fields) < 3:
                    continue
                _job_id, scheduler_state, exit_code = fields[:3]
                grouped[parent].append(
                    {
                        "state": normalized_state(scheduler_state),
                        "exit_code": exit_code,
                    }
                )
        states: dict[str, dict[str, str]] = {}
        terminal_priority = [
            "OUT_OF_MEMORY",
            "TIMEOUT",
            "FAILED",
            "CANCELLED",
            "PREEMPTED",
            "NODE_FAIL",
            "BOOT_FAIL",
        ]
        for parent, records in grouped.items():
            if not records:
                continue
            active = [record for record in records if record["state"] in ACTIVE_STATES]
            if active:
                running = next(
                    (record for record in active if record["state"] == "RUNNING"),
                    active[0],
                )
                states[parent] = running
                continue
            selected = None
            for scheduler_state in terminal_priority:
                selected = next(
                    (record for record in records if record["state"] == scheduler_state),
                    None,
                )
                if selected is not None:
                    break
            states[parent] = selected or records[0]
        return states

    def find_job(self, job_name: str) -> list[str]:
        found: set[str] = set()
        for command in (
            ["squeue", "-h", "-n", job_name, "-o", "%A"],
            [
                "sacct",
                "-n",
                "-X",
                "-S",
                "now-7days",
                "--name",
                job_name,
                "--format=JobIDRaw",
            ],
        ):
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode:
                continue
            for line in result.stdout.splitlines():
                value = line.strip().split(".")[0]
                if value.isdigit():
                    found.add(value)
        return sorted(found)

    def submit(self, arguments: list[str]) -> str:
        if not arguments or arguments[0] != "sbatch":
            raise ValueError("Slurm submission must use sbatch")
        partition = explicit_sbatch_partition(arguments)
        validate_s83_partition_live(partition)
        result = subprocess.run(
            arguments,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        job_id = result.stdout.strip().split(";", 1)[0]
        if not job_id.isdigit():
            raise RuntimeError(f"sbatch returned an invalid job id: {result.stdout!r}")
        return job_id


def campaign_paths(campaign: dict[str, Any]) -> tuple[Path, Path]:
    controller = campaign["controller"]
    return Path(controller["state"]), Path(controller["lease"])


def forcing_cache_index(campaign: dict[str, Any]) -> dict[str, Any]:
    policy = campaign.get("policy", {})
    cache = campaign.get("forcing_cache", {})
    if policy.get("shared_forcing_cache") is not True or cache.get("shared") is not True:
        raise ValueError("campaign requires a shared forcing cache")
    node_budget = int(policy["model_node_budget"])
    nodes_per_attempt = int(campaign["model"]["nodes"])
    maximum_slots = node_budget // nodes_per_attempt
    if not 1 <= node_budget <= 46 or maximum_slots < 1:
        raise ValueError("preemptible campaign has an invalid model-node budget")
    if not 1 <= int(policy["model_slots"]) <= maximum_slots:
        raise ValueError(
            "preemptible campaign model slots exceed its model-node budget: "
            f"maximum is {maximum_slots}"
        )
    root = Path(cache["root"]).resolve()
    campaign_root = Path(campaign["campaign_root"]).resolve()
    if root == campaign_root or campaign_root not in root.parents:
        raise ValueError("forcing cache root is outside the campaign root")
    index_path = Path(cache["index"]).resolve()
    index = published_json(index_path, "forcing cache index")
    if sha256(index_path) != cache["index_sha256"]:
        raise ValueError("forcing cache index changed")
    if (
        index.get("schema_version") != 1
        or index.get("shared") is not True
        or index.get("campaign_id") != campaign["campaign_id"]
        or Path(index.get("records_root", "")).resolve() != Path(cache["records_root"]).resolve()
        or Path(index.get("producer_root", "")).resolve() != Path(cache["producer_root"]).resolve()
        or index.get("record_count") != len(index.get("records", []))
    ):
        raise ValueError("forcing cache index does not match the campaign")
    seen_times: dict[str, str] = {}
    seen_paths: set[str] = set()
    records_root = Path(cache["records_root"]).resolve()
    for record in index["records"]:
        path = Path(record["forcing_file"]).resolve()
        if records_root not in path.parents:
            raise ValueError(f"forcing cache record is outside records root: {path}")
        value = str(path)
        valid_time = str(record["valid_time"])
        if value in seen_paths or (valid_time in seen_times and seen_times[valid_time] != value):
            raise ValueError("forcing cache index has a duplicate identity")
        seen_paths.add(value)
        seen_times[valid_time] = value
        if not record.get("consumers"):
            raise ValueError(f"forcing cache record has no consumers: {path}")
    return index


def validate_campaign_runtime(
    campaign: dict[str, Any],
    repo_root: Path,
) -> None:
    evidence = campaign.get("runtime_release")
    if not evidence:
        raise ValueError("campaign lacks a frozen runtime release")
    path = Path(evidence["path"])
    if sha256(path) != evidence["sha256"]:
        raise ValueError("runtime release manifest changed")
    payload = validate_runtime_release(
        path,
        expected_root=repo_root,
        production=campaign.get("purpose", "qualification") == "production",
    )
    if (
        payload["release_root"] != evidence["release_root"]
        or payload["source_commit"] != evidence["source_commit"]
        or payload["source_dirty"] != evidence["source_dirty"]
    ):
        raise ValueError("runtime release evidence changed")
    python_evidence = campaign.get("python_environment")
    if not python_evidence:
        raise ValueError("campaign lacks a frozen Python environment")
    python_report = Path(python_evidence["path"])
    if sha256(python_report) != python_evidence["sha256"]:
        raise ValueError("Python environment report changed")
    python_payload = validate_python_environment(
        python_report,
        path,
        smoke=False,
    )
    if (
        python_payload["python"] != python_evidence["python"]
        or python_payload["python_version"] != python_evidence["python_version"]
        or python_payload["requirements_sha256"] != python_evidence["requirements_sha256"]
    ):
        raise ValueError("Python environment evidence changed")


def new_state(campaign_path: Path, campaign: dict[str, Any]) -> dict[str, Any]:
    chains: dict[str, Any] = {}
    for chain in campaign["chains"]:
        chains[chain["chain_id"]] = {
            "segments": [
                {
                    "segment_id": segment["segment_id"],
                    "status": "WAITING_FORCING",
                    "attempts": [],
                    "model_completion": None,
                    "solver_report": None,
                    "compression_complete": False,
                    "segment_retirement": None,
                    "restart_retirement": None,
                    "lifecycle_complete": False,
                    "cpu_failures": {},
                    "terminal_error": None,
                }
                for segment in chain["segments"]
            ]
        }
    return {
        "schema_version": 1,
        "campaign_id": campaign["campaign_id"],
        "goal": campaign.get("goal", {}).get("outcome"),
        "campaign": str(campaign_path.resolve()),
        "campaign_sha256": sha256(campaign_path),
        "status": "ACTIVE",
        "capacity": {
            "model_slots": campaign["policy"]["model_slots"],
            "cpu_slots": campaign["policy"]["cpu_slots"],
        },
        "chains": chains,
        "cpu_batch": None,
        "events": [{"time": utc_now(), "kind": "STATE_INITIALIZED"}],
        "updated_at": utc_now(),
    }


def load_or_create_state(
    campaign_path: Path,
    campaign: dict[str, Any],
    state_path: Path,
) -> dict[str, Any]:
    if state_path.is_file():
        state = load_json(state_path)
        if state.get("campaign_sha256") != sha256(campaign_path):
            raise ValueError("controller state belongs to a different campaign plan")
        for chain in state.get("chains", {}).values():
            for segment in chain.get("segments", []):
                segment.setdefault("segment_retirement", None)
                segment.setdefault("restart_retirement", None)
                segment.setdefault("lifecycle_complete", False)
        return state
    state = new_state(campaign_path, campaign)
    write_json_atomic(state_path, state)
    return state


def spec_segment(
    campaign: dict[str, Any],
    chain_id: str,
    index: int,
) -> dict[str, Any]:
    for chain in campaign["chains"]:
        if chain["chain_id"] == chain_id:
            return chain["segments"][index]
    raise KeyError(chain_id)


def runtime_segment(
    state: dict[str, Any],
    chain_id: str,
    index: int,
) -> dict[str, Any]:
    return state["chains"][chain_id]["segments"][index]


def forcing_publication_passes(segment: dict[str, Any]) -> bool:
    path = Path(segment["forcing_publication"])
    if not path.is_file() or not Path(f"{path}.ready").is_file():
        return False
    try:
        return load_json(path).get("status") == "PASS"
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def forcing_record_ready(record: dict[str, Any]) -> bool:
    """Check the producer publication contract without re-hashing its payload.

    The forcing ``.ready`` marker is created only after the data, validation,
    and manifest have been atomically published. The segment finalizer performs
    the expensive independent payload checksum before HICAR can be submitted.
    """
    forcing = Path(record["forcing_file"])
    base = Path(str(forcing)[:-3]) if str(forcing).endswith(".nc") else forcing
    validation = Path(f"{base}.validation.json")
    manifest_path = Path(f"{base}.manifest.json")
    try:
        manifest = load_json(manifest_path)
        digest = manifest.get("forcing_sha256")
        return (
            forcing.is_file()
            and Path(f"{forcing}.ready").is_file()
            and validation.is_file()
            and load_json(validation).get("status") == "PASS"
            and manifest.get("status") == "PASS"
            and manifest.get("valid_time") == record["valid_time"]
            and isinstance(digest, str)
            and len(digest) == 64
        )
    except (OSError, KeyError, ValueError, json.JSONDecodeError):
        return False


def missing_forcing_records(segment: dict[str, Any]) -> list[int]:
    plan = load_json(Path(segment["plan"]))
    return [
        index for index, record in enumerate(plan["records"]) if not forcing_record_ready(record)
    ]


def completion_report(runtime: dict[str, Any]) -> dict[str, Any] | None:
    value = runtime.get("model_completion")
    if not value:
        return None
    path = Path(value)
    if not path.is_file() or not Path(f"{path}.ready").is_file():
        return None
    try:
        report = load_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return report if report.get("status") == "PASS" else None


def solver_report_path(attempt: dict[str, Any]) -> Path:
    return Path(attempt["run_dir"]) / "scientific_validation" / "solver_log_diagnostics.json"


def solver_report_passes(attempt: dict[str, Any]) -> bool:
    path = solver_report_path(attempt)
    if not path.is_file() or not Path(f"{path}.ready").is_file():
        return False
    try:
        return load_json(path).get("status") == "PASS"
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def compression_tasks(
    chain_id: str,
    segment: dict[str, Any],
    runtime: dict[str, Any],
) -> list[dict[str, Any]]:
    report = completion_report(runtime)
    if report is None:
        return []
    attempt = runtime["attempts"][-1]
    target_dir = (Path(segment["compressed_root"]) / attempt["attempt_id"]).resolve()
    tasks = []
    for artifact in report["output"]["files"]:
        source = Path(artifact["path"])
        target = target_dir / source.name
        tasks.append(
            {
                "kind": "compression",
                "chain_id": chain_id,
                "segment_id": segment["segment_id"],
                "source": str(source),
                "target": str(target),
                "target_dir": str(target_dir),
            }
        )
    return tasks


def segment_retirement_task(
    campaign: dict[str, Any],
    chain_id: str,
    segment: dict[str, Any],
    runtime: dict[str, Any],
    index: int,
) -> dict[str, Any] | None:
    if (
        completion_report(runtime) is None
        or not runtime.get("solver_report")
        or not runtime.get("compression_complete")
    ):
        return None
    compressions = []
    for item in compression_tasks(chain_id, segment, runtime):
        compressions.append(
            {
                "source": item["source"],
                "target": item["target"],
                "report": f"{item['target']}.compression.json",
            }
        )
    successful_attempt = runtime["attempts"][-1]
    obsolete = [
        attempt["attempt_dir"]
        for attempt in runtime["attempts"][:-1]
        if attempt.get("status") not in {"SUBMITTING", "SUBMITTED", "RUNNING", "PUBLISHED"}
    ]
    return {
        "kind": "segment_retirement",
        "task_id": f"{chain_id}:{segment['segment_id']}:segment-retirement",
        "chain_id": chain_id,
        "segment_id": segment["segment_id"],
        "segment_index": index,
        "campaign_root": campaign["campaign_root"],
        "plan": segment["plan"],
        "forcing_publication": segment["forcing_publication"],
        "shared_forcing_cache": bool(campaign.get("forcing_cache", {}).get("shared")),
        "model_completion": runtime["model_completion"],
        "successful_attempt_id": successful_attempt["attempt_id"],
        "compressions": compressions,
        "obsolete_attempt_dirs": obsolete,
        "report": str(
            Path(
                segment.get(
                    "lifecycle_root",
                    Path(segment["attempt_root"]).parent / "lifecycle",
                )
            )
            / "segment_retirement.json"
        ),
    }


def preserve_restart(
    campaign: dict[str, Any],
    segment: dict[str, Any],
    *,
    final: bool,
) -> bool:
    if final:
        return True
    interval = int(campaign["policy"].get("preserve_restart_every_segments", 30))
    return interval > 0 and int(segment["sequence"]) % interval == 0


def restart_retirement_task(
    campaign: dict[str, Any],
    state: dict[str, Any],
    chain: dict[str, Any],
    index: int,
) -> dict[str, Any] | None:
    segment = chain["segments"][index]
    runtime = runtime_segment(state, chain["chain_id"], index)
    if completion_report(runtime) is None or runtime["status"] != "COMPLETE":
        return None
    final = index == len(chain["segments"]) - 1
    successor_path = None
    if not final:
        successor = runtime_segment(state, chain["chain_id"], index + 1)
        if completion_report(successor) is None or successor["status"] != "COMPLETE":
            return None
        successor_path = successor["model_completion"]
    return {
        "kind": "restart_retirement",
        "task_id": (f"{chain['chain_id']}:{segment['segment_id']}:restart-retirement"),
        "chain_id": chain["chain_id"],
        "segment_id": segment["segment_id"],
        "segment_index": index,
        "campaign_root": campaign["campaign_root"],
        "previous_completion": runtime["model_completion"],
        "next_completion": successor_path,
        "preserve": preserve_restart(campaign, segment, final=final),
        "report": str(
            Path(
                segment.get(
                    "lifecycle_root",
                    Path(segment["attempt_root"]).parent / "lifecycle",
                )
            )
            / "restart_retirement.json"
        ),
    }


def forcing_cache_record_report(
    campaign: dict[str, Any],
    record: dict[str, Any],
) -> Path:
    token = hashlib.sha256(str(Path(record["forcing_file"]).resolve()).encode()).hexdigest()[:20]
    return (
        Path(campaign["forcing_cache"]["root"]) / "retirement" / "records" / f"{token}.json"
    ).resolve()


def forcing_cache_record_task(
    campaign: dict[str, Any],
    record: dict[str, Any],
) -> dict[str, Any]:
    consumers = []
    for consumer in record["consumers"]:
        segment = spec_segment(
            campaign,
            consumer["chain_id"],
            int(consumer["segment_index"]),
        )
        lifecycle_root = Path(
            segment.get(
                "lifecycle_root",
                Path(segment["attempt_root"]).parent / "lifecycle",
            )
        )
        consumers.append(
            {
                **consumer,
                "segment_retirement": str((lifecycle_root / "segment_retirement.json").resolve()),
            }
        )
    owner = consumers[0]
    return {
        "kind": "forcing_cache_retirement",
        "task_id": (f"forcing-cache:{hashlib.sha256(record['forcing_file'].encode()).hexdigest()}"),
        "chain_id": owner["chain_id"],
        "segment_id": owner["segment_id"],
        "segment_index": owner["segment_index"],
        "campaign_root": campaign["campaign_root"],
        "forcing_file": record["forcing_file"],
        "valid_time": record["valid_time"],
        "cycle_date": record["cycle_date"],
        "consumers": consumers,
        "report": str(forcing_cache_record_report(campaign, record)),
    }


def forcing_cache_record_safe(
    campaign: dict[str, Any],
    state: dict[str, Any],
    record: dict[str, Any],
) -> bool:
    for consumer in record["consumers"]:
        runtime = runtime_segment(
            state,
            consumer["chain_id"],
            int(consumer["segment_index"]),
        )
        if not runtime.get("segment_retirement"):
            return False
    return True


def forcing_cycle_cache_task(
    campaign: dict[str, Any],
    cycle_date: str,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    owner = records[0]["consumers"][0]
    reports = [str(forcing_cache_record_report(campaign, record)) for record in records]
    return {
        "kind": "forcing_cycle_cache_retirement",
        "task_id": f"forcing-cycle-cache:{cycle_date}",
        "chain_id": owner["chain_id"],
        "segment_id": owner["segment_id"],
        "segment_index": owner["segment_index"],
        "campaign_root": campaign["campaign_root"],
        "cycle_date": cycle_date,
        "record_retirements": reports,
        "producer_cache_dir": str(
            (Path(campaign["forcing_cache"]["producer_root"]) / "cache" / cycle_date).resolve()
        ),
        "report": str(
            (
                Path(campaign["forcing_cache"]["root"])
                / "retirement"
                / "cycles"
                / f"{cycle_date}.json"
            ).resolve()
        ),
    }


def pending_forcing_cache_retirements(
    campaign: dict[str, Any],
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    index = forcing_cache_index(campaign)
    records = index["records"]
    pending_records = []
    for record in records:
        task = forcing_cache_record_task(campaign, record)
        if forcing_cache_record_safe(campaign, state, record) and not retirement_task_ready(task):
            pending_records.append(task)
    if pending_records:
        return pending_records

    by_cycle: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_cycle.setdefault(record["cycle_date"], []).append(record)
    pending_cycles = []
    for cycle_date, cycle_records in sorted(by_cycle.items()):
        if not all(
            retirement_task_ready(forcing_cache_record_task(campaign, record))
            for record in cycle_records
        ):
            continue
        task = forcing_cycle_cache_task(campaign, cycle_date, cycle_records)
        if not retirement_task_ready(task):
            pending_cycles.append(task)
    return pending_cycles


def forcing_cache_retirement_complete(campaign: dict[str, Any]) -> bool:
    index = forcing_cache_index(campaign)
    if not all(
        retirement_task_ready(forcing_cache_record_task(campaign, record))
        for record in index["records"]
    ):
        return False
    by_cycle: dict[str, list[dict[str, Any]]] = {}
    for record in index["records"]:
        by_cycle.setdefault(record["cycle_date"], []).append(record)
    return all(
        retirement_task_ready(forcing_cycle_cache_task(campaign, cycle, records))
        for cycle, records in by_cycle.items()
    )


def retirement_task_ready(task: dict[str, Any]) -> bool:
    try:
        report = Path(task["report"])
        if not report.is_file() or not Path(f"{report}.ready").is_file():
            return False
        payload = load_json(report)
        return (
            payload.get("status") == "PASS"
            and payload.get("task_id") == task["task_id"]
            and payload.get("task_sha256") == json_payload_sha256(task)
            and payload.get("action") in {"PRESERVED", "RETIRED"}
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False


def task_ready(task: dict[str, Any], campaign: dict[str, Any]) -> bool:
    try:
        kind = task["kind"]
        if kind == "forcing_record":
            segment = spec_segment(
                campaign,
                task["chain_id"],
                int(task["segment_index"]),
            )
            plan = load_json(Path(segment["plan"]))
            return forcing_record_ready(plan["records"][int(task["record_index"])])
        if kind == "forcing_finalize":
            segment = spec_segment(
                campaign,
                task["chain_id"],
                int(task["segment_index"]),
            )
            return forcing_publication_passes(segment)
        if kind == "solver_audit":
            path = Path(task["run_dir"]) / "scientific_validation/solver_log_diagnostics.json"
            return (
                path.is_file()
                and Path(f"{path}.ready").is_file()
                and load_json(path).get("status") == "PASS"
            )
        if kind == "compression":
            target = Path(task["target"])
            report = Path(f"{target}.compression.json")
            return (
                target.is_file()
                and Path(f"{target}.ready").is_file()
                and report.is_file()
                and load_json(report).get("status") == "PASS"
            )
        if kind in {
            "segment_retirement",
            "restart_retirement",
            "forcing_cache_retirement",
            "forcing_cycle_cache_retirement",
        }:
            return retirement_task_ready(task)
        raise ValueError(f"unsupported task kind: {kind}")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False


def active_attempt(runtime: dict[str, Any]) -> dict[str, Any] | None:
    for attempt in reversed(runtime["attempts"]):
        if attempt["status"] in {"SUBMITTING", "SUBMITTED", "RUNNING"}:
            return attempt
    return None


def job_ids(state: dict[str, Any]) -> list[str]:
    values = []
    for chain in state["chains"].values():
        for segment in chain["segments"]:
            for attempt in segment["attempts"]:
                if attempt.get("job_id") and attempt["status"] in {
                    "SUBMITTING",
                    "SUBMITTED",
                    "RUNNING",
                }:
                    values.append(str(attempt["job_id"]))
    batch = state.get("cpu_batch")
    if batch and batch.get("job_id"):
        values.append(str(batch["job_id"]))
    return values


def read_receipt(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        job_id = str(load_json(path)["job_id"])
    except (OSError, KeyError, ValueError, json.JSONDecodeError):
        return None
    return job_id if job_id.isdigit() else None


def write_receipt(path: Path, job_id: str, command: list[str]) -> None:
    write_json_atomic(
        path,
        {
            "schema_version": 1,
            "status": "SUBMITTED",
            "job_id": job_id,
            "submitted_at": utc_now(),
            "command": command,
        },
    )


def recover_submitting_job(
    item: dict[str, Any],
    scheduler: Slurm,
    allow_submit: bool,
) -> bool:
    if item.get("job_id"):
        return True
    receipt = Path(item["receipt"])
    job_id = read_receipt(receipt)
    if job_id is None:
        found = scheduler.find_job(item["job_name"])
        if len(found) > 1:
            raise RuntimeError(f"multiple Slurm jobs share submission identity {item['job_name']}")
        if found:
            job_id = found[0]
            write_receipt(receipt, job_id, item["command"])
    if job_id is None and allow_submit:
        submitted_at = datetime.fromisoformat(item["submitted_at"])
        age = (datetime.now(UTC) - submitted_at).total_seconds()
        if age >= SUBMISSION_RECOVERY_SECONDS:
            job_id = scheduler.submit(item["command"])
            write_receipt(receipt, job_id, item["command"])
    if job_id is None:
        return False
    item["job_id"] = job_id
    item["status"] = "SUBMITTED"
    return True


def classify_attempt_terminal(
    attempt: dict[str, Any],
    scheduler_record: dict[str, str],
) -> str:
    scheduler_state = normalized_state(scheduler_record["state"])
    exit_code = parse_exit_code(scheduler_record.get("exit_code"))
    exit_signal = parse_exit_signal(scheduler_record.get("exit_code"))
    interruption = Path(attempt["run_dir"]) / "attempt_interrupted.json"
    if scheduler_state in RETRYABLE_STATES:
        return "RETRYABLE"
    if scheduler_state == "FAILED" and (
        exit_code == PREEMPTED_EXIT_CODE or exit_signal in {9, 15} or interruption.is_file()
    ):
        return "RETRYABLE"
    if scheduler_state == SUCCESS_STATE:
        return "COMPLETED_WITHOUT_PUBLICATION"
    return "TERMINAL_FAILURE"


def refresh_model_attempts(
    campaign: dict[str, Any],
    state: dict[str, Any],
    scheduler_states: dict[str, dict[str, str]],
    scheduler: Slurm,
    execute: bool,
) -> None:
    maximum = int(campaign["policy"]["max_model_attempts"])
    for chain in campaign["chains"]:
        chain_id = chain["chain_id"]
        for index, segment in enumerate(chain["segments"]):
            runtime = runtime_segment(state, chain_id, index)
            if runtime["status"] == "COMPLETE" and runtime.get("model_completion"):
                continue
            if not runtime["attempts"]:
                continue
            attempt = runtime["attempts"][-1]
            completion = Path(attempt["run_dir"]) / "model_chunk_completion.json"
            try:
                completion_passes = (
                    completion.is_file()
                    and Path(f"{completion}.ready").is_file()
                    and load_json(completion).get("status") == "PASS"
                )
            except (OSError, ValueError, json.JSONDecodeError):
                completion_passes = False
            if completion_passes:
                attempt["status"] = "PUBLISHED"
                runtime["model_completion"] = str(completion)
                runtime["status"] = "MODEL_PUBLISHED"
                continue
            if attempt["status"] == "SUBMITTING":
                recover_submitting_job(attempt, scheduler, execute)
            job_id = attempt.get("job_id")
            record = scheduler_states.get(str(job_id)) if job_id else None
            if record is None:
                continue
            scheduler_state = normalized_state(record["state"])
            attempt["scheduler_state"] = scheduler_state
            attempt["scheduler_exit_code"] = record.get("exit_code")
            if scheduler_state in ACTIVE_STATES:
                attempt["status"] = "RUNNING" if scheduler_state == "RUNNING" else "SUBMITTED"
                runtime["status"] = attempt["status"]
                continue
            classification = classify_attempt_terminal(attempt, record)
            previous_status = attempt["status"]
            attempt["status"] = classification
            if classification == "RETRYABLE" and (
                maximum == 0 or len(runtime["attempts"]) < maximum
            ):
                runtime["status"] = "READY_TO_RETRY"
                if previous_status != "RETRYABLE":
                    event(
                        state,
                        "MODEL_ATTEMPT_RETRYABLE",
                        chain_id=chain_id,
                        segment_id=segment["segment_id"],
                        attempt_id=attempt["attempt_id"],
                        scheduler_state=scheduler_state,
                    )
            elif classification == "RETRYABLE":
                runtime["terminal_error"] = f"model retry budget exhausted after {maximum} attempts"
                runtime["status"] = "BLOCKED"
            else:
                runtime["terminal_error"] = (
                    f"model attempt ended as {scheduler_state} without a validated completion"
                )
                runtime["status"] = "BLOCKED"


def refresh_solver_and_compression(
    campaign: dict[str, Any],
    state: dict[str, Any],
) -> None:
    for chain in campaign["chains"]:
        chain_id = chain["chain_id"]
        for index, segment in enumerate(chain["segments"]):
            runtime = runtime_segment(state, chain_id, index)
            if runtime["status"] == "COMPLETE" and runtime["compression_complete"]:
                continue
            if completion_report(runtime) is None:
                continue
            attempt = runtime["attempts"][-1]
            if solver_report_passes(attempt):
                runtime["solver_report"] = str(solver_report_path(attempt))
                runtime["status"] = "COMPLETE"
            tasks = compression_tasks(chain_id, segment, runtime)
            runtime["compression_complete"] = bool(tasks) and all(
                task_ready(task, campaign) for task in tasks
            )


def refresh_lifecycle(
    campaign: dict[str, Any],
    state: dict[str, Any],
) -> None:
    for chain in campaign["chains"]:
        chain_id = chain["chain_id"]
        for index, segment in enumerate(chain["segments"]):
            runtime = runtime_segment(state, chain_id, index)
            segment_task = segment_retirement_task(campaign, chain_id, segment, runtime, index)
            runtime["segment_retirement"] = (
                segment_task["report"]
                if segment_task and retirement_task_ready(segment_task)
                else None
            )
            restart_task = restart_retirement_task(campaign, state, chain, index)
            runtime["restart_retirement"] = (
                restart_task["report"]
                if restart_task and retirement_task_ready(restart_task)
                else None
            )
            runtime["lifecycle_complete"] = bool(
                runtime.get("segment_retirement") and runtime.get("restart_retirement")
            )


def clear_resolved_lifecycle_terminal_errors(state: dict[str, Any]) -> None:
    """Unblock lifecycle work that published after its retry budget was exhausted.

    A task can become valid after Slurm has reported its last failed attempt, for
    example when an operator repairs an immutable input and reruns the exact
    checksum-bound task.  Keep the failure counters as history, but do not leave
    the campaign permanently blocked once the expected lifecycle publication is
    present and has already passed ``retirement_task_ready`` in
    :func:`refresh_lifecycle`.
    """

    evidence_keys = {
        "segment_retirement": "segment_retirement",
        "restart_retirement": "restart_retirement",
    }
    for chain_id, chain in state["chains"].items():
        for runtime in chain["segments"]:
            error = runtime.get("terminal_error")
            if not isinstance(error, str):
                continue
            for kind, evidence_key in evidence_keys.items():
                if not error.startswith(f"CPU task {kind} failed in scheduler state "):
                    continue
                if not runtime.get(evidence_key):
                    break
                runtime["terminal_error"] = None
                event(
                    state,
                    "CPU_TASK_RECOVERED",
                    chain_id=chain_id,
                    segment_id=runtime["segment_id"],
                    task_kind=kind,
                    failure_count=int(runtime["cpu_failures"].get(kind, 0)),
                )
                break


def refresh_cpu_batch(
    campaign: dict[str, Any],
    state: dict[str, Any],
    scheduler_states: dict[str, dict[str, str]],
    scheduler: Slurm,
    execute: bool,
) -> None:
    batch = state.get("cpu_batch")
    if not batch:
        return
    task_file = Path(batch["task_file"])
    try:
        task_file_valid = (
            task_file.is_file()
            and Path(f"{task_file}.ready").is_file()
            and sha256(task_file) == batch["task_sha256"]
        )
    except OSError:
        task_file_valid = False
    if not task_file_valid:
        for task in batch["tasks"]:
            runtime = runtime_segment(
                state,
                task["chain_id"],
                int(task["segment_index"]),
            )
            runtime["terminal_error"] = f"campaign CPU task publication changed: {task_file}"
            runtime["status"] = "BLOCKED"
        event(state, "CPU_TASK_PUBLICATION_CHANGED", batch_id=batch["batch_id"])
        state["cpu_batch"] = None
        return
    if batch["status"] == "SUBMITTING":
        recover_submitting_job(batch, scheduler, execute)
    job_id = batch.get("job_id")
    record = scheduler_states.get(str(job_id)) if job_id else None
    scheduler_state = None
    if record is not None:
        scheduler_state = normalized_state(record["state"])
        batch["scheduler_state"] = scheduler_state
        batch["scheduler_exit_code"] = record.get("exit_code")
        if scheduler_state in ACTIVE_STATES:
            batch["status"] = "RUNNING" if scheduler_state == "RUNNING" else "SUBMITTED"
            return
    if all(task_ready(task, campaign) for task in batch["tasks"]):
        event(state, "CPU_BATCH_PUBLISHED", batch_id=batch["batch_id"])
        state["cpu_batch"] = None
        return
    if record is None:
        return

    retryable = scheduler_state in (RETRYABLE_STATES | {"FAILED", "OUT_OF_MEMORY", "TIMEOUT"})
    maximum = int(campaign["policy"]["max_cpu_attempts"])
    for task in batch["tasks"]:
        if task_ready(task, campaign):
            continue
        runtime = runtime_segment(
            state,
            task["chain_id"],
            int(task["segment_index"]),
        )
        key = task["kind"]
        failures = int(runtime["cpu_failures"].get(key, 0)) + 1
        runtime["cpu_failures"][key] = failures
        if not retryable or failures >= maximum:
            runtime["terminal_error"] = (
                f"CPU task {key} failed in scheduler state {scheduler_state}"
            )
            runtime["status"] = "BLOCKED"
    event(
        state,
        "CPU_BATCH_TERMINAL",
        batch_id=batch["batch_id"],
        scheduler_state=scheduler_state,
        retryable=retryable,
    )
    state["cpu_batch"] = None


def frontier_indices(
    campaign: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, int]:
    frontiers = {}
    for chain in campaign["chains"]:
        chain_id = chain["chain_id"]
        for index, _segment in enumerate(chain["segments"]):
            runtime = runtime_segment(state, chain_id, index)
            if runtime["status"] != "COMPLETE":
                frontiers[chain_id] = index
                break
    return frontiers


def eligible_prefetch_segments(
    campaign: dict[str, Any],
    state: dict[str, Any],
) -> list[tuple[str, int, dict[str, Any], dict[str, Any]]]:
    prefetch = int(campaign["policy"]["prefetch_segments_per_chain"])
    values = []
    for chain_id, frontier in frontier_indices(campaign, state).items():
        chain = next(item for item in campaign["chains"] if item["chain_id"] == chain_id)
        for index in range(frontier, min(len(chain["segments"]), frontier + prefetch + 1)):
            values.append(
                (
                    chain_id,
                    index,
                    chain["segments"][index],
                    runtime_segment(state, chain_id, index),
                )
            )
    return values


def pending_cpu_tasks(
    campaign: dict[str, Any],
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    batch_limit = int(campaign["policy"].get("max_cpu_batch_tasks", 32))
    post_tasks = []
    for chain in campaign["chains"]:
        chain_id = chain["chain_id"]
        for index, segment in enumerate(chain["segments"]):
            runtime = runtime_segment(state, chain_id, index)
            if runtime.get("solver_report") or completion_report(runtime) is None:
                continue
            attempt = runtime["attempts"][-1]
            post_tasks.append(
                {
                    "kind": "solver_audit",
                    "chain_id": chain_id,
                    "segment_id": segment["segment_id"],
                    "segment_index": index,
                    "plan": segment["plan"],
                    "run_dir": attempt["run_dir"],
                }
            )

    for chain in campaign["chains"]:
        chain_id = chain["chain_id"]
        for index, segment in enumerate(chain["segments"]):
            runtime = runtime_segment(state, chain_id, index)
            if runtime["compression_complete"] or completion_report(runtime) is None:
                continue
            for task in compression_tasks(chain_id, segment, runtime):
                task["segment_index"] = index
                if not task_ready(task, campaign):
                    post_tasks.append(task)

    for chain in campaign["chains"]:
        chain_id = chain["chain_id"]
        for index, segment in enumerate(chain["segments"]):
            runtime = runtime_segment(state, chain_id, index)
            if runtime.get("segment_retirement"):
                continue
            task = segment_retirement_task(campaign, chain_id, segment, runtime, index)
            if task and not task_ready(task, campaign):
                post_tasks.append(task)

    for chain in campaign["chains"]:
        for index, _segment in enumerate(chain["segments"]):
            runtime = runtime_segment(state, chain["chain_id"], index)
            if runtime.get("restart_retirement"):
                continue
            task = restart_retirement_task(campaign, state, chain, index)
            if task and not task_ready(task, campaign):
                post_tasks.append(task)
    post_tasks.extend(pending_forcing_cache_retirements(campaign, state))

    input_tasks = []
    for chain_id, index, segment, _runtime in eligible_prefetch_segments(campaign, state):
        if not forcing_publication_passes(segment) and not missing_forcing_records(segment):
            input_tasks.append(
                {
                    "kind": "forcing_finalize",
                    "chain_id": chain_id,
                    "segment_id": segment["segment_id"],
                    "segment_index": index,
                    "plan": segment["plan"],
                }
            )

    model = campaign["model"]
    scheduled_forcing: set[str] = set()
    for chain_id, index, segment, _runtime in eligible_prefetch_segments(campaign, state):
        if forcing_publication_passes(segment):
            continue
        plan = load_json(Path(segment["plan"]))
        for record_index in missing_forcing_records(segment):
            forcing_file = str(Path(plan["records"][record_index]["forcing_file"]).resolve())
            if forcing_file in scheduled_forcing:
                continue
            scheduled_forcing.add(forcing_file)
            input_tasks.append(
                {
                    "kind": "forcing_record",
                    "chain_id": chain_id,
                    "segment_id": segment["segment_id"],
                    "segment_index": index,
                    "plan": segment["plan"],
                    "record_index": record_index,
                    "case_root": model["case_root"],
                    "static_file": campaign["forcing_cache"]["static_file"],
                    "forcing_file": forcing_file,
                }
            )

    input_weight = int(campaign["policy"].get("input_task_weight", 3))
    post_weight = int(campaign["policy"].get("post_task_weight", 1))
    selected = []
    input_index = 0
    post_index = 0
    while len(selected) < batch_limit and (
        input_index < len(input_tasks) or post_index < len(post_tasks)
    ):
        # Put both lanes at the head of every cycle so a two-way Slurm array
        # starts input production and lifecycle work together.
        if input_index < len(input_tasks):
            selected.append(input_tasks[input_index])
            input_index += 1
        if len(selected) >= batch_limit:
            break
        if post_index < len(post_tasks):
            selected.append(post_tasks[post_index])
            post_index += 1
        for _ in range(input_weight - 1):
            if len(selected) >= batch_limit or input_index >= len(input_tasks):
                break
            selected.append(input_tasks[input_index])
            input_index += 1
        for _ in range(post_weight - 1):
            if len(selected) >= batch_limit or post_index >= len(post_tasks):
                break
            selected.append(post_tasks[post_index])
            post_index += 1
    return selected


def shell_export(exports: dict[str, str]) -> str:
    values = []
    for key, value in sorted(exports.items()):
        if any(character in value for character in ",\n\r"):
            raise ValueError(f"Slurm export value for {key} contains a separator")
        values.append(f"{key}={value}")
    return "ALL," + ",".join(values)


def submission_identity(prefix: str, token: str) -> str:
    digest = hashlib.sha256(token.encode()).hexdigest()[:16]
    return f"{prefix}-{digest}"


def submit_recorded(
    *,
    item: dict[str, Any],
    scheduler: Slurm,
    state_path: Path,
    state: dict[str, Any],
) -> str:
    write_json_atomic(state_path, state)
    job_id = scheduler.submit(item["command"])
    write_receipt(Path(item["receipt"]), job_id, item["command"])
    item["job_id"] = job_id
    item["status"] = "SUBMITTED"
    write_json_atomic(state_path, state)
    return job_id


def previous_completion(
    campaign: dict[str, Any],
    state: dict[str, Any],
    chain_id: str,
    index: int,
) -> tuple[Path, dict[str, Any]] | None:
    if index == 0:
        return None
    previous_runtime = runtime_segment(state, chain_id, index - 1)
    report = completion_report(previous_runtime)
    if report is None or previous_runtime["status"] != "COMPLETE":
        return None
    return Path(previous_runtime["model_completion"]), report


def model_submission_command(
    campaign: dict[str, Any],
    segment: dict[str, Any],
    attempt: dict[str, Any],
    previous: tuple[Path, dict[str, Any]] | None,
    repo_root: Path,
) -> list[str]:
    model = campaign["model"]
    script = Path(
        model.get(
            "script",
            repo_root / "case_studies/swiss_200m/scripts/run_rea_l_stream_chunk_balfrin.sbatch",
        )
    ).resolve()
    exports = {
        "REPO_ROOT": str(repo_root),
        "STREAM_PLAN": segment["plan"],
        "HICAR_MULTILEVEL_ROOT": model["hicar_root"],
        "HICAR_SWISS_CASE": model["case_root"],
        "HICAR_STATIC_FILE": segment.get("static_file", model["static_file"]),
        "HICAR_EXPECTED_COMMIT": model["expected_hicar_commit"],
        "STREAM_OUTPUT_INTERVAL": str(model["output_interval_seconds"]),
        "STREAM_OUTPUT_PROFILE": model["output_profile"],
        "STREAM_REA_L_LAND_INITIALIZATION": ("1" if segment["rea_l_land_initialization"] else "0"),
        "STREAM_RUN_DIR": attempt["run_dir"],
        "STREAM_RESTART_DIR": attempt["restart_dir"],
        "STREAM_PREEMPTIBLE_ATTEMPT": "1",
        "STREAM_ATTEMPT_ID": attempt["attempt_id"],
        "HICAR_PREEMPTION_HELPER": str((repo_root / "orchestration/preemption.py").resolve()),
        "HICAR_VALIDATION_PYTHON": campaign["python_environment"]["python"],
    }
    if model.get("build_root"):
        exports["HICAR_MULTILEVEL_BUILD"] = model["build_root"]
    if previous is not None:
        previous_path, report = previous
        exports.update(
            {
                "STREAM_RESTART_FROM": segment["start"],
                "STREAM_RESTART_INPUT_FILE": report["restart"]["path"],
                "STREAM_RESTART_INPUT_REPORT": str(previous_path),
            }
        )
    attempt_dir = Path(attempt["attempt_dir"])
    return [
        "sbatch",
        "--parsable",
        "--no-requeue",
        "--partition=preemptible",
        f"--nodes={int(model['nodes'])}",
        "--ntasks-per-node=5",
        "--cpus-per-task=1",
        "--gres=gpu:4",
        f"--time={model['time_limit']}",
        "--exclusive",
        "--signal=B:USR1@300",
        f"--job-name={attempt['job_name']}",
        f"--output={attempt_dir / 'slurm-%j.out'}",
        f"--error={attempt_dir / 'slurm-%j.err'}",
        f"--export={shell_export(exports)}",
        str(script),
    ]


def submit_ready_models(
    campaign: dict[str, Any],
    state: dict[str, Any],
    state_path: Path,
    repo_root: Path,
    scheduler: Slurm,
    execute: bool,
    actions: list[dict[str, Any]],
) -> None:
    active = 0
    for chain in state["chains"].values():
        for runtime in chain["segments"]:
            if active_attempt(runtime) is not None:
                active += 1
    slots = max(0, int(state["capacity"]["model_slots"]) - active)
    for chain_id, index in frontier_indices(campaign, state).items():
        if slots <= 0:
            break
        unretired = sum(
            1
            for candidate in state["chains"][chain_id]["segments"]
            if completion_report(candidate) is not None and not candidate.get("segment_retirement")
        )
        if unretired >= int(campaign["policy"].get("max_unretired_segments_per_chain", 2)):
            continue
        segment = spec_segment(campaign, chain_id, index)
        runtime = runtime_segment(state, chain_id, index)
        if runtime["terminal_error"] or completion_report(runtime) is not None:
            continue
        if active_attempt(runtime) is not None or not forcing_publication_passes(segment):
            continue
        previous = previous_completion(campaign, state, chain_id, index)
        if index > 0 and previous is None:
            continue
        sequence = len(runtime["attempts"]) + 1
        attempt_id = f"a{sequence:03d}-{uuid.uuid4().hex[:8]}"
        attempt_dir = Path(segment["attempt_root"]) / attempt_id
        token = f"{campaign['campaign_id']}:{chain_id}:{segment['segment_id']}:{attempt_id}"
        attempt = {
            "attempt_id": attempt_id,
            "attempt_dir": str(attempt_dir),
            "run_dir": str(attempt_dir / "run"),
            "restart_dir": str(attempt_dir / "restart"),
            "status": "SUBMITTING",
            "job_id": None,
            "job_name": submission_identity("hicm", token),
            "receipt": str(attempt_dir / "submission_receipt.json"),
            "submitted_at": utc_now(),
        }
        attempt["command"] = model_submission_command(
            campaign, segment, attempt, previous, repo_root
        )
        actions.append(
            {
                "action": "SUBMIT_MODEL",
                "chain_id": chain_id,
                "segment_id": segment["segment_id"],
                "attempt_id": attempt_id,
                "command": attempt["command"],
            }
        )
        if execute:
            attempt_dir.mkdir(parents=True, exist_ok=False)
            runtime["attempts"].append(attempt)
            runtime["status"] = "SUBMITTING"
            job_id = submit_recorded(
                item=attempt,
                scheduler=scheduler,
                state_path=state_path,
                state=state,
            )
            event(
                state,
                "MODEL_ATTEMPT_SUBMITTED",
                chain_id=chain_id,
                segment_id=segment["segment_id"],
                attempt_id=attempt_id,
                job_id=job_id,
            )
        slots -= 1


def submit_cpu_batch(
    campaign: dict[str, Any],
    state: dict[str, Any],
    state_path: Path,
    repo_root: Path,
    scheduler: Slurm,
    execute: bool,
    actions: list[dict[str, Any]],
) -> None:
    if state.get("cpu_batch") or int(state["capacity"]["cpu_slots"]) == 0:
        return
    tasks = pending_cpu_tasks(campaign, state)
    if not tasks:
        return
    task_kinds: dict[str, int] = {}
    failure_level = 0
    for task in tasks:
        kind = str(task["kind"])
        task_kinds[kind] = task_kinds.get(kind, 0) + 1
        runtime = runtime_segment(
            state,
            task["chain_id"],
            int(task["segment_index"]),
        )
        failure_level = max(
            failure_level,
            int(runtime["cpu_failures"].get(kind, 0)),
        )
    base_cpus = int(campaign["policy"].get("cpu_cpus_per_task", 4))
    maximum_cpus = int(campaign["policy"].get("cpu_retry_max_cpus_per_task", 16))
    cpus_per_task = min(base_cpus * (2**failure_level), maximum_cpus)
    batch_id = f"cpu-{uuid.uuid4().hex[:12]}"
    task_root = Path(campaign["controller"]["cpu_task_root"])
    task_file = task_root / f"{batch_id}.json"
    task_payload = {
        "schema_version": 1,
        "campaign_id": campaign["campaign_id"],
        "batch_id": batch_id,
        "tasks": tasks,
    }
    task_sha256 = json_payload_sha256(task_payload)
    token = f"{campaign['campaign_id']}:{batch_id}"
    job_name = submission_identity("hicc", token)
    script = (
        repo_root / "case_studies/swiss_200m/scripts/"
        "run_preemptible_campaign_cpu_task_balfrin.sbatch"
    ).resolve()
    command = [
        "sbatch",
        "--parsable",
        "--no-requeue",
        f"--partition={campaign['policy'].get('cpu_partition', 'pp-short')}",
        f"--cpus-per-task={cpus_per_task}",
        f"--array=0-{len(tasks) - 1}%{int(state['capacity']['cpu_slots'])}",
        f"--job-name={job_name}",
        f"--export={shell_export({'REPO_ROOT': str(repo_root), 'HICAR_CAMPAIGN_CPU_TASK_FILE': str(task_file), 'HICAR_CAMPAIGN_CPU_TASK_SHA256': task_sha256, 'HICAR_VALIDATION_PYTHON': campaign['python_environment']['python']})}",
        str(script),
    ]
    actions.append(
        {
            "action": "SUBMIT_CPU_BATCH",
            "batch_id": batch_id,
            "task_kind": tasks[0]["kind"],
            "task_kinds": task_kinds,
            "task_count": len(tasks),
            "cpus_per_task": cpus_per_task,
            "command": command,
        }
    )
    if not execute:
        return
    task_root.mkdir(parents=True, exist_ok=True)
    write_json_atomic(task_file, task_payload)
    if sha256(task_file) != task_sha256:
        raise RuntimeError(f"campaign CPU task hash mismatch after write: {task_file}")
    Path(f"{task_file}.ready").touch()
    batch = {
        "batch_id": batch_id,
        "task_file": str(task_file),
        "task_sha256": task_sha256,
        "tasks": tasks,
        "task_kinds": task_kinds,
        "cpus_per_task": cpus_per_task,
        "status": "SUBMITTING",
        "job_id": None,
        "job_name": job_name,
        "receipt": str(task_root / f"{batch_id}.submission.json"),
        "command": command,
        "submitted_at": utc_now(),
    }
    state["cpu_batch"] = batch
    job_id = submit_recorded(
        item=batch,
        scheduler=scheduler,
        state_path=state_path,
        state=state,
    )
    event(
        state,
        "CPU_BATCH_SUBMITTED",
        batch_id=batch_id,
        job_id=job_id,
        task_kind=tasks[0]["kind"],
        task_kinds=task_kinds,
        task_count=len(tasks),
        cpus_per_task=cpus_per_task,
    )


def exact_chain_output_times(
    campaign: dict[str, Any],
    state: dict[str, Any],
    chain: dict[str, Any],
) -> tuple[list[str], list[dict[str, Any]]]:
    interval = timedelta(seconds=int(campaign["model"]["output_interval_seconds"]))
    actual: list[str] = []
    evidence: list[dict[str, Any]] = []
    previous_end: str | None = None
    for index, segment in enumerate(chain["segments"]):
        if previous_end is not None and segment["start"] != previous_end:
            raise ValueError(f"campaign plan has a gap or overlap before {segment['segment_id']}")
        runtime = runtime_segment(state, chain["chain_id"], index)
        completion_path = Path(runtime["model_completion"])
        completion = published_json(completion_path, "model completion")
        if completion.get("status") != "PASS":
            raise ValueError(f"model completion is not PASS: {completion_path}")
        segment_times = completion.get("output", {}).get("times")
        if not isinstance(segment_times, list) or not segment_times:
            raise ValueError(f"model completion has no output times: {completion_path}")
        actual.extend(str(value) for value in segment_times)

        solver_path = Path(runtime["solver_report"])
        solver = published_json(solver_path, "solver audit")
        if solver.get("status") != "PASS":
            raise ValueError(f"solver audit is not PASS: {solver_path}")
        compressed = []
        for task in compression_tasks(chain["chain_id"], segment, runtime):
            if not task_ready(task, campaign):
                raise ValueError(f"compressed output is not published: {task['target']}")
            compression_path = Path(f"{task['target']}.compression.json")
            compressed.append(
                {
                    "path": task["target"],
                    "report": str(compression_path),
                    "report_sha256": sha256(compression_path),
                }
            )
        segment_retirement = Path(runtime["segment_retirement"])
        restart_retirement = Path(runtime["restart_retirement"])
        for path, label in (
            (segment_retirement, "segment retirement"),
            (restart_retirement, "restart retirement"),
        ):
            lifecycle = published_json(path, label)
            if lifecycle.get("status") != "PASS" or lifecycle.get("action") not in {
                "PRESERVED",
                "RETIRED",
            }:
                raise ValueError(f"{label} is not complete: {path}")
        evidence.append(
            {
                "segment_id": segment["segment_id"],
                "start": segment["start"],
                "end": segment["end"],
                "completion": str(completion_path),
                "completion_sha256": sha256(completion_path),
                "solver_report": str(solver_path),
                "solver_report_sha256": sha256(solver_path),
                "compressed": compressed,
                "segment_retirement": str(segment_retirement),
                "segment_retirement_sha256": sha256(segment_retirement),
                "restart_retirement": str(restart_retirement),
                "restart_retirement_sha256": sha256(restart_retirement),
            }
        )
        previous_end = segment["end"]

    start = datetime.fromisoformat(chain["segments"][0]["start"])
    end = datetime.fromisoformat(chain["segments"][-1]["end"])
    expected = []
    cursor = start
    while cursor <= end:
        expected.append(cursor.isoformat())
        cursor += interval
    if actual != expected:
        raise ValueError(
            f"chain {chain['chain_id']} output union is not exact: "
            f"expected {len(expected)} unique ordered times, found {len(actual)}"
        )
    return actual, evidence


def publish_campaign_completion(
    campaign: dict[str, Any],
    state: dict[str, Any],
) -> Path:
    output = Path(campaign["campaign_root"]) / "campaign_completion.json"
    marker = Path(f"{output}.ready")
    if output.is_file() and marker.is_file():
        existing = load_json(output)
        if (
            existing.get("status") != "PASS"
            or existing.get("campaign_id") != campaign["campaign_id"]
            or existing.get("campaign_sha256") != state["campaign_sha256"]
        ):
            raise ValueError(f"campaign completion conflicts with state: {output}")
        return output

    state.setdefault("completion_time", utc_now())
    chain_reports = []
    for chain in campaign["chains"]:
        times, evidence = exact_chain_output_times(campaign, state, chain)
        chain_reports.append(
            {
                "chain_id": chain["chain_id"],
                "start": chain["segments"][0]["start"],
                "end": chain["segments"][-1]["end"],
                "output_count": len(times),
                "output_times": times,
                "segments": evidence,
            }
        )
    payload = {
        "schema_version": 1,
        "status": "PASS",
        "campaign_id": campaign["campaign_id"],
        "campaign": state["campaign"],
        "campaign_sha256": state["campaign_sha256"],
        "goal": campaign.get("goal"),
        "resource_summary": campaign.get("resource_summary"),
        "completed_at": state["completion_time"],
        "chains": chain_reports,
    }
    if output.is_file():
        if load_json(output) != payload:
            raise ValueError(f"refusing to replace campaign completion: {output}")
    else:
        write_json_atomic(output, payload)
    marker.touch()
    return output


def update_campaign_status(
    campaign: dict[str, Any],
    state: dict[str, Any],
) -> None:
    blocked = []
    complete = True
    for chain in campaign["chains"]:
        chain_id = chain["chain_id"]
        for index, segment in enumerate(chain["segments"]):
            runtime = runtime_segment(state, chain_id, index)
            if runtime["terminal_error"]:
                blocked.append(f"{chain_id}/{segment['segment_id']}: {runtime['terminal_error']}")
            if (
                runtime["status"] != "COMPLETE"
                or not runtime["compression_complete"]
                or not runtime.get("lifecycle_complete")
            ):
                complete = False
    if complete and not forcing_cache_retirement_complete(campaign):
        complete = False
    if blocked:
        state["status"] = "BLOCKED"
        state["blockers"] = blocked
    elif complete:
        try:
            completion = publish_campaign_completion(campaign, state)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            state["status"] = "BLOCKED"
            state["blockers"] = [f"campaign completion validation failed: {exc}"]
        else:
            state["status"] = "COMPLETE"
            state["completion_report"] = str(completion)
            state["blockers"] = []
    else:
        state["status"] = "ACTIVE"
        state["blockers"] = []
    state["updated_at"] = utc_now()


def reconcile(
    *,
    campaign_path: Path,
    repo_root: Path,
    scheduler: Slurm,
    execute: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    campaign = published_json(campaign_path, "campaign plan")
    if campaign.get("model", {}).get("partition") != "preemptible":
        raise ValueError("campaign heavy-model partition must be preemptible")
    forcing_cache_index(campaign)
    validate_campaign_runtime(campaign, repo_root)
    state_path, lease_path = campaign_paths(campaign)
    actions: list[dict[str, Any]] = []
    with lease(lease_path, int(campaign["policy"]["lease_seconds"])):
        state = load_or_create_state(campaign_path, campaign, state_path)
        scheduler_states = scheduler.query(job_ids(state))
        refresh_model_attempts(campaign, state, scheduler_states, scheduler, execute)
        refresh_cpu_batch(campaign, state, scheduler_states, scheduler, execute)
        refresh_solver_and_compression(campaign, state)
        refresh_lifecycle(campaign, state)
        clear_resolved_lifecycle_terminal_errors(state)
        update_campaign_status(campaign, state)
        if state["status"] == "ACTIVE":
            submit_ready_models(
                campaign,
                state,
                state_path,
                repo_root,
                scheduler,
                execute,
                actions,
            )
            submit_cpu_batch(
                campaign,
                state,
                state_path,
                repo_root,
                scheduler,
                execute,
                actions,
            )
        update_campaign_status(campaign, state)
        write_json_atomic(state_path, state)
    return state, actions


def set_capacity(campaign_path: Path, models: int | None, cpus: int | None) -> dict[str, Any]:
    campaign = published_json(campaign_path, "campaign plan")
    state_path, lease_path = campaign_paths(campaign)
    with lease(lease_path, int(campaign["policy"]["lease_seconds"])):
        state = load_or_create_state(campaign_path, campaign, state_path)
        if models is not None:
            maximum = int(campaign["policy"]["model_node_budget"]) // int(
                campaign["model"]["nodes"]
            )
            if not 0 <= models <= maximum:
                raise ValueError(f"--models must be within 0..{maximum}")
            state["capacity"]["model_slots"] = models
        if cpus is not None:
            maximum = int(campaign["policy"].get("max_cpu_slots", 8))
            if not 0 <= cpus <= maximum:
                raise ValueError(f"--cpus must be within 0..{maximum}")
            state["capacity"]["cpu_slots"] = cpus
        event(
            state,
            "CAPACITY_UPDATED",
            model_slots=state["capacity"]["model_slots"],
            cpu_slots=state["capacity"]["cpu_slots"],
        )
        state["updated_at"] = utc_now()
        write_json_atomic(state_path, state)
    return state


def recover_published_cpu_batch(campaign_path: Path, task_file: Path) -> dict[str, Any]:
    """Clear a false CPU blocker only after every task has published valid evidence."""

    campaign = published_json(campaign_path, "campaign plan")
    state_path, lease_path = campaign_paths(campaign)
    task_root = Path(campaign["controller"]["cpu_task_root"]).resolve()
    task_file = task_file.resolve()
    if task_file.parent != task_root:
        raise ValueError(f"CPU task file is outside the campaign task root: {task_file}")
    payload = published_json(task_file, "campaign CPU task batch")
    if payload.get("campaign_id") != campaign["campaign_id"]:
        raise ValueError("CPU task batch belongs to a different campaign")
    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("CPU task batch has no tasks")
    missing = [task for task in tasks if not task_ready(task, campaign)]
    if missing:
        raise ValueError(
            f"CPU task batch has {len(missing)} task(s) without valid published evidence"
        )

    with lease(lease_path, int(campaign["policy"]["lease_seconds"])):
        state = load_or_create_state(campaign_path, campaign, state_path)
        recovered: set[tuple[str, int, str]] = set()
        for task in tasks:
            chain_id = str(task["chain_id"])
            segment_index = int(task["segment_index"])
            kind = str(task["kind"])
            runtime = runtime_segment(state, chain_id, segment_index)
            error = runtime.get("terminal_error")
            if not isinstance(error, str) or not error.startswith(f"CPU task {kind} failed"):
                continue
            runtime["terminal_error"] = None
            if runtime["status"] == "BLOCKED":
                runtime["status"] = (
                    "WAITING_FORCING" if completion_report(runtime) is None else "MODEL_PUBLISHED"
                )
            failures = max(int(runtime["cpu_failures"].get(kind, 0)) - 1, 0)
            if failures:
                runtime["cpu_failures"][kind] = failures
            else:
                runtime["cpu_failures"].pop(kind, None)
            recovered.add((chain_id, segment_index, kind))
        if not recovered:
            raise ValueError("CPU task batch does not match a recoverable campaign blocker")
        event(
            state,
            "CPU_BATCH_EVIDENCE_RECOVERED",
            batch_id=payload.get("batch_id"),
            task_file=str(task_file),
            task_sha256=sha256(task_file),
            recovered=[
                {"chain_id": chain_id, "segment_index": index, "kind": kind}
                for chain_id, index, kind in sorted(recovered)
            ],
        )
        update_campaign_status(campaign, state)
        write_json_atomic(state_path, state)
    return state


def print_summary(state: dict[str, Any]) -> None:
    counts: dict[str, int] = {}
    for chain in state["chains"].values():
        for segment in chain["segments"]:
            counts[segment["status"]] = counts.get(segment["status"], 0) + 1
    print(
        json.dumps(
            {
                "campaign_id": state["campaign_id"],
                "goal": state.get("goal"),
                "status": state["status"],
                "capacity": state["capacity"],
                "segment_status_counts": counts,
                "cpu_batch": (
                    {
                        "batch_id": state["cpu_batch"]["batch_id"],
                        "status": state["cpu_batch"]["status"],
                        "job_id": state["cpu_batch"].get("job_id"),
                        "task_count": len(state["cpu_batch"]["tasks"]),
                    }
                    if state.get("cpu_batch")
                    else None
                ),
                "blockers": state.get("blockers", []),
                "updated_at": state["updated_at"],
            },
            indent=2,
            sort_keys=True,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "reconcile", "status"):
        sub = subparsers.add_parser(name)
        sub.add_argument("--campaign", type=Path, required=True)
        if name in {"init", "reconcile"}:
            sub.add_argument("--repo-root", type=Path, required=True)
        if name == "reconcile":
            sub.add_argument("--execute", action="store_true")
    watch = subparsers.add_parser("watch")
    watch.add_argument("--campaign", type=Path, required=True)
    watch.add_argument("--repo-root", type=Path, required=True)
    watch.add_argument("--execute", action="store_true")
    watch.add_argument("--poll-seconds", type=int, default=60)
    watch.add_argument("--max-seconds", type=int, default=82800)
    capacity = subparsers.add_parser("set-capacity")
    capacity.add_argument("--campaign", type=Path, required=True)
    capacity.add_argument("--models", type=int)
    capacity.add_argument("--cpus", type=int)
    recovery = subparsers.add_parser("recover-cpu-batch")
    recovery.add_argument("--campaign", type=Path, required=True)
    recovery.add_argument("--task-file", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    campaign_path = args.campaign.resolve()
    if args.command == "set-capacity":
        state = set_capacity(campaign_path, args.models, args.cpus)
        print_summary(state)
        return 0
    if args.command == "recover-cpu-batch":
        state = recover_published_cpu_batch(campaign_path, args.task_file)
        print_summary(state)
        return 0
    campaign = published_json(campaign_path, "campaign plan")
    state_path, lease_path = campaign_paths(campaign)
    if args.command == "status":
        if not state_path.is_file():
            raise SystemExit("campaign state is not initialized")
        state = load_json(state_path)
        if state.get("campaign_sha256") != sha256(campaign_path):
            raise SystemExit("campaign state belongs to a different campaign")
        print_summary(state)
        return 0
    if args.command == "init":
        forcing_cache_index(campaign)
        validate_campaign_runtime(campaign, args.repo_root.resolve())
        with lease(lease_path, int(campaign["policy"]["lease_seconds"])):
            state = load_or_create_state(campaign_path, campaign, state_path)
        print_summary(state)
        return 0
    if args.command == "reconcile":
        state, actions = reconcile(
            campaign_path=campaign_path,
            repo_root=args.repo_root.resolve(),
            scheduler=Slurm(),
            execute=args.execute,
        )
        print(json.dumps({"actions": actions}, indent=2, sort_keys=True))
        print_summary(state)
        return 2 if state["status"] == "BLOCKED" else 0

    if args.poll_seconds < 10:
        raise SystemExit("--poll-seconds must be at least 10")
    if args.max_seconds <= 0:
        raise SystemExit("--max-seconds must be positive")
    deadline = time.monotonic() + args.max_seconds
    while True:
        state, actions = reconcile(
            campaign_path=campaign_path,
            repo_root=args.repo_root.resolve(),
            scheduler=Slurm(),
            execute=args.execute,
        )
        print(json.dumps({"actions": actions}, indent=2, sort_keys=True))
        print_summary(state)
        if state["status"] in {"BLOCKED", "COMPLETE"}:
            return 2 if state["status"] == "BLOCKED" else 0
        if not args.execute or time.monotonic() + args.poll_seconds > deadline:
            return 0
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
