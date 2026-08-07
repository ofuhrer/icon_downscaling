#!/usr/bin/env python3
"""Publish an assessment manifest for a completed repeated-day experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_published_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or not Path(f"{path}.ready").is_file():
        raise ValueError(f"publication is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("status") != "PASS":
        raise ValueError(f"publication is not PASS: {path}")
    return payload


def prepare(
    cycle_root: Path,
    cycle_count: int,
    cycle_one_completion_path: Path,
    cycle_one_results_path: Path | None,
    cycle_one_run_id: str | None,
    experiment_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    experiment = json.loads(experiment_path.read_text(encoding="utf-8"))
    if experiment.get("status") not in {"RUNNING", "PASS"}:
        raise ValueError("experiment contract is not active")
    cycle_one_completion = read_published_json(cycle_one_completion_path)
    if cycle_one_results_path is not None:
        if cycle_one_run_id is None:
            raise ValueError("cycle-one run ID is required with an external result")
        results = read_published_json(cycle_one_results_path)
        matching = [
            item for item in results["runs"] if item["run_id"] == cycle_one_run_id
        ]
        if len(matching) != 1:
            raise ValueError(f"expected one cycle-one run, found {len(matching)}")
        cycle_one_history = matching[0]["history_files"]
    else:
        cycle_one_history = [
            item["path"] for item in cycle_one_completion["output"]["files"]
        ]

    cycles = [
        {
            "cycle": 1,
            "completion": str(cycle_one_completion_path.resolve()),
            "completion_sha256": sha256(cycle_one_completion_path),
            "history_files": cycle_one_history,
        }
    ]
    for cycle in range(2, cycle_count + 1):
        completion_path = (
            cycle_root
            / "cycles"
            / f"cycle-{cycle:03d}"
            / "run"
            / "model_chunk_completion.json"
        )
        completion = read_published_json(completion_path)
        cycles.append(
            {
                "cycle": cycle,
                "completion": str(completion_path.resolve()),
                "completion_sha256": sha256(completion_path),
                "history_files": [
                    item["path"] for item in completion["output"]["files"]
                ],
            }
        )

    payload = {
        "schema_version": 1,
        "status": "PASS",
        "experiment": str(experiment_path.resolve()),
        "experiment_sha256": sha256(experiment_path),
        "model_interval": experiment["model_interval"],
        "output_interval_seconds": int(
            cycle_one_completion["output_interval_seconds"]
        ),
        "equilibration_rule": experiment["equilibration_rule"],
        "cycles": cycles,
    }
    if output_path.exists() or Path(f"{output_path}.ready").exists():
        raise ValueError(f"refusing to replace assessment manifest: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=output_path.parent, delete=False
    ) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, output_path)
    Path(f"{output_path}.ready").touch()
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycle-root", type=Path, required=True)
    parser.add_argument("--cycle-count", type=int, required=True)
    parser.add_argument("--cycle-one-completion", type=Path, required=True)
    parser.add_argument("--cycle-one-results", type=Path)
    parser.add_argument("--cycle-one-run-id")
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = prepare(
        args.cycle_root.resolve(),
        args.cycle_count,
        args.cycle_one_completion.resolve(),
        args.cycle_one_results.resolve() if args.cycle_one_results else None,
        args.cycle_one_run_id,
        args.experiment.resolve(),
        args.output.resolve(),
    )
    print(f"repeated-day assessment manifest: cycles={len(payload['cycles'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
