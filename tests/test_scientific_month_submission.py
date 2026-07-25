from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "streaming"
    / "submit_scientific_month_pilot.py"
)
SPEC = importlib.util.spec_from_file_location("month_submitter", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def chunk(tmp_path: Path, name: str, sequence: int):
    plan = tmp_path / name / "chunk_plan.json"
    plan.parent.mkdir(parents=True)
    plan.write_text('{"producer_concurrency": 4}')
    return {
        "chunk_id": name,
        "chunk_plan": str(plan),
        "forcing_record_count": 169 if sequence < 5 else 73,
        "run_dir": str(tmp_path / "runs" / name),
        "shared_restart_dir": str(tmp_path / "runs" / "restart"),
        "restart_from": None if sequence == 1 else f"2020-07-{sequence:02d}T00:00:00",
        "rea_l_land_initialization": sequence == 1,
        "output_profile": "qualification",
        "output_interval_seconds": 10800,
        "output_file_list": str(tmp_path / name / "output_file_list.txt"),
        "compressed_output_dir": str(tmp_path / "runs" / name / "compressed"),
        "model_completion_report": str(
            tmp_path / "runs" / name / "model_chunk_completion.json"
        ),
        "forcing_retirement_report": str(
            tmp_path / "runs" / name / "forcing_retirement.json"
        ),
        "restart_retirement_report": str(
            tmp_path / "runs" / name / "restart_retirement.json"
        ),
    }


def test_submission_dag_overlaps_forcing_and_gates_late_month(tmp_path):
    segments = [chunk(tmp_path, f"segment_{index}", index) for index in range(1, 6)]
    overlap = chunk(tmp_path, "overlap", 2)
    overlap["forcing_record_count"] = 193
    overlap["restart_from"] = "2020-07-08T00:00:00"
    overlap["rea_l_land_initialization"] = False
    plan = {
        "_plan_path": str(tmp_path / "month_pilot_plan.json"),
        "expected_hicar_commit": "2ea31109801a2477a946840693934318f8d50c95",
        "static_file": str(tmp_path / "static.nc"),
        "scientific_plan": str(tmp_path / "scientific_plan.json"),
        "segments": segments,
        "uninterrupted_restart_overlap": overlap,
        "validation_sources": {
            **chunk(tmp_path, "validation", 1),
            "expected_reference_record_count": 249,
        },
    }

    jobs = MODULE.build_job_specs(
        plan,
        tmp_path / "repo",
        tmp_path / "case",
        tmp_path / "hicar",
    )
    by_name = {item["name"]: item for item in jobs}

    assert len(jobs) == 50
    assert by_name["month_rea_l_reference"]["array"] == "0-248%4"
    assert by_name["month_swissmetnet_observations"]["dependencies"] == []
    assert by_name["segment_01_forcing"]["dependencies"] == []
    assert by_name["segment_02_forcing"]["dependencies"] == [
        {
            "kind": "after",
            "job": "segment_01_model",
            "delay_minutes": 10,
        }
    ]
    assert {item["job"] for item in by_name["segment_03_model"]["dependencies"]} == {
        "segment_03_forcing_finalize",
        "segment_02_solver_audit",
        "restart_overlap_solver_audit",
    }
    assert {item["job"] for item in by_name["segment_04_model"]["dependencies"]} == {
        "segment_04_forcing_finalize",
        "segment_03_solver_audit",
        "restart_trajectory_comparison",
    }
    assert by_name["segment_05_model"]["exports"]["STREAM_RESTART_FROM"]
    assert (
        by_name["segment_05_model"]["exports"]["HICAR_EXPECTED_COMMIT"]
        == plan["expected_hicar_commit"]
    )
    assert (
        by_name["restart_overlap_model"]["exports"]["HICAR_EXPECTED_COMMIT"]
        == plan["expected_hicar_commit"]
    )
    assert by_name["segment_05_compression"]["dependencies"] == [
        {"kind": "afterok", "job": "segment_05_model"}
    ]
    assert by_name["segment_05_solver_audit"]["dependencies"] == [
        {"kind": "afterok", "job": "segment_05_model"}
    ]
    assert by_name["segment_05_forcing_retirement"]["dependencies"] == [
        {"kind": "afterok", "job": "segment_05_model"}
    ]
    assert (
        by_name["segment_05_forcing_retirement"]["exports"][
            "FORCING_RETIREMENT_REPORT"
        ]
        == segments[4]["forcing_retirement_report"]
    )
    assert by_name["segment_01_restart_retirement"]["dependencies"] == [
        {"kind": "afterok", "job": "restart_trajectory_comparison"}
    ]
    assert by_name["segment_02_restart_retirement"]["dependencies"] == [
        {"kind": "afterok", "job": "segment_01_restart_retirement"}
    ]
    assert {
        item["job"]
        for item in by_name["segment_03_restart_retirement"]["dependencies"]
    } == {"segment_04_solver_audit", "segment_02_restart_retirement"}
    assert {
        item["job"]
        for item in by_name["segment_04_restart_retirement"]["dependencies"]
    } == {"segment_05_solver_audit", "segment_03_restart_retirement"}
    assert (
        by_name["segment_01_model"]["exports"]["STREAM_REA_L_LAND_INITIALIZATION"]
        == "1"
    )
    assert by_name["month_validate_physical"]["dependencies"] == [
        {"kind": "afterok", "job": "segment_05_model"}
    ]
    assert {
        item["job"]
        for item in by_name["month_validate_swissmetnet"]["dependencies"]
    } == {
        "segment_05_model",
        "month_rea_l_reference_finalize",
        "month_swissmetnet_observations",
    }
    assert by_name["month_drift_screen"]["dependencies"] == [
        {"kind": "afterok", "job": "month_validate_physical"}
    ]
    assessment_dependencies = {
        item["job"] for item in by_name["month_assessment"]["dependencies"]
    }
    assert {
        "restart_trajectory_comparison",
        "month_drift_screen",
        "month_validate_physical",
        "month_validate_rea_l_source",
        "month_validate_swissmetnet",
        "month_validate_ogd_grid",
        "segment_01_solver_audit",
        "segment_05_compression",
        "segment_05_forcing_retirement",
        "restart_overlap_solver_audit",
        "restart_overlap_compression",
        "restart_overlap_forcing_retirement",
        "segment_01_restart_retirement",
        "segment_04_restart_retirement",
    } <= assessment_dependencies


def test_sbatch_arguments_resolve_logical_dependencies(tmp_path):
    spec = MODULE.job(
        "model",
        tmp_path / "model.sbatch",
        {"STREAM_PLAN": "/scratch/plan.json"},
        [
            {"kind": "afterok", "job": "forcing"},
            {"kind": "after", "job": "previous", "delay_minutes": 10},
        ],
    )

    arguments = MODULE.sbatch_arguments(
        spec,
        {"forcing": "123", "previous": "122"},
    )

    assert "--dependency=afterok:123,after:122+10" in arguments
    assert "--export=ALL,STREAM_PLAN=/scratch/plan.json" in arguments


def test_month_runtime_stack_is_checksum_frozen(tmp_path):
    segments = [chunk(tmp_path, f"segment_{index}", index) for index in range(1, 6)]
    overlap = chunk(tmp_path, "overlap", 2)
    overlap["forcing_record_count"] = 193
    plan = {
        "_plan_path": str(tmp_path / "month_pilot_plan.json"),
        "expected_hicar_commit": "2ea31109801a2477a946840693934318f8d50c95",
        "static_file": str(tmp_path / "static.nc"),
        "scientific_plan": str(tmp_path / "scientific_plan.json"),
        "segments": segments,
        "uninterrupted_restart_overlap": overlap,
        "validation_sources": {
            **chunk(tmp_path, "validation", 1),
            "expected_reference_record_count": 249,
        },
    }
    jobs = MODULE.build_job_specs(
        plan,
        ROOT,
        ROOT / "case_studies/swiss_200m",
        ROOT / "HICAR",
    )

    manifest = MODULE.validate_runtime_stack(
        jobs,
        ROOT,
        ROOT / "case_studies/swiss_200m",
    )

    assert manifest
    assert all(len(item["sha256"]) == 64 for item in manifest)
    assert any(
        item["path"].endswith("validate_model_chunk.py")
        for item in manifest
    )
    assert any(
        item["path"].endswith("evaluate_scientific_event.py")
        for item in manifest
    )
    assert any(
        item["path"].endswith("month_source_contract.py")
        for item in manifest
    )


def test_month_runtime_stack_rejects_stale_runner(tmp_path):
    repo = tmp_path / "repo"
    case = repo / "case_studies/swiss_200m"
    files = {
        case / "scripts/run_rea_l_stream_chunk_balfrin.sbatch": "legacy runner",
        case / "streaming/validate_model_chunk.py": (
            "def validate_provenance(): pass\n"
            'payload = {"schema_version": 2}\n'
        ),
        case / "validation/assess_scientific_month.py": (
            "production_provenance\nconsistent_model_identity\n"
            "frozen_hicar_source_commit\nproduction_water_budget_observables\n"
            "cumulative_water_restart_continuity\n"
        ),
        case / "validation/evaluate_scientific_event.py": (
            "production_cumulative\nevaporation_net_cumulative\n"
            "production_eligible\n"
        ),
        case / "validation/month_source_contract.py": (
            "OUTPUT_DIAGNOSTIC_ONLY\nnonzero_runoff_observed\n"
            "pre-existing field equivalence is not exact\n"
        ),
        case / "scripts/render_hicar_namelist.py": "renderer",
    }
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    with pytest.raises(ValueError, match="runtime-stack file is stale"):
        MODULE.validate_runtime_stack([], repo, case)


def test_submission_refuses_missing_child_source_qualification(tmp_path):
    plan_path = tmp_path / "month_plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "status": "PLANNED",
                "authorization": {
                    "decision": "GO_MONTH_AND_100M_CAPACITY_GATE"
                },
                "expected_hicar_commit": "a" * 40,
                "required_parent_hicar_commit": "b" * 40,
                "source_qualification_report": str(
                    tmp_path / "missing_source_qualification.json"
                ),
                "source_qualification_sha256": None,
            }
        )
    )
    Path(f"{plan_path}.ready").touch()

    result = subprocess.run(
        [
            sys.executable,
            str(MODULE_PATH),
            "--month-plan",
            str(plan_path),
            "--repo-root",
            str(tmp_path / "repo"),
            "--case-root",
            str(tmp_path / "case"),
            "--hicar-root",
            str(tmp_path / "hicar"),
        ],
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "month source qualification failed" in result.stderr
