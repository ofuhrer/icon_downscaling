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


def payload_sha256(payload: dict[str, Any]) -> str:
    content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    return hashlib.sha256(content.encode()).hexdigest()


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


def independent_chain_scope(definition: dict[str, Any]) -> dict[str, Any]:
    model = definition["model"]
    chains = [
        {
            "chain_id": str(chain["chain_id"]),
            "start": parse_time(str(chain["start"])).strftime(TIME_FORMAT),
            "end": parse_time(str(chain["end"])).strftime(TIME_FORMAT),
            "rea_l_land_initialization": bool(
                chain.get("rea_l_land_initialization", True)
            ),
        }
        for chain in definition["chains"]
    ]
    return {
        "schema_version": 1,
        "campaign_id": str(definition["campaign_id"]),
        "expected_hicar_commit": str(model["expected_hicar_commit"]),
        "static_file": str(Path(model["static_file"]).resolve()),
        "chain_count": len(chains),
        "chains": chains,
    }


def require_independent_chain_authorization(
    definition: dict[str, Any],
) -> dict[str, Any] | None:
    scope = independent_chain_scope(definition)
    if scope["chain_count"] <= 1:
        return None
    value = definition.get("independent_chain_authorization")
    if not value:
        raise ValueError(
            "multiple independent chains require independent_chain_authorization"
        )
    path = Path(value).resolve()
    authorization = published_json(path, "independent-chain authorization")
    if authorization.get("schema_version") != 1:
        raise ValueError("independent-chain authorization schema is not 1")
    if authorization.get("status") != "PASS":
        raise ValueError("independent-chain authorization is not PASS")
    if authorization.get("decision") not in {
        "GO_INDEPENDENT_CHAINS",
        "GO_20_YEAR_200M_PRODUCTION",
    }:
        raise ValueError("independent-chain authorization has no accepted decision")
    scope_hash = payload_sha256(scope)
    if (
        authorization.get("scope") != scope
        or authorization.get("scope_sha256") != scope_hash
    ):
        raise ValueError(
            "independent-chain authorization does not match this campaign"
        )
    return {
        "path": str(path),
        "sha256": sha256(path),
        "decision": authorization["decision"],
        "scope": scope,
        "scope_sha256": scope_hash,
    }


def require_production_authorization(
    definition: dict[str, Any],
) -> dict[str, Any] | None:
    purpose = definition.get("purpose", "qualification")
    if purpose not in {"qualification", "production"}:
        raise ValueError("purpose must be qualification or production")
    if purpose != "production":
        return None
    value = definition.get("production_authorization")
    if not value:
        raise ValueError("production purpose requires production_authorization")
    path = Path(value).resolve()
    authorization = published_json(path, "production authorization")
    accepted = (
        authorization.get("assessment_status") == "COMPLETE"
        and authorization.get("decision") == "GO_20_YEAR_200M_PRODUCTION"
        and authorization.get("authorization", {}).get(
            "twenty_year_200m_production"
        )
    )
    if not accepted:
        raise ValueError("production authorization is not GO_20_YEAR_200M_PRODUCTION")
    return {
        "path": str(path),
        "sha256": sha256(path),
        "decision": authorization["decision"],
    }


