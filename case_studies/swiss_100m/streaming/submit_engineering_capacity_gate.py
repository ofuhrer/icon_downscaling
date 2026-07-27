#!/usr/bin/env python3
"""Preview or submit the authorized national 100 m capacity/restart DAG."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import NamedTemporaryFile

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[2] / "swiss_200m" / "validation"),
)
from month_source_contract import (  # noqa: E402
    OUTPUT_DIAGNOSTIC_ONLY,
    require_published_source_qualification,
)


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_runtime_stack(
    jobs: list[dict],
    repo_root: Path,
    case_root: Path,
) -> list[dict[str, object]]:
    critical = {
        case_root / "scripts/run_engineering_capacity_segment_balfrin.sbatch": (
            "status --porcelain --untracked-files=no",
            "ls-files --others --exclude-standard",
            "does not match the frozen capacity-gate commit",
            "--source-commit-file",
            "--source-tree-status-file",
            "--archived-forcing-publication",
        ),
        repo_root
        / "case_studies/swiss_200m/streaming/validate_model_chunk.py": (
            "def validate_provenance(",
            '"schema_version": 2',
        ),
        case_root / "validation/assess_engineering_capacity_gate.py": (
            "production provenance is not PASS",
            "do not share one source, executable, and static identity",
            "capacity source qualification is not checksum-frozen",
        ),
        repo_root
        / "case_studies/swiss_200m/validation/month_source_contract.py": (
            "OUTPUT_DIAGNOSTIC_ONLY",
            "SCIENTIFIC_BASELINE_TRANSITION",
            "nonzero runoff",
        ),
    }
    critical = {path.resolve(): tokens for path, tokens in critical.items()}
    paths = {
        Path(spec["script"]).resolve()
        for spec in jobs
    } | {
        path.resolve() for path in critical
    } | {
        (case_root / "scripts/render_hicar_namelist.py").resolve(),
        Path(__file__).resolve(),
    }
    manifest = []
    for path in sorted(paths):
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"runtime-stack file is missing or empty: {path}")
        text = path.read_text(errors="replace")
        missing = [
            token for token in critical.get(path, ()) if token not in text
        ]
        if missing:
            raise ValueError(
                f"runtime-stack file is stale: {path}; missing {missing}"
            )
        manifest.append(
            {
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    return manifest


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def job(
    name: str,
    script: Path,
    exports: dict[str, str],
    dependencies: list[str] | None = None,
    array: str | None = None,
) -> dict:
    return {
        "name": name,
        "script": str(script),
        "exports": {key: str(value) for key, value in sorted(exports.items())},
        "dependencies": dependencies or [],
        "array": array,
    }


def build_jobs(
    plan: dict,
    plan_path: Path,
    repo_root: Path,
    case_root: Path,
    hicar_root: Path,
) -> list[dict]:
    scripts = {
        "forcing": repo_root
        / "case_studies/swiss_200m/scripts/produce_rea_l_stream_record_balfrin.sbatch",
        "finalize": repo_root
        / "case_studies/swiss_200m/scripts/finalize_rea_l_stream_chunk_balfrin.sbatch",
        "model": case_root
        / "scripts/run_engineering_capacity_segment_balfrin.sbatch",
        "solver": repo_root
        / "case_studies/swiss_200m/scripts/validate_solver_event_balfrin.sbatch",
        "boundary": case_root
        / "scripts/compare_capacity_restart_boundary_balfrin.sbatch",
        "assessor": case_root
        / "scripts/assess_engineering_capacity_gate_balfrin.sbatch",
    }
    missing = [str(path) for path in scripts.values() if not path.is_file()]
    if missing:
        raise ValueError(f"capacity scripts are missing: {missing}")
    static_file = plan["static_file"]
    common = {
        "REPO_ROOT": str(repo_root),
        "CAPACITY_GATE_PLAN": str(plan_path),
        "HICAR_SWISS_CASE": str(case_root),
        "HICAR_MULTILEVEL_ROOT": str(hicar_root),
        "HICAR_EXPECTED_COMMIT": plan["expected_hicar_commit"],
        "HICAR_STATIC_FILE": static_file,
    }
    jobs = []
    for segment in plan["segments"]:
        prefix = (
            "initial"
            if not segment["restart_continuation"]
            else "continuation"
        )
        forcing = job(
            f"{prefix}_forcing",
            scripts["forcing"],
            {
                "REPO_ROOT": str(repo_root),
                "STREAM_PLAN": segment["chunk_plan"],
                "HICAR_FORCING_CASE": str(case_root),
                "HICAR_STATIC_DOMAIN": static_file,
            },
            (
                ["afterok:initial_forcing"]
                if prefix == "continuation"
                else []
            ),
            array=f"0-{int(segment['forcing_record_count']) - 1}%3",
        )
        finalizer = job(
            f"{prefix}_forcing_finalize",
            scripts["finalize"],
            {
                "REPO_ROOT": str(repo_root),
                "STREAM_PLAN": segment["chunk_plan"],
            },
            [f"afterok:{forcing['name']}"],
        )
        dependencies = [f"afterok:{finalizer['name']}"]
        if prefix == "continuation":
            dependencies.append("afterok:initial_solver")
        model = job(
            f"{prefix}_model",
            scripts["model"],
            {
                **common,
                "CAPACITY_SEGMENT_ID": segment["id"],
            },
            dependencies,
        )
        solver = job(
            f"{prefix}_solver",
            scripts["solver"],
            {
                "REPO_ROOT": str(repo_root),
                "STREAM_PLAN": segment["chunk_plan"],
                "EVENT_RUN_DIR": segment["run_dir"],
            },
            [f"afterok:{model['name']}"],
        )
        jobs.extend([forcing, finalizer, model, solver])

    boundary = job(
        "restart_boundary",
        scripts["boundary"],
        {
            "REPO_ROOT": str(repo_root),
            "CAPACITY_GATE_PLAN": str(plan_path),
        },
        ["afterok:initial_model"],
    )
    jobs.append(boundary)
    assessor = job(
        "capacity_verdict",
        scripts["assessor"],
        {
            "REPO_ROOT": str(repo_root),
            "CAPACITY_GATE_PLAN": str(plan_path),
        },
        ["afterok:continuation_solver", "afterok:restart_boundary"],
    )
    jobs.append(assessor)
    return jobs


def dependency_argument(spec: dict, submitted: dict[str, str]) -> str | None:
    rendered = []
    for dependency in spec["dependencies"]:
        kind, name = dependency.split(":", 1)
        if name not in submitted:
            raise ValueError(f"dependency {name} has not been submitted")
        rendered.append(f"{kind}:{submitted[name]}")
    return ",".join(rendered) if rendered else None


def submit(spec: dict, submitted: dict[str, str]) -> str:
    command = ["sbatch", "--parsable"]
    dependency = dependency_argument(spec, submitted)
    if dependency:
        command.append(f"--dependency={dependency}")
    if spec["array"]:
        command.append(f"--array={spec['array']}")
    exports = dict(spec["exports"])
    if spec["name"] == "capacity_verdict":
        names = {
            "INITIAL_FORCING_JOB": "initial_forcing",
            "INITIAL_FORCING_FINALIZE_JOB": "initial_forcing_finalize",
            "INITIAL_MODEL_JOB": "initial_model",
            "INITIAL_SOLVER_JOB": "initial_solver",
            "CONTINUATION_FORCING_JOB": "continuation_forcing",
            "CONTINUATION_FORCING_FINALIZE_JOB": "continuation_forcing_finalize",
            "CONTINUATION_MODEL_JOB": "continuation_model",
            "CONTINUATION_SOLVER_JOB": "continuation_solver",
            "RESTART_BOUNDARY_JOB": "restart_boundary",
        }
        exports.update({key: submitted[name] for key, name in names.items()})
    command.append(
        "--export=ALL,"
        + ",".join(f"{key}={value}" for key, value in sorted(exports.items()))
    )
    command.append(spec["script"])
    result = subprocess.run(
        command,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip().split(";", 1)[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--case-root", type=Path, required=True)
    parser.add_argument("--hicar-root", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()

    if not args.plan.is_file() or not Path(f"{args.plan}.ready").is_file():
        raise SystemExit("capacity plan is not published")
    plan = load_json(args.plan)
    if plan.get("status") != "AUTHORIZED_AND_PLANNED":
        raise SystemExit("capacity plan is not authorized")
    for key in ("event_assessment", "geometry_report"):
        path = Path(plan[key])
        if not path.is_file() or not Path(f"{path}.ready").is_file():
            raise SystemExit(f"{key} is no longer published")
        if sha256(path) != plan[f"{key}_sha256"]:
            raise SystemExit(f"{key} checksum changed after planning")
    if sha256(Path(plan["static_file"])) != plan["static_sha256"]:
        raise SystemExit("100 m static checksum changed after planning")
    expected_commit = plan.get("expected_hicar_commit")
    if not expected_commit:
        raise SystemExit("capacity plan does not freeze an expected HICAR commit")
    source_qualification_path = Path(
        plan.get("source_qualification_report", "")
    )
    source_mode = (
        plan.get("source_qualification_mode") or OUTPUT_DIAGNOSTIC_ONLY
    )
    _, source_failures = require_published_source_qualification(
        source_qualification_path,
        expected_child_commit=expected_commit,
        required_parent_commit=plan.get("required_parent_hicar_commit"),
        qualification_mode=source_mode,
    )
    if source_failures:
        raise SystemExit(
            "capacity source qualification failed: "
            + "; ".join(source_failures)
        )
    if (
        not plan.get("source_qualification_sha256")
        or sha256(source_qualification_path)
        != plan["source_qualification_sha256"]
    ):
        raise SystemExit(
            "capacity source qualification changed after planning"
        )
    source_commit = subprocess.run(
        ["git", "-C", str(args.hicar_root.resolve()), "rev-parse", "HEAD"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.strip()
    if source_commit != expected_commit:
        raise SystemExit(
            f"HICAR source commit {source_commit} does not match the frozen "
            f"capacity-gate commit {expected_commit}"
        )

    jobs = build_jobs(
        plan,
        args.plan.resolve(),
        args.repo_root.resolve(),
        args.case_root.resolve(),
        args.hicar_root.resolve(),
    )
    runtime_stack = validate_runtime_stack(
        jobs,
        args.repo_root.resolve(),
        args.case_root.resolve(),
    )
    preview = {
        "status": "DRY_RUN",
        "plan": str(args.plan.resolve()),
        "runtime_stack": runtime_stack,
        "jobs": jobs,
    }
    if not args.execute:
        print(json.dumps(preview, indent=2, sort_keys=True))
        return 0

    receipt = (
        args.receipt
        or Path(plan["gate_root"]) / "capacity_gate_submission.json"
    ).resolve()
    if receipt.exists() or Path(f"{receipt}.ready").exists():
        raise SystemExit(f"submission receipt already exists: {receipt}")
    journal = Path(f"{receipt}.partial")
    if journal.exists():
        raise SystemExit(f"partial submission journal requires inspection: {journal}")

    submitted: dict[str, str] = {}
    try:
        for spec in jobs:
            submitted[spec["name"]] = submit(spec, submitted)
            write_json_atomic(
                journal,
                {
                    "status": "PARTIAL",
                    "plan": str(args.plan.resolve()),
                    "plan_sha256": sha256(args.plan),
                    "runtime_stack": runtime_stack,
                    "submitted": submitted,
                },
            )
    except Exception:
        raise
    payload = {
        "schema_version": 1,
        "status": "SUBMITTED",
        "plan": str(args.plan.resolve()),
        "plan_sha256": sha256(args.plan),
        "runtime_stack": runtime_stack,
        "jobs": submitted,
    }
    write_json_atomic(receipt, payload)
    Path(f"{receipt}.ready").touch()
    journal.unlink()
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
