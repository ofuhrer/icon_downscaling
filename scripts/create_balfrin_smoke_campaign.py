#!/usr/bin/env python3
"""Create a bounded Swiss 200 m pre-emptible qualification definition."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestration"))
from runtime_contract import (  # noqa: E402
    validate_python_environment,
    validate_runtime_release,
)


TIME_FORMAT = "%Y-%m-%dT%H:%M:%S"


def selected_site_config(default: Path) -> Path:
    """Return the operator-selected site record, or the repository default."""
    return Path(os.environ.get("HICAR_SITE_CONFIG", default))


def sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def require_publication(path: Path, label: str) -> Path:
    path = path.resolve()
    if not path.is_file() or not Path(f"{path}.ready").is_file():
        raise ValueError(f"{label} is not published: {path}")
    return path


def verify_build(
    hicar_root: Path,
    build_root: Path,
    expected_commit: str,
    expected_builder_sha256: str,
) -> None:
    hicar_root = hicar_root.resolve()
    build_root = build_root.resolve()
    commit = subprocess.check_output(
        ["git", "-C", str(hicar_root), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    if commit != expected_commit:
        raise ValueError(f"HICAR checkout is {commit}; expected production pin {expected_commit}")
    tracked = subprocess.check_output(
        [
            "git",
            "-C",
            str(hicar_root),
            "status",
            "--porcelain",
            "--untracked-files=no",
        ],
        text=True,
    ).strip()
    untracked_inputs = subprocess.check_output(
        [
            "git",
            "-C",
            str(hicar_root),
            "ls-files",
            "--others",
            "--exclude-standard",
            "--",
            "src",
            "cmake",
            "external",
            "tools",
            "CMakeLists.txt",
            "CMakePresets.json",
        ],
        text=True,
    ).strip()
    status = "\n".join(item for item in (tracked, untracked_inputs) if item)
    if status:
        raise ValueError("HICAR checkout is not clean")
    executable = build_root / "HICAR_gpu"
    provenance = require_publication(
        build_root / "hicar_build_provenance.txt",
        "HICAR build provenance",
    )
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError(f"HICAR GPU executable is missing: {executable}")
    content = provenance.read_text()
    required_lines = (
        f"source_commit={expected_commit}",
        "variant=gpu-nccl",
        expected_builder_sha256,
        f"executable={executable}",
        f"{sha256(executable)}  {executable}",
    )
    missing = [line for line in required_lines if line not in content]
    if missing:
        raise ValueError(
            "HICAR build provenance does not match the requested run: " + "; ".join(missing)
        )


def definition_payload(
    *,
    campaign_id: str,
    campaign_root: Path,
    runtime_manifest: Path,
    python_report: Path,
    hicar_root: Path,
    build_root: Path,
    static_file: Path,
    expected_commit: str,
    start: datetime,
    hours: int,
    segment_hours: int | None = None,
    output_profile: str = "qualification",
) -> dict[str, Any]:
    end = start + timedelta(hours=hours)
    segment_hours = hours if segment_hours is None else segment_hours
    if hours % segment_hours:
        raise ValueError("smoke duration must be divisible by segment duration")
    release_root = runtime_manifest.resolve().parent
    return {
        "schema_version": 1,
        "purpose": "qualification",
        "campaign_id": campaign_id,
        "campaign_root": str(campaign_root.resolve()),
        "runtime_release": str(runtime_manifest.resolve()),
        "python_environment": str(python_report.resolve()),
        "goal": {
            "outcome": "Verify that the current runtime can complete and publish a short HICAR segment.",
            "why_now": "This is the smallest representative check before committing more Balfrin resources.",
            "evidence_needed": [
                "A validated model completion and exact-end restart",
                "Clean solver, compression, and retirement reports",
            ],
            "stop_conditions": [
                "Stop after the planned smoke segments complete",
                "Reassess on any deterministic model or validation failure",
            ],
            "resource_rationale": "One four-node model slot and one CPU worker are sufficient for this bounded check.",
        },
        "model": {
            "expected_hicar_commit": expected_commit,
            "case_root": str(release_root / "case_studies/swiss_200m"),
            "hicar_root": str(hicar_root.resolve()),
            "build_root": str(build_root.resolve()),
            "static_file": str(static_file.resolve()),
            "nodes": 4,
            "time_limit": "01:00:00",
            "output_profile": output_profile,
            "output_interval_seconds": 3600,
        },
        "policy": {
            "segment_hours": segment_hours,
            "model_node_budget": 4,
            "cpu_slots": 1,
            "prefetch_segments_per_chain": 1,
            "max_model_attempts": 0,
            "max_cpu_attempts": 3,
            "rolling_retirement": True,
            "preserve_restart_every_segments": 0,
            "max_unretired_segments_per_chain": 1,
        },
        "chains": [
            {
                "chain_id": "smoke",
                "start": start.strftime(TIME_FORMAT),
                "end": end.strftime(TIME_FORMAT),
                "rea_l_land_initialization": True,
            }
        ],
    }


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path = path.resolve()
    if path.exists():
        existing = json.loads(path.read_text())
        if existing != payload:
            raise ValueError(f"refusing to replace campaign definition: {path}")
        return
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-id", default="swiss-200m-smoke")
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--runtime-manifest", type=Path, required=True)
    parser.add_argument("--python-report", type=Path, required=True)
    parser.add_argument("--hicar-root", type=Path, required=True)
    parser.add_argument("--build-root", type=Path, required=True)
    parser.add_argument("--static-file", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--hours", type=int, default=2)
    parser.add_argument(
        "--output-profile",
        choices=("routine", "qualification"),
        default="qualification",
    )
    parser.add_argument(
        "--segment-hours",
        type=int,
        help="Restart-linked segment length; defaults to the full smoke duration.",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--site-config",
        type=Path,
        default=selected_site_config(ROOT / "config/balfrin.env"),
    )
    args = parser.parse_args()

    if not args.campaign_id.replace("-", "").replace("_", "").isalnum():
        raise SystemExit("campaign ID may contain only letters, numbers, - and _")
    if not 1 <= args.hours <= 24:
        raise SystemExit("--hours must be within 1..24")
    segment_hours = args.hours if args.segment_hours is None else args.segment_hours
    if not 1 <= segment_hours <= args.hours or args.hours % segment_hours:
        raise SystemExit("--segment-hours must divide --hours and be within 1..hours")
    try:
        start = datetime.strptime(args.start, TIME_FORMAT)
    except ValueError as exc:
        raise SystemExit(f"--start must use {TIME_FORMAT}: {exc}") from exc
    site = {}
    for raw in args.site_config.read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            site[key] = os.environ.get(key, value)
    expected_commit = site["HICAR_PRODUCTION_COMMIT"]

    runtime_manifest = require_publication(
        args.runtime_manifest,
        "runtime release",
    )
    runtime = validate_runtime_release(
        runtime_manifest,
        expected_root=runtime_manifest.parent,
    )
    if runtime["source_dirty"]:
        raise ValueError("qualification smoke requires a clean runtime release")
    builder_record = next(
        item
        for item in runtime["files"]
        if item["path"] == "case_studies/swiss_200m/scripts/build_hicar_balfrin.sbatch"
    )
    python_report = require_publication(
        args.python_report,
        "Python environment",
    )
    validate_python_environment(
        python_report,
        runtime_manifest,
        smoke=True,
    )
    static_file = require_publication(args.static_file, "static domain")
    verify_build(
        args.hicar_root,
        args.build_root,
        expected_commit,
        builder_record["sha256"],
    )

    payload = definition_payload(
        campaign_id=args.campaign_id,
        campaign_root=args.campaign_root,
        runtime_manifest=runtime_manifest,
        python_report=python_report,
        hicar_root=args.hicar_root,
        build_root=args.build_root,
        static_file=static_file,
        expected_commit=expected_commit,
        start=start,
        hours=args.hours,
        segment_hours=segment_hours,
        output_profile=args.output_profile,
    )
    write_json_atomic(args.output, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
