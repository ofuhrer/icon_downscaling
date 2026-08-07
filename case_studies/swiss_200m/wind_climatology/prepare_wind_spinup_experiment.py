#!/usr/bin/env python3
"""Prepare independent cold-start chains for the HICAR wind spin-up gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


TIME_FORMAT = "%Y-%m-%dT%H:%M:%S"


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        raise ValueError("timestamps must be naive UTC")
    if parsed.minute or parsed.second or parsed.microsecond:
        raise ValueError("timestamps must be on exact hours")
    return parsed


def parse_case(value: str) -> tuple[str, datetime]:
    case_id, separator, timestamp = value.partition("=")
    if not separator or not case_id:
        raise ValueError("--case must be CASE_ID=YYYY-MM-DDTHH:MM:SS")
    if not all(character.isalnum() or character in "._-" for character in case_id):
        raise ValueError(f"unsafe case id: {case_id}")
    return case_id, parse_time(timestamp)


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


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_experiment(
    *,
    contract_path: Path,
    cases: list[tuple[str, datetime]],
    output: Path,
    static_root: Path | None = None,
) -> dict[str, Any]:
    contract = json.loads(contract_path.read_text())
    if contract.get("schema_version") != 1:
        raise ValueError("production candidate schema_version must be 1")
    spinup = contract["spinup"]
    candidate_hours = [int(value) for value in spinup["candidate_hours"]]
    if candidate_hours != sorted(set(candidate_hours)) or candidate_hours[0] < 0:
        raise ValueError("spin-up candidates must be unique non-negative hours")
    reference = int(spinup["reference_spinup_hours"])
    if reference != candidate_hours[-1]:
        raise ValueError("reference spin-up must be the longest candidate")
    retained_hours = int(spinup["retained_hours"])
    overlap_hours = int(spinup["overlap_hours"])
    cadence = int(spinup["screen_output_interval_seconds"])
    if retained_hours <= 0 or overlap_hours < 0 or cadence <= 0:
        raise ValueError("invalid retained duration, overlap, or cadence")
    if not cases:
        raise ValueError("at least one case is required")
    case_ids = [case_id for case_id, _ in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("case ids must be unique")

    runs: list[dict[str, Any]] = []
    chains: list[dict[str, Any]] = []
    for case_id, target_start in cases:
        target_end = target_start + timedelta(hours=retained_hours)
        assessment_end = target_end + timedelta(hours=overlap_hours)
        for hours in candidate_hours:
            integration_start = target_start - timedelta(hours=hours)
            chain_id = f"spinup-{case_id}-{hours:02d}h"
            static_file = None
            if static_root is not None:
                stamp = integration_start.strftime("%Y%m%d_%H%M")
                static_file = str(
                    (static_root / f"domain_static_alpine_bridge_200m_rea_l_{stamp}.nc").resolve()
                )
            run = {
                "run_id": chain_id,
                "case_id": case_id,
                "spinup_hours": hours,
                "reference": hours == reference,
                "integration_start": integration_start.strftime(TIME_FORMAT),
                "integration_end": assessment_end.strftime(TIME_FORMAT),
                "discard_before": target_start.strftime(TIME_FORMAT),
                "retained_start_exclusive": target_start.strftime(TIME_FORMAT),
                "retained_end_inclusive": target_end.strftime(TIME_FORMAT),
                "overlap_end_inclusive": assessment_end.strftime(TIME_FORMAT),
                "output_interval_seconds": cadence,
                "output_profile": contract["production_output"]["profile"],
                "rea_l_land_initialization": True,
            }
            if static_file is not None:
                run["static_file"] = static_file
            runs.append(run)
            chain = {
                "chain_id": chain_id,
                "start": run["integration_start"],
                "end": run["integration_end"],
                "rea_l_land_initialization": True,
            }
            if static_file is not None:
                chain["static_file"] = static_file
            chains.append(chain)

    payload: dict[str, Any] = {
        "schema_version": 1,
        "status": "PLANNED_QUALIFICATION_NOT_AUTHORIZED",
        "purpose": "qualification",
        "contract": str(contract_path.resolve()),
        "contract_sha256": sha256(contract_path),
        "reference_spinup_hours": reference,
        "candidate_spinup_hours": candidate_hours,
        "selection_rule": (
            "smallest candidate passing every case and height for which every "
            "longer candidate also passes"
        ),
        "thresholds": {
            "vector_rmse_m_s": 0.20,
            "relative_vector_rmse": 0.03,
            "absolute_speed_bias_m_s": 0.10,
            "direction_mae_degrees": 5.0,
            "direction_min_speed_m_s": 2.0,
            "vector_error_p99_m_s": 0.75,
        },
        "cases": [
            {"case_id": case_id, "target_start": start.strftime(TIME_FORMAT)}
            for case_id, start in cases
        ],
        "runs": runs,
        "preemptible_campaign_fragment": {
            "purpose": "experiment",
            "goal": {
                "outcome": "Determine the shortest wind spin-up that is stable across the selected regimes.",
                "why_now": "Spin-up uncertainty blocks a defensible wind-climatology workflow.",
                "evidence_needed": ["Convergence behavior across every declared case and height"],
                "stop_conditions": [
                    "Stop when convergence is bracketed or the longest member still fails"
                ],
                "resource_rationale": "Independent cases share forcing and run concurrently within the declared node budget.",
            },
            "model": {
                "expected_hicar_commit": contract["hicar"]["candidate_commit"],
                "output_profile": contract["production_output"]["profile"],
                "output_interval_seconds": cadence,
                "partition": "preemptible",
                "nodes": 4,
                "time_limit": "06:00:00",
            },
            "policy": {
                "segment_hours": 24,
                "model_node_budget": 46,
                "max_cpu_batch_tasks": 32,
                "input_task_weight": 3,
                "post_task_weight": 1,
                "rolling_retirement": True,
            },
            "chains": chains,
            "chain_independence": ("Each chain has its own initial state and restart trajectory."),
        },
    }
    if output.exists() or Path(f"{output}.ready").exists():
        raise ValueError(f"refusing to replace existing experiment: {output}")
    write_json_atomic(output, payload)
    Path(f"{output}.ready").touch()
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--contract",
        type=Path,
        default=Path(__file__).with_name("wind_production_candidate.json"),
    )
    parser.add_argument(
        "--case",
        action="append",
        required=True,
        help="Repeat CASE_ID=YYYY-MM-DDTHH:MM:SS for each frozen event.",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--static-root",
        type=Path,
        help=(
            "Bind each chain to the time-matched bridge static expected under "
            "this publication root."
        ),
    )
    args = parser.parse_args()
    cases = [parse_case(value) for value in args.case]
    payload = build_experiment(
        contract_path=args.contract.resolve(),
        cases=cases,
        output=args.output.resolve(),
        static_root=args.static_root.resolve() if args.static_root else None,
    )
    print(
        f"wind spin-up experiment planned: cases={len(payload['cases'])} "
        f"runs={len(payload['runs'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
