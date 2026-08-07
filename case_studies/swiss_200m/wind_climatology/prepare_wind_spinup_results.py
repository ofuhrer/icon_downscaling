#!/usr/bin/env python3
"""Bind completed campaign histories into a wind-spinup results publication."""

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


def require_published(path: Path, label: str) -> None:
    if not path.is_file() or not Path(f"{path}.ready").is_file():
        raise ValueError(f"{label} is not published: {path}")


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


def build_results(
    experiment_path: Path,
    completion_path: Path,
    results_path: Path,
) -> dict[str, Any]:
    require_published(experiment_path, "experiment manifest")
    require_published(completion_path, "campaign completion")
    experiment = json.loads(experiment_path.read_text())
    completion = json.loads(completion_path.read_text())
    if completion.get("status") != "PASS":
        raise ValueError("campaign completion is not PASS")
    expected = {run["run_id"] for run in experiment["runs"]}
    chains = {chain["chain_id"]: chain for chain in completion["chains"]}
    if set(chains) != expected:
        raise ValueError("campaign completion does not cover the exact experiment runs")

    runs = []
    for run in experiment["runs"]:
        chain = chains[run["run_id"]]
        paths = [
            Path(item["path"])
            for segment in chain["segments"]
            for item in segment["compressed"]
        ]
        if not paths:
            raise ValueError(f"{run['run_id']} has no compressed histories")
        for path in paths:
            require_published(path, "compressed history")
        runs.append(
            {
                "run_id": run["run_id"],
                "history_files": [str(path) for path in paths],
            }
        )

    payload = {
        "schema_version": 1,
        "status": "PASS",
        "experiment": str(experiment_path),
        "experiment_sha256": sha256(experiment_path),
        "campaign_completion": str(completion_path),
        "campaign_completion_sha256": sha256(completion_path),
        "runs": runs,
    }
    if results_path.exists() or Path(f"{results_path}.ready").exists():
        raise ValueError(f"refusing to replace existing results: {results_path}")
    write_json_atomic(results_path, payload)
    Path(f"{results_path}.ready").touch()
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--campaign-completion", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    args = parser.parse_args()
    payload = build_results(
        args.experiment.resolve(),
        args.campaign_completion.resolve(),
        args.results.resolve(),
    )
    print(f"wind-spinup results published: {len(payload['runs'])} runs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
