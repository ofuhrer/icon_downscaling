#!/usr/bin/env python3
"""Execute one globally throttled campaign CPU task on Balfrin."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path


def exec_bash(script: Path, environment: dict[str, str]) -> None:
    if not script.is_file():
        raise SystemExit(f"missing task script: {script}")
    merged = os.environ.copy()
    merged.update(environment)
    os.execve("/bin/bash", ["bash", str(script)], merged)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-file", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--index", type=int)
    parser.add_argument("--repo-root", type=Path, required=True)
    args = parser.parse_args()
    index = (
        args.index
        if args.index is not None
        else int(os.environ["SLURM_ARRAY_TASK_ID"])
    )
    content = args.task_file.read_bytes()
    if hashlib.sha256(content).hexdigest() != args.expected_sha256:
        raise SystemExit("campaign CPU task file checksum changed")
    payload = json.loads(content)
    if payload.get("schema_version") != 1:
        raise SystemExit("unsupported campaign CPU task schema")
    tasks = payload["tasks"]
    if not isinstance(tasks, list) or not tasks:
        raise SystemExit("campaign CPU task file has no tasks")
    if index < 0 or index >= len(tasks):
        raise SystemExit(f"task index {index} is outside 0..{len(tasks) - 1}")
    task = tasks[index]
    repo_root = args.repo_root.resolve()
    scripts = repo_root / "case_studies/swiss_200m/scripts"
    common = {"REPO_ROOT": str(repo_root)}
    kind = task["kind"]

    if kind == "forcing_record":
        exec_bash(
            scripts / "produce_rea_l_stream_record_balfrin.sbatch",
            {
                **common,
                "STREAM_PLAN": task["plan"],
                "STREAM_INDEX": str(task["record_index"]),
                "HICAR_FORCING_CASE": task["case_root"],
                "HICAR_STATIC_DOMAIN": task["static_file"],
            },
        )
    if kind == "forcing_finalize":
        exec_bash(
            scripts / "finalize_rea_l_stream_chunk_balfrin.sbatch",
            {**common, "STREAM_PLAN": task["plan"]},
        )
    if kind == "solver_audit":
        exec_bash(
            scripts / "validate_solver_event_balfrin.sbatch",
            {
                **common,
                "STREAM_PLAN": task["plan"],
                "EVENT_RUN_DIR": task["run_dir"],
            },
        )
    if kind == "compression":
        temporary_root = Path(
            os.environ.get("SLURM_TMPDIR", args.task_file.parent)
        )
        temporary_root.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(
            prefix="hicar-compression-source.",
            suffix=".txt",
            dir=temporary_root,
            text=True,
        )
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(f"{task['source']}\n")
        exec_bash(
            scripts / "compress_hicar_stream_output_balfrin.sbatch",
            {
                **common,
                "OUTPUT_FILE_LIST": name,
                "OUTPUT_INDEX": "0",
                "COMPRESSED_OUTPUT_DIR": task["target_dir"],
            },
        )
    raise SystemExit(f"unsupported campaign CPU task kind: {kind}")


if __name__ == "__main__":
    raise SystemExit(main())