def run_chunk_planner(
    *,
    planner: Path,
    start: datetime,
    end: datetime,
    chunk_id: str,
    chunk_root: Path,
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
    if not 1 <= model["nodes"] <= 44:
        raise ValueError("model.nodes must be within 1..44")
    model["time_limit"] = str(model.get("time_limit", "06:00:00"))
    time_limit_seconds = slurm_time_seconds(model["time_limit"])
    if not 600 <= time_limit_seconds <= 6 * 3600:
        raise ValueError("model.time_limit must be within 00:10:00..06:00:00")
    model["output_profile"] = str(model.get("output_profile", "routine"))
    model["output_interval_seconds"] = int(
        model.get("output_interval_seconds", 3600)
    )
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
            "segment duration must be divisible by model.output_interval_seconds"
        )
    model_node_budget = int(policy.get("model_node_budget", 44))
    if not 1 <= model_node_budget <= 44:
        raise ValueError("policy.model_node_budget must be within 1..44")
    maximum_model_slots = model_node_budget // model["nodes"]
    model_slots = int(policy.get("model_slots", maximum_model_slots))
    cpu_slots = int(policy.get("cpu_slots", 2))
    if not 1 <= model_slots <= maximum_model_slots:
        raise ValueError(
            "policy.model_slots exceeds the model-node budget: "
            f"maximum is {maximum_model_slots}"
        )
    if not 1 <= cpu_slots <= 2:
        raise ValueError("policy.cpu_slots must be within 1..2")
    rolling_retirement = policy.get("rolling_retirement", True)
    if not isinstance(rolling_retirement, bool):
        raise ValueError("policy.rolling_retirement must be boolean")
    policy.update(
        {
            "segment_hours": segment_hours,
            "model_node_budget": model_node_budget,
            "model_slots": model_slots,
            "cpu_slots": cpu_slots,
            "prefetch_segments_per_chain": int(
                policy.get("prefetch_segments_per_chain", 1)
            ),
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
    if policy["max_model_attempts"] < 0 or policy["max_cpu_attempts"] < 1:
        raise ValueError(
            "model retry limit must be non-negative and CPU retry limit positive"
        )
    if policy["prefetch_segments_per_chain"] < 0:
        raise ValueError("prefetch_segments_per_chain must be non-negative")
    if policy["lease_seconds"] < 60:
        raise ValueError("lease_seconds must be at least 60")
    if not policy["rolling_retirement"]:
        raise ValueError(
            "pre-emptible campaigns require policy.rolling_retirement=true"
        )
    if policy["preserve_restart_every_segments"] < 0:
        raise ValueError(
            "preserve_restart_every_segments must be non-negative"
        )
    if policy["max_unretired_segments_per_chain"] < 1:
        raise ValueError(
            "max_unretired_segments_per_chain must be positive"
        )

    purpose = definition.get("purpose", "qualification")
    production_authorization = require_production_authorization(definition)
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
    authorization = require_independent_chain_authorization(definition)
    chunk_planner = (
        repo_root
        / "case_studies/swiss_200m/streaming/create_chunk_plan.py"
    ).resolve()
    if not chunk_planner.is_file():
        raise ValueError(f"missing chunk planner: {chunk_planner}")

    prepared_chains = []
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
        duration_hours = int((end - start).total_seconds() // 3600)
        if duration_hours % segment_hours:
            raise ValueError(
                f"chain {chain_id} duration must be divisible by segment_hours"
            )
        segments = []
        cursor = start
        sequence = 0
        while cursor < end:
            sequence += 1
            segment_end = cursor + timedelta(hours=segment_hours)
            stamp = cursor.strftime("%Y%m%dT%H%M%S")
            segment_id = f"{chain_id}_{stamp}_{segment_hours:02d}h"
            segment_root = (
                campaign_root
                / "chains"
                / chain_id
                / "segments"
                / f"{sequence:05d}_{stamp}"
            )
            chunk_root = segment_root / "forcing_chunk"
            plan = run_chunk_planner(
                planner=chunk_planner,
                start=cursor,
                end=segment_end,
                chunk_id=segment_id,
                chunk_root=chunk_root,
                producer_concurrency=cpu_slots,
            )
            plan_payload = load_json(plan)
            segments.append(
                {
                    "sequence": sequence,
                    "segment_id": segment_id,
                    "start": cursor.strftime(TIME_FORMAT),
                    "end": segment_end.strftime(TIME_FORMAT),
                    "hours": segment_hours,
                    "plan": str(plan),
                    "plan_sha256": sha256(plan),
                    "forcing_publication": str(
                        Path(plan_payload["chunk_root"])
                        / "forcing_publication.json"
                    ),
                    "attempt_root": str(segment_root / "attempts"),
                    "compressed_root": str(segment_root / "compressed"),
                    "lifecycle_root": str(segment_root / "lifecycle"),
                    "rea_l_land_initialization": bool(
                        sequence == 1 and chain.get("rea_l_land_initialization", True)
                    ),
                }
            )
            cursor = segment_end
        prepared_chains.append({"chain_id": chain_id, "segments": segments})

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
        "independent_chain_authorization": authorization,
        "production_authorization": production_authorization,
        "model": model,
        "policy": policy,
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
