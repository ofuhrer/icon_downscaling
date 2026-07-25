from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "case_studies/swiss_100m/streaming/submit_engineering_capacity_gate.py"
)
SPEC = importlib.util.spec_from_file_location("submit_100m_capacity", SCRIPT)
SUBMITTER = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(SUBMITTER)


def test_capacity_dag_requires_solver_pass_before_restart_continuation(tmp_path):
    segments = []
    for name, continuation in (
        ("initial_00_02", False),
        ("continuation_02_04", True),
    ):
        chunk = tmp_path / name / "chunk_plan.json"
        chunk.parent.mkdir(parents=True)
        chunk.write_text("{}")
        segments.append(
            {
                "id": name,
                "restart_continuation": continuation,
                "chunk_plan": str(chunk),
                "forcing_record_count": 3,
                "run_dir": str(tmp_path / "runs" / name),
            }
        )
    plan = {
        "expected_hicar_commit": "2ea31109801a2477a946840693934318f8d50c95",
        "static_file": str(tmp_path / "static.nc"),
        "segments": segments,
    }
    jobs = SUBMITTER.build_jobs(
        plan,
        tmp_path / "capacity_gate_plan.json",
        ROOT,
        ROOT / "case_studies/swiss_100m",
        ROOT / "HICAR",
    )
    by_name = {item["name"]: item for item in jobs}
    assert len(jobs) == 10
    assert by_name["initial_forcing"]["array"] == "0-2%3"
    assert by_name["continuation_forcing"]["dependencies"] == [
        "afterok:initial_forcing"
    ]
    assert "afterok:initial_solver" in by_name["continuation_model"]["dependencies"]
    assert by_name["restart_boundary"]["dependencies"] == ["afterok:initial_model"]
    assert set(by_name["capacity_verdict"]["dependencies"]) == {
        "afterok:continuation_solver",
        "afterok:restart_boundary",
    }


def test_capacity_runner_invokes_full_production_provenance_contract():
    runner = (
        ROOT
        / "case_studies/swiss_100m/scripts/run_engineering_capacity_segment_balfrin.sbatch"
    ).read_text()

    for token in (
        "HICAR_EXPECTED_COMMIT",
        "does not match the frozen capacity-gate commit",
        "status --porcelain --untracked-files=no",
        "ls-files --others --exclude-standard",
        "--source-commit-file",
        "--source-tree-status-file",
        "--executable-digest-file",
        "--forcing-publication",
        "--archived-plan",
        "--archived-forcing-publication",
    ):
        assert token in runner


def test_capacity_runtime_stack_is_checksum_frozen(tmp_path):
    plan = {
        "expected_hicar_commit": "2ea31109801a2477a946840693934318f8d50c95",
        "static_file": str(tmp_path / "static.nc"),
        "segments": [],
    }
    jobs = SUBMITTER.build_jobs(
        plan,
        tmp_path / "capacity_gate_plan.json",
        ROOT,
        ROOT / "case_studies/swiss_100m",
        ROOT / "HICAR",
    )

    manifest = SUBMITTER.validate_runtime_stack(
        jobs,
        ROOT,
        ROOT / "case_studies/swiss_100m",
    )

    assert manifest
    assert all(len(item["sha256"]) == 64 for item in manifest)
    assert any(
        item["path"].endswith("validate_model_chunk.py")
        for item in manifest
    )


def test_capacity_runtime_stack_rejects_stale_runner(tmp_path):
    repo = tmp_path / "repo"
    case = repo / "case_studies/swiss_100m"
    files = {
        case / "scripts/run_engineering_capacity_segment_balfrin.sbatch": (
            "legacy runner"
        ),
        repo / "case_studies/swiss_200m/streaming/validate_model_chunk.py": (
            "def validate_provenance(): pass\n"
            'payload = {"schema_version": 2}\n'
        ),
        case / "validation/assess_engineering_capacity_gate.py": (
            "production provenance is not PASS\n"
            "do not share one source, executable, and static identity\n"
        ),
        case / "scripts/render_hicar_namelist.py": "renderer",
    }
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    with pytest.raises(ValueError, match="runtime-stack file is stale"):
        SUBMITTER.validate_runtime_stack([], repo, case)
