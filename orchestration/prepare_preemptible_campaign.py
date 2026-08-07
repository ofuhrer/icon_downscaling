#!/usr/bin/env python3
"""Prepare immutable short-slice plans for a pre-emptible HICAR campaign."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from runtime_contract import (
    S83_APPROVED_PARTITIONS,
    validate_python_environment,
    validate_runtime_release,
)

TIME_FORMAT = "%Y-%m-%dT%H:%M:%S"
COMMIT = re.compile(r"^[0-9a-f]{40}$")
SLURM_TIME = re.compile(r"^(?:(\d+)-)?(\d{1,2}):([0-5]\d):([0-5]\d)$")
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


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


def published_json(path: Path, label: str) -> dict[str, Any]:
    marker = Path(f"{path}.ready")
    if not path.is_file() or not marker.is_file():
        raise ValueError(f"{label} is not published: {path}")
    return load_json(path)


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        raise ValueError(f"timestamp must be naive UTC: {value}")
    if parsed.minute or parsed.second or parsed.microsecond:
        raise ValueError(f"timestamp is not on an exact hour: {value}")
    return parsed


def slurm_time_seconds(value: str) -> int:
    match = SLURM_TIME.fullmatch(value)
    if match is None:
        raise ValueError(f"invalid Slurm time limit: {value}")
    days, hours, minutes, seconds = (int(item or 0) for item in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def normalize_goal(definition: dict[str, Any]) -> dict[str, Any]:
    """Keep the campaign tied to a useful question, not an approval artifact."""
    goal = definition.get("goal")
    if not isinstance(goal, dict):
        raise ValueError("campaign definition requires a goal")
    normalized: dict[str, Any] = {}
    for key in ("outcome", "why_now", "resource_rationale"):
        value = goal.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"goal.{key} must be a non-empty string")
        normalized[key] = value.strip()
    for key in ("evidence_needed", "stop_conditions"):
        values = goal.get(key)
        if (
            not isinstance(values, list)
            or not values
            or any(not isinstance(value, str) or not value.strip() for value in values)
        ):
            raise ValueError(f"goal.{key} must contain non-empty strings")
        normalized[key] = [value.strip() for value in values]
    return normalized


def run_chunk_planner(
    *,
    planner: Path,
    start: datetime,
    end: datetime,
    chunk_id: str,
    chunk_root: Path,
    forcing_dir: Path,
    producer_root: Path,
    producer_concurrency: int,
) -> Path:
    plan = chunk_root / "chunk_plan.json"
    subprocess.run(
        [
            sys.executable,
            str(planner),
            "--start",
            start.strftime(TIME_FORMAT),
            "--end",
            end.strftime(TIME_FORMAT),
            "--chunk-id",
            chunk_id,
            "--chunk-root",
            str(chunk_root),
            "--plan",
            str(plan),
            "--forcing-dir",
            str(forcing_dir),
            "--producer-root",
            str(producer_root),
            "--producer-concurrency",
            str(producer_concurrency),
        ],
        check=True,
    )
    published_json(plan, "short-slice chunk plan")
    return plan.resolve()


def build_campaign(
    definition_path: Path,
    output: Path,
    repo_root: Path,
) -> dict[str, Any]:
    definition = load_json(definition_path)
    if definition.get("schema_version") != 1:
        raise ValueError("campaign definition schema_version must be 1")
    campaign_id = str(definition["campaign_id"])
    if not IDENTIFIER.fullmatch(campaign_id):
        raise ValueError("campaign_id contains unsafe characters")
    campaign_root = Path(definition["campaign_root"]).resolve()
    chains = definition.get("chains", [])
    if not chains:
        raise ValueError("campaign definition has no chains")

    model = dict(definition["model"])
    expected_commit = str(model.get("expected_hicar_commit", ""))
    if not COMMIT.fullmatch(expected_commit):
        raise ValueError("model.expected_hicar_commit must be a full commit")
    partition = model.get("partition", "preemptible")
    if partition != "preemptible":
        raise ValueError("heavy HICAR campaign jobs must initially use preemptible")
    model["partition"] = partition
    model["nodes"] = int(model.get("nodes", 4))
    if not 1 <= model["nodes"] <= 46:
        raise ValueError("model.nodes must be within 1..46")
    model["time_limit"] = str(model.get("time_limit", "06:00:00"))
    time_limit_seconds = slurm_time_seconds(model["time_limit"])
    if not 600 <= time_limit_seconds <= 6 * 3600:
        raise ValueError("model.time_limit must be within 00:10:00..06:00:00")
    model["output_profile"] = str(model.get("output_profile", "routine"))
    model["output_interval_seconds"] = int(model.get("output_interval_seconds", 3600))
    if model["output_interval_seconds"] <= 0:
        raise ValueError("model.output_interval_seconds must be positive")
    for key in ("case_root", "hicar_root", "static_file"):
        model[key] = str(Path(model[key]).resolve())
    for key in ("script", "build_root"):
        if model.get(key):
            model[key] = str(Path(model[key]).resolve())

    policy = dict(definition.get("policy", {}))
    segment_hours = int(policy.get("segment_hours", 24))
    if segment_hours <= 0 or segment_hours > 24:
        raise ValueError("policy.segment_hours must be within 1..24")
    if (segment_hours * 3600) % model["output_interval_seconds"]:
        raise ValueError(
            "maximum segment duration must be divisible by model.output_interval_seconds"
        )
    model_node_budget = int(policy.get("model_node_budget", 46))
    if not 1 <= model_node_budget <= 46:
        raise ValueError("policy.model_node_budget must be within 1..46")
    maximum_model_slots = model_node_budget // model["nodes"]
    if maximum_model_slots < 1:
        raise ValueError("policy.model_node_budget must fit at least one model attempt")
    model_slots = int(policy.get("model_slots", maximum_model_slots))
    cpu_partition = str(policy.get("cpu_partition", "pp-short"))
    if cpu_partition != "pp-short" or cpu_partition not in S83_APPROVED_PARTITIONS:
        raise ValueError(
            "short campaign pre/post-processing must use the s83-open pp-short partition"
        )
    max_cpu_slots = 8
    cpu_slots = int(policy.get("cpu_slots", max_cpu_slots))
    cpu_cpus_per_task = int(policy.get("cpu_cpus_per_task", 4))
    cpu_retry_max_cpus_per_task = int(policy.get("cpu_retry_max_cpus_per_task", 16))
    if not 1 <= model_slots <= maximum_model_slots:
        raise ValueError(
            f"policy.model_slots exceeds the model-node budget: maximum is {maximum_model_slots}"
        )
    if not 1 <= cpu_slots <= max_cpu_slots:
        raise ValueError(f"policy.cpu_slots must be within 1..{max_cpu_slots}")
    if cpu_cpus_per_task < 4:
        raise ValueError(
            "policy.cpu_cpus_per_task must reserve at least four cores "
            "as the shared-node memory proxy"
        )
    if cpu_retry_max_cpus_per_task < cpu_cpus_per_task or cpu_retry_max_cpus_per_task > 32:
        raise ValueError(
            "policy.cpu_retry_max_cpus_per_task must be between the base CPU request and 32"
        )
    rolling_retirement = policy.get("rolling_retirement", True)
    if not isinstance(rolling_retirement, bool):
        raise ValueError("policy.rolling_retirement must be boolean")
    policy.update(
        {
            "segment_hours": segment_hours,
            "model_node_budget": model_node_budget,
            "model_slots": model_slots,
            "cpu_slots": cpu_slots,
            "max_cpu_slots": max_cpu_slots,
            "cpu_partition": cpu_partition,
            "cpu_cpus_per_task": cpu_cpus_per_task,
            "cpu_retry_max_cpus_per_task": cpu_retry_max_cpus_per_task,
            "max_cpu_batch_tasks": int(policy.get("max_cpu_batch_tasks", 32)),
            "shared_forcing_cache": True,
            "input_task_weight": int(policy.get("input_task_weight", 3)),
            "post_task_weight": int(policy.get("post_task_weight", 1)),
            "prefetch_segments_per_chain": int(policy.get("prefetch_segments_per_chain", 1)),
            "max_model_attempts": int(policy.get("max_model_attempts", 0)),
            "max_cpu_attempts": int(policy.get("max_cpu_attempts", 3)),
            "lease_seconds": int(policy.get("lease_seconds", 300)),
            "rolling_retirement": rolling_retirement,
            "preserve_restart_every_segments": int(
                policy.get("preserve_restart_every_segments", 30)
            ),
            "max_unretired_segments_per_chain": int(
                policy.get("max_unretired_segments_per_chain", 2)
            ),
        }
    )
    if policy["max_model_attempts"] < 0:
        raise ValueError("model retry limit must be non-negative")
    if policy["max_cpu_attempts"] != 3:
        raise ValueError("policy.max_cpu_attempts must be 3 for bounded shared-node recovery")
    if policy["prefetch_segments_per_chain"] < 0:
        raise ValueError("prefetch_segments_per_chain must be non-negative")
    if policy["max_cpu_batch_tasks"] < 1:
        raise ValueError("max_cpu_batch_tasks must be positive")
    if policy["input_task_weight"] < 1 or policy["post_task_weight"] < 1:
        raise ValueError("input and post task weights must be positive")
    if policy["lease_seconds"] < 60:
        raise ValueError("lease_seconds must be at least 60")
    if not policy["rolling_retirement"]:
        raise ValueError("pre-emptible campaigns require policy.rolling_retirement=true")
    if policy["preserve_restart_every_segments"] < 0:
        raise ValueError("preserve_restart_every_segments must be non-negative")
    if policy["max_unretired_segments_per_chain"] < 1:
        raise ValueError("max_unretired_segments_per_chain must be positive")

    purpose = definition.get("purpose", "experiment")
    if purpose not in {"experiment", "qualification", "production"}:
        raise ValueError("purpose must be experiment, qualification, or production")
    goal = normalize_goal(definition)
    runtime_value = definition.get("runtime_release")
    if not runtime_value:
        raise ValueError("campaign definition requires runtime_release")
    runtime_path = Path(runtime_value).resolve()
    runtime_payload = validate_runtime_release(
        runtime_path,
        expected_root=repo_root,
        production=purpose == "production",
    )
    runtime_release = {
        "path": str(runtime_path),
        "sha256": sha256(runtime_path),
        "release_root": runtime_payload["release_root"],
        "purpose": runtime_payload["purpose"],
        "source_commit": runtime_payload["source_commit"],
        "source_dirty": runtime_payload["source_dirty"],
    }
    python_value = definition.get("python_environment")
    if not python_value:
        raise ValueError("campaign definition requires python_environment")
    python_path = Path(python_value).resolve()
    python_payload = validate_python_environment(
        python_path,
        runtime_path,
        smoke=True,
    )
    python_environment = {
        "path": str(python_path),
        "sha256": sha256(python_path),
        "python": python_payload["python"],
        "python_version": python_payload["python_version"],
        "requirements_sha256": python_payload["requirements_sha256"],
    }
    chunk_planner = (repo_root / "case_studies/swiss_200m/streaming/create_chunk_plan.py").resolve()
    if not chunk_planner.is_file():
        raise ValueError(f"missing chunk planner: {chunk_planner}")
    forcing_cache_root = (campaign_root / "forcing_cache").resolve()
    forcing_records_root = (forcing_cache_root / "records").resolve()
    forcing_producer_root = (forcing_cache_root / "producer").resolve()

    prepared_chains = []
    shared_records: dict[str, dict[str, Any]] = {}
    seen_chain_ids: set[str] = set()
    for chain in chains:
        chain_id = str(chain["chain_id"])
        if not IDENTIFIER.fullmatch(chain_id):
            raise ValueError(f"chain_id contains unsafe characters: {chain_id}")
        if chain_id in seen_chain_ids:
            raise ValueError(f"duplicate chain_id: {chain_id}")
        seen_chain_ids.add(chain_id)
        start = parse_time(str(chain["start"]))
        end = parse_time(str(chain["end"]))
        if end <= start:
            raise ValueError(f"chain {chain_id} end must follow start")
        chain_static_file = str(Path(chain.get("static_file", model["static_file"])).resolve())
        duration_seconds = int((end - start).total_seconds())
        if duration_seconds % model["output_interval_seconds"]:
            raise ValueError(
                f"chain {chain_id} duration must be divisible by model.output_interval_seconds"
            )
        segments = []
        cursor = start
        sequence = 0
        while cursor < end:
            sequence += 1
            remaining_hours = int((end - cursor).total_seconds() // 3600)
            current_segment_hours = min(segment_hours, remaining_hours)
            segment_end = cursor + timedelta(hours=current_segment_hours)
            stamp = cursor.strftime("%Y%m%dT%H%M%S")
            segment_id = f"{chain_id}_{stamp}_{current_segment_hours:02d}h"
            segment_root = (
                campaign_root / "chains" / chain_id / "segments" / f"{sequence:05d}_{stamp}"
            )
            chunk_root = segment_root / "forcing_chunk"
            plan = run_chunk_planner(
                planner=chunk_planner,
                start=cursor,
                end=segment_end,
                chunk_id=segment_id,
                chunk_root=chunk_root,
                forcing_dir=forcing_records_root,
                producer_root=forcing_producer_root,
                producer_concurrency=cpu_slots,
            )
            plan_payload = load_json(plan)
            cache_contract = plan_payload.get("forcing_cache", {})
            if (
                cache_contract.get("shared") is not True
                or Path(cache_contract.get("records_root", "")).resolve() != forcing_records_root
                or Path(cache_contract.get("producer_root", "")).resolve() != forcing_producer_root
            ):
                raise ValueError(f"chunk plan lacks the shared forcing cache: {plan}")
            for record in plan_payload["records"]:
                forcing_file = str(Path(record["forcing_file"]).resolve())
                existing = shared_records.get(forcing_file)
                identity = {
                    "valid_time": record["valid_time"],
                    "cycle_date": record["cycle_date"],
                    "cycle_time": record["cycle_time"],
                    "step_hours": record["step_hours"],
                    "forcing_file": forcing_file,
                }
                if existing is None:
                    existing = {**identity, "consumers": []}
                    shared_records[forcing_file] = existing
                elif any(existing[key] != value for key, value in identity.items()):
                    raise ValueError(f"shared forcing cache identity collision: {forcing_file}")
                existing["consumers"].append(
                    {
                        "chain_id": chain_id,
                        "segment_index": sequence - 1,
                        "segment_id": segment_id,
                        "plan": str(plan),
                        "forcing_publication": str(
                            Path(plan_payload["chunk_root"]) / "forcing_publication.json"
                        ),
                    }
                )
            segments.append(
                {
                    "sequence": sequence,
                    "segment_id": segment_id,
                    "start": cursor.strftime(TIME_FORMAT),
                    "end": segment_end.strftime(TIME_FORMAT),
                    "hours": current_segment_hours,
                    "plan": str(plan),
                    "plan_sha256": sha256(plan),
                    "forcing_publication": str(
                        Path(plan_payload["chunk_root"]) / "forcing_publication.json"
                    ),
                    "attempt_root": str(segment_root / "attempts"),
                    "compressed_root": str(segment_root / "compressed"),
                    "lifecycle_root": str(segment_root / "lifecycle"),
                    "static_file": chain_static_file,
                    "rea_l_land_initialization": bool(
                        sequence == 1 and chain.get("rea_l_land_initialization", True)
                    ),
                }
            )
            cursor = segment_end
        prepared_chains.append(
            {
                "chain_id": chain_id,
                "static_file": chain_static_file,
                "segments": segments,
            }
        )

    cache_index_path = forcing_cache_root / "index.json"
    cache_index = {
        "schema_version": 1,
        "status": "PLANNED",
        "campaign_id": campaign_id,
        "shared": True,
        "records_root": str(forcing_records_root),
        "producer_root": str(forcing_producer_root),
        "static_file": model["static_file"],
        "record_count": len(shared_records),
        "records": [shared_records[path] for path in sorted(shared_records)],
    }
    if cache_index_path.exists() or Path(f"{cache_index_path}.ready").exists():
        existing = published_json(cache_index_path, "forcing cache index")
        if existing != cache_index:
            raise ValueError(f"refusing to replace forcing cache index: {cache_index_path}")
    else:
        write_json_atomic(cache_index_path, cache_index)
        Path(f"{cache_index_path}.ready").touch()

    payload = {
        "schema_version": 1,
        "status": "PLANNED",
        "campaign_id": campaign_id,
        "purpose": purpose,
        "campaign_root": str(campaign_root),
        "definition": str(definition_path.resolve()),
        "definition_sha256": sha256(definition_path),
        "runtime_release": runtime_release,
        "python_environment": python_environment,
        "goal": goal,
        "model": model,
        "policy": policy,
        "resource_summary": {
            "nodes_per_attempt": model["nodes"],
            "node_budget": model_node_budget,
            "model_slots": model_slots,
            "maximum_slots_within_budget": maximum_model_slots,
            "maximum_concurrent_nodes": model_slots * model["nodes"],
            "unused_nodes_at_capacity": (model_node_budget - model_slots * model["nodes"]),
            "cpu_slots": cpu_slots,
            "cpu_cpus_per_task": cpu_cpus_per_task,
        },
        "forcing_cache": {
            "shared": True,
            "root": str(forcing_cache_root),
            "records_root": str(forcing_records_root),
            "producer_root": str(forcing_producer_root),
            "static_file": model["static_file"],
            "index": str(cache_index_path),
            "index_sha256": sha256(cache_index_path),
        },
        "chains": prepared_chains,
        "controller": {
            "state": str(campaign_root / "controller_state.json"),
            "lease": str(campaign_root / "controller_state.lease"),
            "cpu_task_root": str(campaign_root / "cpu_tasks"),
        },
    }
    if output.exists() or Path(f"{output}.ready").exists():
        existing = published_json(output, "campaign plan")
        if existing != payload:
            raise ValueError(f"refusing to replace campaign plan: {output}")
        return existing
    write_json_atomic(output, payload)
    Path(f"{output}.ready").touch()
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--definition", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    args = parser.parse_args()
    payload = build_campaign(
        args.definition.resolve(),
        args.output.resolve(),
        args.repo_root.resolve(),
    )
    count = sum(len(chain["segments"]) for chain in payload["chains"])
    print(
        f"pre-emptible campaign planned: {payload['campaign_id']} "
        f"chains={len(payload['chains'])} segments={count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
