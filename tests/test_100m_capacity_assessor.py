from __future__ import annotations

import json
import hashlib
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
ASSESSOR = (
    ROOT
    / "case_studies/swiss_100m/validation/assess_engineering_capacity_gate.py"
)
BASE_CONFIG = (
    ROOT / "case_studies/swiss_100m/config/engineering_capacity_gate.json"
)
CHILD = "a" * 40
PARENT = "b" * 40


def publish(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    Path(f"{path}.ready").touch()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_qualification() -> dict:
    return {
        "schema_version": 1,
        "status": "PASS",
        "change_scope": "OUTPUT_DIAGNOSTIC_ONLY",
        "child_commit": CHILD,
        "parent_commit": PARENT,
        "parent_ancestry": {
            "status": "PASS",
            "parent_is_ancestor": True,
            "merge_base": PARENT,
        },
        "evidence": {
            "clean_target_build": {
                "status": "PASS",
                "artifact_sha256": "1" * 64,
                "source_tree_clean": True,
                "source_commit": CHILD,
                "target": "HICAR",
            },
            "restart_continuity": {
                "status": "PASS",
                "artifact_sha256": "2" * 64,
                "source_commit": CHILD,
                "nonzero_runoff_observed": True,
                "compared_fields": [
                    "precipitation",
                    "runoff_surface_cumulative",
                    "runoff_subsurface_cumulative",
                    "evaporation_net_cumulative",
                ],
            },
            "representative_bridge_run": {
                "status": "PASS",
                "artifact_sha256": "3" * 64,
                "source_commit": CHILD,
                "completion_status": "PASS",
            },
            "national_short_run": {
                "status": "PASS",
                "artifact_sha256": "4" * 64,
                "source_commit": CHILD,
                "completion_status": "PASS",
            },
            "preexisting_field_equivalence": {
                "status": "PASS",
                "artifact_sha256": "5" * 64,
                "compared_field_count": 20,
                "mismatch_count": 0,
            },
            "solver_gate_equivalence": {
                "status": "PASS",
                "artifact_sha256": "6" * 64,
                "compared_gate_count": 8,
                "mismatch_count": 0,
            },
        },
    }


def write_memory(directory: Path, low_gpu: bool = False) -> None:
    directory.mkdir(parents=True)
    for rank in range(64):
        host = f"node{rank // 4:02d}"
        peak = 39000 if low_gpu and rank == 0 else 20000
        (directory / f"gpu_rank_{rank}_{host}.txt").write_text(
            f"rank={rank}\nhost={host}\ngpu_index={rank % 4}\n"
            f"peak_gpu_memory_mib={peak}\ntotal_gpu_memory_mib=40960\n"
        )
    for node in range(16):
        host = f"node{node:02d}"
        (directory / f"node_{host}.txt").write_text(
            f"host={host}\ntotal_memory_kib=100000\n"
            "minimum_available_memory_kib=30000\n"
        )


def build_fixture(tmp_path: Path, low_gpu: bool = False) -> tuple[Path, Path, list[str]]:
    config = tmp_path / "gate.json"
    static = tmp_path / "static.nc"
    static.write_bytes(b"national-static")
    static_digest = sha256(static)
    config_payload = json.loads(BASE_CONFIG.read_text())
    config_payload["case"]["static_sha256"] = static_digest
    config.write_text(json.dumps(config_payload))
    geometry = tmp_path / "geometry.json"
    publish(
        geometry,
        {
            "status": "PASS",
            "minimum_mass_jacobian": {"value": 0.2},
            "minimum_interface_layer_thickness": {"value_m": 8.0},
            "minimum_mass_level_spacing": {"value_m": 8.0},
            "static_sha256": static_digest,
            "terrain_shape": config_payload["case"]["horizontal_shape_yx"],
        },
    )
    event = tmp_path / "event_assessment.json"
    publish(
        event,
        {
            "assessment_status": "COMPLETE",
            "decision": "GO_MONTH_AND_100M_CAPACITY_GATE",
        },
    )
    qualification = tmp_path / "source_qualification.json"
    publish(qualification, source_qualification())
    segments = []
    time_sets = [
        [
            "2010-01-01T00:00:00",
            "2010-01-01T01:00:00",
            "2010-01-01T02:00:00",
        ],
        ["2010-01-01T03:00:00", "2010-01-01T04:00:00"],
    ]
    for index, (name, continuation) in enumerate(
        (("initial_00_02", False), ("continuation_02_04", True))
    ):
        run = tmp_path / "runs" / name
        model_log = run / "model.out"
        run.mkdir(parents=True)
        model_log.write_text(
            ("Reading restart data\n" if continuation else "")
            + """
 Timing across all compute images:
 total: 100.0 | 99.0 | 101.0
 init: 20.0 | 19.0 | 21.0
 input: 2.0 | 1.0 | 3.0
 output: 3.0 | 2.0 | 4.0
 physics: 70.0 | 69.0 | 71.0
 forcing: 1.0 | 0.5 | 1.5
 wind bal: 2.0 | 1.0 | 3.0
 winds: 2.0 | 1.0 | 3.0
"""
        )
        completion = run / "model_chunk_completion.json"
        publish(
            completion,
            {
                "status": "PASS",
                "restart_continuation": continuation,
                "provenance": {
                    "status": "PASS",
                    "source_commit": "a" * 40,
                    "executable_sha256": "b" * 64,
                "static_sha256": static_digest,
                    "forcing_publication_sha256": "d" * 64,
                },
                "output": {"times": time_sets[index], "size_bytes": 1000},
                "restart": {"size_bytes": 2000},
                "model_log": str(model_log),
            },
        )
        solver = run / "scientific_validation/solver_log_diagnostics.json"
        publish(
            solver,
            {
                "status": "PASS",
                "sleve_geometry": [
                    {
                        "minimum_mass_jacobian": 0.2,
                        "minimum_interface_thickness_m": 8.0,
                    }
                ],
                "adjoint_conservation": {
                    "relative_Bq": {"maximum": 1e-6}
                },
            },
        )
        timing = run / "phase_timing.json"
        publish(
            timing,
            {
                "status": "PASS",
                "model_wall_seconds": 120.0,
                "validation_wall_seconds": 5.0,
                "restart_write_wall_upper_bound_seconds": 20.0,
            },
        )
        memory = run / "memory"
        write_memory(memory, low_gpu=low_gpu and index == 0)
        segments.append(
            {
                "id": name,
                "restart_continuation": continuation,
                "completion_report": str(completion),
                "solver_report": str(solver),
                "timing_report": str(timing),
                "memory_dir": str(memory),
            }
        )
    boundary = tmp_path / "boundary.json"
    publish(boundary, {"status": "PASS"})
    plan = tmp_path / "capacity_gate_plan.json"
    publish(
        plan,
        {
            "status": "AUTHORIZED_AND_PLANNED",
            "gate_config": str(config),
            "gate_config_sha256": sha256(config),
            "event_assessment": str(event),
            "event_assessment_sha256": sha256(event),
            "event_decision": "GO_MONTH_AND_100M_CAPACITY_GATE",
            "expected_hicar_commit": "a" * 40,
            "source_qualification_report": str(qualification),
            "source_qualification_sha256": sha256(qualification),
            "source_qualification_mode": "OUTPUT_DIAGNOSTIC_ONLY",
            "required_parent_hicar_commit": PARENT,
            "geometry_report": str(geometry),
            "geometry_report_sha256": sha256(geometry),
            "static_file": str(static),
            "static_sha256": static_digest,
            "segments": segments,
            "boundary_comparison_report": str(boundary),
        },
    )
    accounting = tmp_path / "accounting.psv"
    labels = [
        "initial_forcing",
        "initial_forcing_finalize",
        "initial_model",
        "initial_solver",
        "continuation_forcing",
        "continuation_forcing_finalize",
        "continuation_model",
        "continuation_solver",
        "restart_boundary",
    ]
    accounting.write_text(
        "JobIDRaw|JobName|State|ElapsedRaw|AllocTRES|Start|End\n"
        + "".join(
            f"{index}|{label}|COMPLETED|10|node=1|start|end\n"
            for index, label in enumerate(labels, start=1)
        )
    )
    jobs = [
        value
        for index, label in enumerate(labels, start=1)
        for value in ("--job", f"{label}={index}")
    ]
    return plan, accounting, jobs


def test_capacity_assessor_qualifies_only_engineering_capacity(tmp_path):
    plan, accounting, jobs = build_fixture(tmp_path)
    report = tmp_path / "assessment.json"
    result = subprocess.run(
        [
            sys.executable,
            str(ASSESSOR),
            "--plan",
            str(plan),
            "--accounting",
            str(accounting),
            *jobs,
            "--report",
            str(report),
        ],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(report.read_text())
    assert payload["decision"] == "QUALIFIED_100M_ENGINEERING_CAPACITY_ONLY"
    assert payload["authorization"]["100m_engineering_capacity"]
    assert not payload["authorization"]["100m_scientific_production"]


def test_capacity_assessor_rejects_legacy_completion_without_provenance(
    tmp_path,
):
    plan, accounting, jobs = build_fixture(tmp_path)
    plan_payload = json.loads(plan.read_text())
    completion_path = Path(plan_payload["segments"][0]["completion_report"])
    completion = json.loads(completion_path.read_text())
    completion.pop("provenance")
    completion_path.write_text(json.dumps(completion))
    report = tmp_path / "assessment.json"

    result = subprocess.run(
        [
            sys.executable,
            str(ASSESSOR),
            "--plan",
            str(plan),
            "--accounting",
            str(accounting),
            *jobs,
            "--report",
            str(report),
        ],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "production provenance is not PASS" in result.stdout
    payload = json.loads(report.read_text())
    assert payload["decision"] == "HOLD_100M_CAPACITY"


def test_capacity_assessor_rejects_mixed_model_executables(tmp_path):
    plan, accounting, jobs = build_fixture(tmp_path)
    plan_payload = json.loads(plan.read_text())
    completion_path = Path(plan_payload["segments"][1]["completion_report"])
    completion = json.loads(completion_path.read_text())
    completion["provenance"]["executable_sha256"] = "e" * 64
    completion_path.write_text(json.dumps(completion))
    report = tmp_path / "assessment.json"

    result = subprocess.run(
        [
            sys.executable,
            str(ASSESSOR),
            "--plan",
            str(plan),
            "--accounting",
            str(accounting),
            *jobs,
            "--report",
            str(report),
        ],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "do not share one source, executable, and static identity" in result.stdout


def test_capacity_assessor_rejects_source_outside_frozen_gate(tmp_path):
    plan, accounting, jobs = build_fixture(tmp_path)
    plan_payload = json.loads(plan.read_text())
    completion_path = Path(plan_payload["segments"][0]["completion_report"])
    completion = json.loads(completion_path.read_text())
    completion["provenance"]["source_commit"] = "b" * 40
    completion_path.write_text(json.dumps(completion))
    report = tmp_path / "assessment.json"

    result = subprocess.run(
        [
            sys.executable,
            str(ASSESSOR),
            "--plan",
            str(plan),
            "--accounting",
            str(accounting),
            *jobs,
            "--report",
            str(report),
        ],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "source commit differs from the frozen capacity gate" in result.stdout


def test_capacity_assessor_rejects_insufficient_gpu_headroom(tmp_path):
    plan, accounting, jobs = build_fixture(tmp_path, low_gpu=True)
    report = tmp_path / "assessment.json"
    result = subprocess.run(
        [
            sys.executable,
            str(ASSESSOR),
            "--plan",
            str(plan),
            "--accounting",
            str(accounting),
            *jobs,
            "--report",
            str(report),
        ],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 1
    assert "GPUs have less than 15% headroom" in result.stdout


def test_capacity_assessor_rejects_plan_identity_rewritten_away_from_source_gate(
    tmp_path,
):
    plan, accounting, jobs = build_fixture(tmp_path)
    plan_payload = json.loads(plan.read_text())
    plan_payload["expected_hicar_commit"] = "b" * 40
    for segment in plan_payload["segments"]:
        completion_path = Path(segment["completion_report"])
        completion = json.loads(completion_path.read_text())
        completion["provenance"]["source_commit"] = "b" * 40
        completion_path.write_text(json.dumps(completion))
    plan.write_text(json.dumps(plan_payload))
    report = tmp_path / "assessment.json"

    result = subprocess.run(
        [
            sys.executable,
            str(ASSESSOR),
            "--plan",
            str(plan),
            "--accounting",
            str(accounting),
            *jobs,
            "--report",
            str(report),
        ],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "source qualification child commit does not match month plan" in (
        result.stdout
    )


def test_capacity_assessor_rejects_gpu_samples_from_wrong_node_topology(
    tmp_path,
):
    plan, accounting, jobs = build_fixture(tmp_path)
    plan_payload = json.loads(plan.read_text())
    memory_dir = Path(plan_payload["segments"][0]["memory_dir"])
    sample = memory_dir / "gpu_rank_63_node15.txt"
    text = sample.read_text().replace("host=node15", "host=node00")
    sample.write_text(text)
    report = tmp_path / "assessment.json"

    result = subprocess.run(
        [
            sys.executable,
            str(ASSESSOR),
            "--plan",
            str(plan),
            "--accounting",
            str(accounting),
            *jobs,
            "--report",
            str(report),
        ],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "exact expected node/GPU topology" in result.stdout


def test_capacity_assessor_requires_complete_accounting_dag(tmp_path):
    plan, accounting, _ = build_fixture(tmp_path)
    report = tmp_path / "assessment.json"

    result = subprocess.run(
        [
            sys.executable,
            str(ASSESSOR),
            "--plan",
            str(plan),
            "--accounting",
            str(accounting),
            "--report",
            str(report),
        ],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "accounting labels do not match the frozen capacity DAG" in result.stdout
