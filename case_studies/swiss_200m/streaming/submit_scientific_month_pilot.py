#!/usr/bin/env python3
"""Submit or preview the gate-authorized scientific month-pilot Slurm DAG."""

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
    str(Path(__file__).resolve().parents[1] / "validation"),
)
from month_source_contract import (  # noqa: E402
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
        case_root / "scripts/run_rea_l_stream_chunk_balfrin.sbatch": (
            "status --porcelain --untracked-files=no",
            "ls-files --others --exclude-standard",
            "HICAR_EXPECTED_COMMIT",
            "--source-commit-file",
            "--source-tree-status-file",
            "--archived-forcing-publication",
        ),
        repo_root
        / "case_studies/swiss_200m/streaming/validate_model_chunk.py": (
            "def validate_provenance(",
            '"schema_version": 2',
        ),
        repo_root
        / "case_studies/swiss_200m/validation/assess_scientific_month.py": (
            "production_provenance",
            "consistent_model_identity",
            "frozen_hicar_source_commit",
            "production_water_budget_observables",
            "cumulative_water_restart_continuity",
        ),
        repo_root
        / "case_studies/swiss_200m/validation/evaluate_scientific_event.py": (
            "production_cumulative",
            "evaporation_net_cumulative",
            "production_eligible",
        ),
        repo_root
        / "case_studies/swiss_200m/validation/month_source_contract.py": (
            "OUTPUT_DIAGNOSTIC_ONLY",
            "nonzero_runoff_observed",
            "pre-existing field equivalence is not exact",
        ),
    }
    critical = {path.resolve(): tokens for path, tokens in critical.items()}
    paths = {
        Path(spec["script"]).resolve()
        for spec in jobs
    } | set(critical) | {
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


def job(
    name: str,
    script: Path,
    exports: dict[str, str],
    dependencies: list[dict] | None = None,
    array: str | None = None,
) -> dict:
    return {
        "name": name,
        "script": str(script),
        "exports": {key: str(value) for key, value in sorted(exports.items())},
        "dependencies": dependencies or [],
        "array": array,
    }


def segment_jobs(
    segment: dict,
    sequence: int,
    scripts: dict[str, Path],
    repo_root: Path,
    case_root: Path,
    hicar_root: Path,
    static_file: Path,
    producer_dependency: list[dict],
    model_dependencies: list[dict],
) -> list[dict]:
    prefix = f"segment_{sequence:02d}"
    common = {
        "REPO_ROOT": str(repo_root),
        "STREAM_PLAN": segment["chunk_plan"],
    }
    producer = job(
        f"{prefix}_forcing",
        scripts["producer"],
        {
            **common,
            "HICAR_FORCING_CASE": str(case_root),
            "HICAR_STATIC_DOMAIN": str(static_file),
        },
        producer_dependency,
        (
            f"0-{int(segment['forcing_record_count']) - 1}"
            f"%{int(load_json(Path(segment['chunk_plan']))['producer_concurrency'])}"
        ),
    )
    finalizer = job(
        f"{prefix}_forcing_finalize",
        scripts["finalizer"],
        common,
        [{"kind": "afterok", "job": producer["name"]}],
    )
    model_exports = {
        **common,
        "HICAR_MULTILEVEL_ROOT": str(hicar_root),
        "HICAR_STATIC_FILE": str(static_file),
        "STREAM_OUTPUT_INTERVAL": str(segment["output_interval_seconds"]),
        "STREAM_OUTPUT_PROFILE": segment["output_profile"],
        "STREAM_REA_L_LAND_INITIALIZATION": (
            "1" if segment["rea_l_land_initialization"] else "0"
        ),
        "STREAM_RESTART_DIR": segment["shared_restart_dir"],
        "STREAM_RUN_DIR": segment["run_dir"],
    }
    if segment["restart_from"]:
        model_exports["STREAM_RESTART_FROM"] = segment["restart_from"]
    model = job(
        f"{prefix}_model",
        scripts["model"],
        model_exports,
        [
            {"kind": "afterok", "job": finalizer["name"]},
            *model_dependencies,
        ],
    )
    solver = job(
        f"{prefix}_solver_audit",
        scripts["solver"],
        {
            "EVENT_RUN_DIR": segment["run_dir"],
            "REPO_ROOT": str(repo_root),
            "STREAM_PLAN": segment["chunk_plan"],
        },
        [{"kind": "afterok", "job": model["name"]}],
    )
    compression = job(
        f"{prefix}_compression",
        scripts["compressor"],
        {
            "COMPRESSED_OUTPUT_DIR": segment["compressed_output_dir"],
            "OUTPUT_FILE_LIST": segment["output_file_list"],
            "OUTPUT_INDEX": "0",
            "REPO_ROOT": str(repo_root),
        },
        [{"kind": "afterok", "job": model["name"]}],
    )
    retirement = job(
        f"{prefix}_forcing_retirement",
        scripts["forcing_retirer"],
        {
            **common,
            "FORCING_RETIREMENT_REPORT": segment["forcing_retirement_report"],
            "STREAM_RUN_DIR": segment["run_dir"],
        },
        [{"kind": "afterok", "job": model["name"]}],
    )
    return [producer, finalizer, model, solver, compression, retirement]


def restart_retirement_job(
    previous: dict,
    successor: dict,
    sequence: int,
    scripts: dict[str, Path],
    repo_root: Path,
    dependencies: list[dict],
) -> dict:
    return job(
        f"segment_{sequence:02d}_restart_retirement",
        scripts["restart_retirer"],
        {
            "NEXT_MODEL_COMPLETION": successor["model_completion_report"],
            "PREVIOUS_MODEL_COMPLETION": previous["model_completion_report"],
            "REPO_ROOT": str(repo_root),
            "RESTART_RETIREMENT_REPORT": previous[
                "restart_retirement_report"
            ],
        },
        dependencies,
    )


def build_job_specs(
    plan: dict,
    repo_root: Path,
    case_root: Path,
    hicar_root: Path,
) -> list[dict]:
    scripts = {
        "producer": (
            repo_root
            / "case_studies/swiss_200m/scripts/produce_rea_l_stream_record_balfrin.sbatch"
        ),
        "finalizer": (
            repo_root
            / "case_studies/swiss_200m/scripts/finalize_rea_l_stream_chunk_balfrin.sbatch"
        ),
        "model": (
            repo_root
            / "case_studies/swiss_200m/scripts/run_rea_l_stream_chunk_balfrin.sbatch"
        ),
        "forcing_retirer": (
            repo_root
            / "case_studies/swiss_200m/scripts/retire_rea_l_forcing_chunk_balfrin.sbatch"
        ),
        "restart_retirer": (
            repo_root
            / "case_studies/swiss_200m/scripts/retire_rea_l_restart_boundary_balfrin.sbatch"
        ),
        "comparator": (
            repo_root
            / "case_studies/swiss_200m/scripts/compare_month_restart_trajectory_balfrin.sbatch"
        ),
        "compressor": (
            repo_root
            / "case_studies/swiss_200m/scripts/compress_hicar_stream_output_balfrin.sbatch"
        ),
        "reference_producer": (
            repo_root
            / "case_studies/swiss_200m/scripts/produce_rea_l_event_reference_balfrin.sbatch"
        ),
        "reference_finalizer": (
            repo_root
            / "case_studies/swiss_200m/scripts/finalize_rea_l_event_reference_balfrin.sbatch"
        ),
        "observations": (
            repo_root
            / "case_studies/swiss_200m/scripts/retrieve_smn_event_observations_balfrin.sbatch"
        ),
        "solver": (
            repo_root
            / "case_studies/swiss_200m/scripts/validate_solver_event_balfrin.sbatch"
        ),
        "month_validator": (
            repo_root
            / "case_studies/swiss_200m/scripts/validate_scientific_month_balfrin.sbatch"
        ),
        "drift": (
            repo_root
            / "case_studies/swiss_200m/scripts/screen_scientific_month_drift_balfrin.sbatch"
        ),
        "assessor": (
            repo_root
            / "case_studies/swiss_200m/scripts/assess_scientific_month_balfrin.sbatch"
        ),
    }
    static_file = Path(plan["static_file"])
    segments = plan["segments"]
    if len(segments) != 5:
        raise ValueError("the frozen month pilot must contain five segments")
    validation = plan["validation_sources"]
    validation_common = {
        "REPO_ROOT": str(repo_root),
        "STREAM_PLAN": validation["chunk_plan"],
    }
    reference_producer = job(
        "month_rea_l_reference",
        scripts["reference_producer"],
        validation_common,
        array=(
            f"0-{int(validation['expected_reference_record_count']) - 1}"
            f"%{int(load_json(Path(validation['chunk_plan']))['producer_concurrency'])}"
        ),
    )
    reference_finalizer = job(
        "month_rea_l_reference_finalize",
        scripts["reference_finalizer"],
        validation_common,
        [{"kind": "afterok", "job": reference_producer["name"]}],
    )
    observations = job(
        "month_swissmetnet_observations",
        scripts["observations"],
        validation_common,
    )
    jobs = [reference_producer, reference_finalizer, observations]

    first = segment_jobs(
        segments[0],
        1,
        scripts,
        repo_root,
        case_root,
        hicar_root,
        static_file,
        [],
        [],
    )
    jobs.extend(first)
    previous_model = first[2]["name"]
    previous_solver = first[3]["name"]

    second = segment_jobs(
        segments[1],
        2,
        scripts,
        repo_root,
        case_root,
        hicar_root,
        static_file,
        [{"kind": "after", "job": previous_model, "delay_minutes": 10}],
        [{"kind": "afterok", "job": previous_solver}],
    )
    jobs.extend(second)
    second_model = second[2]["name"]
    second_solver = second[3]["name"]

    overlap = plan["uninterrupted_restart_overlap"]
    overlap_common = {
        "REPO_ROOT": str(repo_root),
        "STREAM_PLAN": overlap["chunk_plan"],
    }
    overlap_forcing = job(
        "restart_overlap_forcing",
        scripts["producer"],
        {
            **overlap_common,
            "HICAR_FORCING_CASE": str(case_root),
            "HICAR_STATIC_DOMAIN": str(static_file),
        },
        [{"kind": "after", "job": previous_model, "delay_minutes": 10}],
        (
            f"0-{int(overlap['forcing_record_count']) - 1}"
            f"%{int(load_json(Path(overlap['chunk_plan']))['producer_concurrency'])}"
        ),
    )
    overlap_finalize = job(
        "restart_overlap_forcing_finalize",
        scripts["finalizer"],
        overlap_common,
        [{"kind": "afterok", "job": overlap_forcing["name"]}],
    )
    overlap_model = job(
        "restart_overlap_model",
        scripts["model"],
        {
            **overlap_common,
            "HICAR_MULTILEVEL_ROOT": str(hicar_root),
            "HICAR_STATIC_FILE": str(static_file),
            "STREAM_OUTPUT_INTERVAL": str(overlap["output_interval_seconds"]),
            "STREAM_OUTPUT_PROFILE": overlap["output_profile"],
            "STREAM_REA_L_LAND_INITIALIZATION": "0",
            "STREAM_RESTART_DIR": overlap["shared_restart_dir"],
            "STREAM_RESTART_FROM": overlap["restart_from"],
            "STREAM_RUN_DIR": overlap["run_dir"],
        },
        [
            {"kind": "afterok", "job": overlap_finalize["name"]},
            {"kind": "afterok", "job": second_solver},
        ],
    )
    overlap_solver = job(
        "restart_overlap_solver_audit",
        scripts["solver"],
        {
            "EVENT_RUN_DIR": overlap["run_dir"],
            "REPO_ROOT": str(repo_root),
            "STREAM_PLAN": overlap["chunk_plan"],
        },
        [{"kind": "afterok", "job": overlap_model["name"]}],
    )
    overlap_compression = job(
        "restart_overlap_compression",
        scripts["compressor"],
        {
            "COMPRESSED_OUTPUT_DIR": overlap["compressed_output_dir"],
            "OUTPUT_FILE_LIST": overlap["output_file_list"],
            "OUTPUT_INDEX": "0",
            "REPO_ROOT": str(repo_root),
        },
        [{"kind": "afterok", "job": overlap_model["name"]}],
    )
    overlap_retirement = job(
        "restart_overlap_forcing_retirement",
        scripts["forcing_retirer"],
        {
            **overlap_common,
            "FORCING_RETIREMENT_REPORT": overlap["forcing_retirement_report"],
            "STREAM_RUN_DIR": overlap["run_dir"],
        },
        [{"kind": "afterok", "job": overlap_model["name"]}],
    )
    jobs.extend(
        [
            overlap_forcing,
            overlap_finalize,
            overlap_model,
            overlap_solver,
            overlap_compression,
            overlap_retirement,
        ]
    )

    third = segment_jobs(
        segments[2],
        3,
        scripts,
        repo_root,
        case_root,
        hicar_root,
        static_file,
        [{"kind": "after", "job": second_model, "delay_minutes": 10}],
        [
            {"kind": "afterok", "job": second_solver},
            {"kind": "afterok", "job": overlap_solver["name"]},
        ],
    )
    jobs.extend(third)
    third_solver = third[3]["name"]

    comparison = job(
        "restart_trajectory_comparison",
        scripts["comparator"],
        {
            "MONTH_PILOT_PLAN": plan["_plan_path"],
            "REPO_ROOT": str(repo_root),
            "SCIENTIFIC_PILOT_PLAN": plan["scientific_plan"],
        },
        [
            {"kind": "afterok", "job": third_solver},
            {"kind": "afterok", "job": overlap_solver["name"]},
        ],
    )
    jobs.append(comparison)
    first_restart_retirement = restart_retirement_job(
        segments[0],
        segments[1],
        1,
        scripts,
        repo_root,
        [{"kind": "afterok", "job": comparison["name"]}],
    )
    second_restart_retirement = restart_retirement_job(
        segments[1],
        segments[2],
        2,
        scripts,
        repo_root,
        [{"kind": "afterok", "job": first_restart_retirement["name"]}],
    )
    jobs.extend([first_restart_retirement, second_restart_retirement])

    fourth = segment_jobs(
        segments[3],
        4,
        scripts,
        repo_root,
        case_root,
        hicar_root,
        static_file,
        [{"kind": "afterok", "job": comparison["name"]}],
        [
            {"kind": "afterok", "job": third_solver},
            {"kind": "afterok", "job": comparison["name"]},
        ],
    )
    jobs.extend(fourth)
    fourth_model = fourth[2]["name"]
    fourth_solver = fourth[3]["name"]
    third_restart_retirement = restart_retirement_job(
        segments[2],
        segments[3],
        3,
        scripts,
        repo_root,
        [
            {"kind": "afterok", "job": fourth_solver},
            {"kind": "afterok", "job": second_restart_retirement["name"]},
        ],
    )
    jobs.append(third_restart_retirement)

    fifth = segment_jobs(
        segments[4],
        5,
        scripts,
        repo_root,
        case_root,
        hicar_root,
        static_file,
        [{"kind": "after", "job": fourth_model, "delay_minutes": 10}],
        [{"kind": "afterok", "job": fourth_solver}],
    )
    jobs.extend(fifth)
    fifth_model = fifth[2]["name"]
    fifth_solver = fifth[3]["name"]
    fourth_restart_retirement = restart_retirement_job(
        segments[3],
        segments[4],
        4,
        scripts,
        repo_root,
        [
            {"kind": "afterok", "job": fifth_solver},
            {"kind": "afterok", "job": third_restart_retirement["name"]},
        ],
    )
    jobs.append(fourth_restart_retirement)

    validator_jobs = {}
    for kind in ("physical", "rea_l_source", "swissmetnet", "ogd_grid"):
        dependencies = [{"kind": "afterok", "job": fifth_model}]
        if kind in {"rea_l_source", "swissmetnet", "ogd_grid"}:
            dependencies.append(
                {"kind": "afterok", "job": reference_finalizer["name"]}
            )
        if kind == "swissmetnet":
            dependencies.append(
                {"kind": "afterok", "job": observations["name"]}
            )
        validator = job(
            f"month_validate_{kind}",
            scripts["month_validator"],
            {
                "MONTH_PILOT_PLAN": plan["_plan_path"],
                "MONTH_VALIDATION_KIND": kind,
                "REPO_ROOT": str(repo_root),
            },
            dependencies,
        )
        validator_jobs[kind] = validator
        jobs.append(validator)

    drift = job(
        "month_drift_screen",
        scripts["drift"],
        {
            "MONTH_PILOT_PLAN": plan["_plan_path"],
            "REPO_ROOT": str(repo_root),
        },
        [
            {
                "kind": "afterok",
                "job": validator_jobs["physical"]["name"],
            }
        ],
    )
    jobs.append(drift)

    evidence_jobs = [
        comparison["name"],
        drift["name"],
        *(validator_jobs[kind]["name"] for kind in validator_jobs),
    ]
    for sequence in range(1, 6):
        evidence_jobs.extend(
            [
                f"segment_{sequence:02d}_solver_audit",
                f"segment_{sequence:02d}_compression",
                f"segment_{sequence:02d}_forcing_retirement",
            ]
        )
    evidence_jobs.extend(
        [
            "restart_overlap_solver_audit",
            "restart_overlap_compression",
            "restart_overlap_forcing_retirement",
            *(
                f"segment_{sequence:02d}_restart_retirement"
                for sequence in range(1, 5)
            ),
        ]
    )
    assessor = job(
        "month_assessment",
        scripts["assessor"],
        {
            "MONTH_PILOT_PLAN": plan["_plan_path"],
            "REPO_ROOT": str(repo_root),
        },
        [{"kind": "afterok", "job": name} for name in evidence_jobs],
    )
    jobs.append(assessor)
    expected_hicar_commit = plan.get("expected_hicar_commit")
    if not expected_hicar_commit:
        raise ValueError("month plan does not freeze an expected HICAR commit")
    for spec in jobs:
        if spec["name"].endswith("_model"):
            spec["exports"]["HICAR_EXPECTED_COMMIT"] = expected_hicar_commit
    return jobs


def dependency_argument(
    dependencies: list[dict], submitted: dict[str, str]
) -> str | None:
    values = []
    for dependency in dependencies:
        job_id = submitted[dependency["job"]]
        if dependency["kind"] == "after":
            values.append(f"after:{job_id}+{int(dependency.get('delay_minutes', 0))}")
        elif dependency["kind"] == "afterok":
            values.append(f"afterok:{job_id}")
        else:
            raise ValueError(f"unsupported dependency kind: {dependency}")
    return ",".join(values) if values else None


def sbatch_arguments(
    spec: dict,
    submitted: dict[str, str],
) -> list[str]:
    arguments = ["sbatch", "--parsable"]
    dependency = dependency_argument(spec["dependencies"], submitted)
    if dependency:
        arguments.append(f"--dependency={dependency}")
    if spec["array"]:
        arguments.append(f"--array={spec['array']}")
    exports = ",".join(f"{key}={value}" for key, value in spec["exports"].items())
    arguments.append(f"--export=ALL,{exports}")
    arguments.append(spec["script"])
    return arguments


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month-plan", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--case-root", type=Path, required=True)
    parser.add_argument("--hicar-root", type=Path, required=True)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    if not args.month_plan.is_file() or not Path(f"{args.month_plan}.ready").is_file():
        raise SystemExit("month plan is not published")
    plan = load_json(args.month_plan)
    if plan.get("status") != "PLANNED":
        raise SystemExit("month plan status is not PLANNED")
    if (
        plan.get("authorization", {}).get("decision")
        != "GO_MONTH_AND_100M_CAPACITY_GATE"
    ):
        raise SystemExit("month plan lacks a passing event authorization")
    expected_commit = plan.get("expected_hicar_commit")
    if not expected_commit:
        raise SystemExit("month plan does not freeze an expected HICAR commit")
    source_qualification_path = Path(plan.get("source_qualification_report", ""))
    source_qualification, source_failures = (
        require_published_source_qualification(
            source_qualification_path,
            expected_child_commit=expected_commit,
            required_parent_commit=plan.get("required_parent_hicar_commit"),
        )
    )
    if source_failures:
        raise SystemExit(
            "month source qualification failed: " + "; ".join(source_failures)
        )
    frozen_source_qualification_sha256 = plan.get(
        "source_qualification_sha256"
    )
    if (
        not frozen_source_qualification_sha256
        or sha256(source_qualification_path)
        != frozen_source_qualification_sha256
    ):
        raise SystemExit(
            "month source qualification is not checksum-frozen by the month plan"
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
            f"month-pilot commit {expected_commit}"
        )
    assessment = Path(plan["authorization"]["event_assessment"])
    if (
        not assessment.is_file()
        or not Path(f"{assessment}.ready").is_file()
        or sha256(assessment) != plan["authorization"]["event_assessment_sha256"]
    ):
        raise SystemExit("event authorization publication changed or is missing")
    plan["_plan_path"] = str(args.month_plan.resolve())
    jobs = build_job_specs(
        plan,
        args.repo_root.resolve(),
        args.case_root.resolve(),
        args.hicar_root.resolve(),
    )
    for spec in jobs:
        if not Path(spec["script"]).is_file():
            raise SystemExit(f"Slurm script is missing: {spec['script']}")
    runtime_stack = validate_runtime_stack(
        jobs,
        args.repo_root.resolve(),
        args.case_root.resolve(),
    )

    if not args.execute:
        print(
            json.dumps(
                {
                    "status": "DRY_RUN",
                    "month_plan": str(args.month_plan.resolve()),
                    "source_qualification": {
                        "path": str(source_qualification_path.resolve()),
                        "sha256": frozen_source_qualification_sha256,
                        "child_commit": source_qualification["child_commit"],
                        "parent_commit": source_qualification["parent_commit"],
                    },
                    "job_count": len(jobs),
                    "runtime_stack": runtime_stack,
                    "jobs": jobs,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    receipt = (
        args.receipt or args.month_plan.parent / "month_pilot_submission.json"
    ).resolve()
    partial = Path(f"{receipt}.partial")
    if receipt.exists() or Path(f"{receipt}.ready").exists() or partial.exists():
        raise SystemExit("submission receipt or partial journal already exists")
    submitted: dict[str, str] = {}
    journal = {
        "schema_version": 1,
        "status": "SUBMITTING",
        "month_plan": str(args.month_plan.resolve()),
        "month_plan_sha256": sha256(args.month_plan),
        "source_qualification": {
            "path": str(source_qualification_path.resolve()),
            "sha256": frozen_source_qualification_sha256,
            "child_commit": source_qualification["child_commit"],
            "parent_commit": source_qualification["parent_commit"],
        },
        "runtime_stack": runtime_stack,
        "jobs": [],
    }
    for spec in jobs:
        arguments = sbatch_arguments(spec, submitted)
        result = subprocess.run(
            arguments,
            check=True,
            text=True,
            capture_output=True,
        )
        job_id = result.stdout.strip().split(";", 1)[0]
        if not job_id.isdigit():
            raise SystemExit(f"unexpected sbatch response: {result.stdout!r}")
        submitted[spec["name"]] = job_id
        journal["jobs"].append(
            {
                **spec,
                "job_id": job_id,
                "sbatch_arguments": arguments,
            }
        )
        write_json_atomic(partial, journal)
    journal["status"] = "SUBMITTED"
    write_json_atomic(receipt, journal)
    partial.unlink()
    Path(f"{receipt}.ready").touch()
    print(
        f"month pilot submitted: jobs={len(jobs)} "
        f"final={submitted['month_assessment']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
