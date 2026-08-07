from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import stat


ROOT = Path(__file__).resolve().parents[1]
PLANNER = ROOT / "orchestration/prepare_preemptible_campaign.py"
RELEASE_SPEC = importlib.util.spec_from_file_location(
    "prepare_runtime_release",
    ROOT / "orchestration/prepare_runtime_release.py",
)
RELEASE_MODULE = importlib.util.module_from_spec(RELEASE_SPEC)
sys.modules[RELEASE_SPEC.name] = RELEASE_MODULE
RELEASE_SPEC.loader.exec_module(RELEASE_MODULE)


def publish_python_environment(report: Path, runtime_manifest: Path, requirements: Path) -> None:
    environment_root = report.with_suffix(".venv")
    executable = environment_root / "bin/python"
    environment_root.mkdir(parents=True, exist_ok=True)
    environment_root.chmod(stat.S_IRWXU)
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.parent.chmod(stat.S_IRWXU)
    if not executable.exists():
        executable.symlink_to(sys.executable)
    freeze = sorted(
        line.strip()
        for line in subprocess.check_output(
            [str(executable), "-m", "pip", "freeze"], text=True
        ).splitlines()
        if line.strip()
    )
    for path in (executable.parent, environment_root):
        path.chmod(stat.S_IRUSR | stat.S_IXUSR)
    freeze_bytes = ("\n".join(freeze) + "\n").encode()
    report.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "status": "PASS",
                "purpose": "preemptible-runtime",
                "environment_root": str(environment_root),
                "immutable": True,
                "python": str(executable),
                "python_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
                "python_version": ".".join(str(item) for item in sys.version_info[:3]),
                "runtime_release": str(runtime_manifest),
                "runtime_release_sha256": hashlib.sha256(runtime_manifest.read_bytes()).hexdigest(),
                "requirements": str(requirements),
                "requirements_sha256": hashlib.sha256(requirements.read_bytes()).hexdigest(),
                "versions": {},
                "pip_freeze": freeze,
                "pip_freeze_sha256": hashlib.sha256(freeze_bytes).hexdigest(),
            }
        )
    )
    Path(f"{report}.ready").touch()


def definition(tmp_path: Path, *, nodes: int = 4, chains: int = 1) -> dict:
    return {
        "schema_version": 1,
        "campaign_id": "test-campaign",
        "campaign_root": str(tmp_path / "campaign"),
        "goal": {
            "outcome": "Resolve the current test question with a bounded campaign.",
            "why_now": "The campaign is the next useful source of evidence.",
            "evidence_needed": ["Validated segment results"],
            "stop_conditions": ["Stop after the declared chains complete"],
            "resource_rationale": "Use only the capacity declared by this test.",
        },
        "model": {
            "expected_hicar_commit": "a" * 40,
            "case_root": str(tmp_path / "case"),
            "hicar_root": str(tmp_path / "HICAR"),
            "static_file": str(tmp_path / "static.nc"),
            "nodes": nodes,
        },
        "policy": {"segment_hours": 24},
        "chains": [
            {
                "chain_id": f"chain-{index}",
                "start": "2020-01-01T00:00:00",
                "end": "2020-01-03T00:00:00",
            }
            for index in range(chains)
        ],
    }


def run_planner(tmp_path: Path, payload: dict) -> subprocess.CompletedProcess[str]:
    release_root = tmp_path / "runtime-release"
    if not release_root.exists():
        RELEASE_MODULE.build_release(ROOT, release_root, "engineering")
    runtime_manifest = release_root / "runtime_release.json"
    requirements = release_root / "requirements/balfrin-preemptible.txt"
    python_report = tmp_path / "python_environment.json"
    publish_python_environment(
        python_report,
        runtime_manifest,
        requirements,
    )
    payload["runtime_release"] = str(runtime_manifest)
    payload["python_environment"] = str(python_report)
    source = tmp_path / "definition.json"
    source.write_text(json.dumps(payload))
    return subprocess.run(
        [
            sys.executable,
            str(PLANNER),
            "--definition",
            str(source),
            "--output",
            str(tmp_path / "campaign_plan.json"),
            "--repo-root",
            str(release_root),
        ],
        text=True,
        capture_output=True,
    )


def test_planner_makes_short_preemptible_segments_and_node_aware_capacity(tmp_path):
    result = run_planner(tmp_path, definition(tmp_path, nodes=16))
    assert result.returncode == 0, result.stderr + result.stdout
    plan_path = tmp_path / "campaign_plan.json"
    plan = json.loads(plan_path.read_text())
    assert Path(f"{plan_path}.ready").is_file()
    assert plan["model"]["partition"] == "preemptible"
    assert plan["policy"]["segment_hours"] == 24
    assert plan["policy"]["model_node_budget"] == 46
    assert plan["policy"]["model_slots"] == 2
    assert plan["policy"]["shared_forcing_cache"] is True
    assert plan["policy"]["input_task_weight"] == 3
    assert plan["policy"]["post_task_weight"] == 1
    assert plan["policy"]["cpu_partition"] == "pp-short"
    assert plan["policy"]["cpu_slots"] == 8
    assert plan["policy"]["max_cpu_slots"] == 8
    assert plan["policy"]["cpu_cpus_per_task"] == 4
    assert plan["policy"]["cpu_retry_max_cpus_per_task"] == 16
    assert plan["forcing_cache"]["shared"] is True
    assert Path(plan["forcing_cache"]["index"]).is_file()
    assert Path(f"{plan['forcing_cache']['index']}.ready").is_file()
    assert len(plan["chains"][0]["segments"]) == 2
    assert all(segment["hours"] == 24 for segment in plan["chains"][0]["segments"])
    assert plan["policy"]["rolling_retirement"] is True
    assert plan["policy"]["preserve_restart_every_segments"] == 30
    assert plan["policy"]["max_unretired_segments_per_chain"] == 2
    assert all("lifecycle_root" in segment for segment in plan["chains"][0]["segments"])


def test_planner_uses_eleven_slots_for_four_node_chains(tmp_path):
    result = run_planner(tmp_path, definition(tmp_path, nodes=4))
    assert result.returncode == 0, result.stderr + result.stdout
    plan = json.loads((tmp_path / "campaign_plan.json").read_text())
    assert plan["policy"]["model_slots"] == 11


def test_planner_fills_two_node_preemptible_budget_with_twenty_three_slots(
    tmp_path,
):
    result = run_planner(tmp_path, definition(tmp_path, nodes=2))
    assert result.returncode == 0, result.stderr + result.stdout
    plan = json.loads((tmp_path / "campaign_plan.json").read_text())
    assert plan["policy"]["model_node_budget"] == 46
    assert plan["policy"]["model_slots"] == 23


def test_planner_accepts_explained_capacity_underfill(tmp_path):
    payload = definition(tmp_path, nodes=2)
    payload["policy"]["model_slots"] = 11
    result = run_planner(tmp_path, payload)
    assert result.returncode == 0, result.stderr + result.stdout
    plan = json.loads((tmp_path / "campaign_plan.json").read_text())
    assert plan["policy"]["model_slots"] == 11
    assert plan["resource_summary"]["unused_nodes_at_capacity"] == 24
    assert plan["goal"]["resource_rationale"] == ("Use only the capacity declared by this test.")


def test_planner_accepts_a_small_goal_sized_node_budget(tmp_path):
    payload = definition(tmp_path, nodes=4)
    payload["policy"]["model_node_budget"] = 4
    result = run_planner(tmp_path, payload)
    assert result.returncode == 0, result.stderr + result.stdout
    plan = json.loads((tmp_path / "campaign_plan.json").read_text())
    assert plan["policy"]["model_node_budget"] == 4
    assert plan["policy"]["model_slots"] == 1


def test_planner_allows_a_short_final_segment(tmp_path):
    payload = definition(tmp_path)
    payload["chains"][0]["end"] = "2020-01-03T01:00:00"
    result = run_planner(tmp_path, payload)
    assert result.returncode == 0, result.stderr + result.stdout
    plan = json.loads((tmp_path / "campaign_plan.json").read_text())
    segments = plan["chains"][0]["segments"]
    assert [segment["hours"] for segment in segments] == [24, 24, 1]
    assert segments[-1]["segment_id"].endswith("_01h")
    assert segments[-1]["end"] == "2020-01-03T01:00:00"


def test_planner_rejects_disabling_rolling_retirement(tmp_path):
    payload = definition(tmp_path)
    payload["policy"]["rolling_retirement"] = False
    result = run_planner(tmp_path, payload)
    assert result.returncode != 0
    assert "rolling_retirement=true" in result.stderr


def test_planner_rejects_privileged_or_underreserved_cpu_work(tmp_path):
    payload = definition(tmp_path)
    payload["policy"]["cpu_partition"] = "pp-production"
    result = run_planner(tmp_path, payload)
    assert result.returncode != 0
    assert "must use the s83-open pp-short" in result.stderr

    payload["policy"]["cpu_partition"] = "pp-short"
    payload["policy"]["cpu_cpus_per_task"] = 2
    result = run_planner(tmp_path, payload)
    assert result.returncode != 0
    assert "at least four cores" in result.stderr

    payload["policy"]["cpu_cpus_per_task"] = 4
    payload["policy"]["max_cpu_attempts"] = 1
    result = run_planner(tmp_path, payload)
    assert result.returncode != 0
    assert "max_cpu_attempts must be 3" in result.stderr


def test_planner_accepts_independent_chains_as_part_of_the_goal(tmp_path):
    payload = definition(tmp_path, chains=2)
    result = run_planner(tmp_path, payload)
    assert result.returncode == 0, result.stderr + result.stdout
    plan = json.loads((tmp_path / "campaign_plan.json").read_text())
    assert len(plan["chains"]) == 2
    assert plan["goal"] == payload["goal"]
    first = json.loads(Path(plan["chains"][0]["segments"][0]["plan"]).read_text())
    second = json.loads(Path(plan["chains"][1]["segments"][0]["plan"]).read_text())
    assert first["forcing_cache"]["shared"] is True
    assert second["forcing_cache"] == first["forcing_cache"]
    assert {record["valid_time"]: record["forcing_file"] for record in first["records"]} == {
        record["valid_time"]: record["forcing_file"] for record in second["records"]
    }
    cache_index = json.loads(Path(plan["forcing_cache"]["index"]).read_text())
    unique = {
        record["forcing_file"]
        for chain in plan["chains"]
        for segment in chain["segments"]
        for record in json.loads(Path(segment["plan"]).read_text())["records"]
    }
    assert cache_index["record_count"] == len(unique)
    assert max(len(record["consumers"]) for record in cache_index["records"]) > 1


def test_planner_binds_and_propagates_per_chain_static_files(tmp_path):
    payload = definition(tmp_path, chains=2)
    payload["chains"][0]["static_file"] = str(tmp_path / "cold-start-0.nc")
    payload["chains"][1]["static_file"] = str(tmp_path / "cold-start-1.nc")
    result = run_planner(tmp_path, payload)
    assert result.returncode == 0, result.stderr + result.stdout
    plan = json.loads((tmp_path / "campaign_plan.json").read_text())
    for index, chain in enumerate(plan["chains"]):
        expected = str((tmp_path / f"cold-start-{index}.nc").resolve())
        assert chain["static_file"] == expected
        assert {segment["static_file"] for segment in chain["segments"]} == {expected}


def test_planner_requires_a_goal_instead_of_an_authorization(tmp_path):
    payload = definition(tmp_path, chains=2)
    del payload["goal"]
    result = run_planner(tmp_path, payload)
    assert result.returncode != 0
    assert "requires a goal" in result.stderr


def test_production_uses_a_clean_production_runtime_without_a_go_artifact(tmp_path):
    payload = definition(tmp_path)
    payload["purpose"] = "production"
    result = run_planner(tmp_path, payload)
    assert result.returncode != 0
    release_manifest = tmp_path / "runtime-release/runtime_release.json"
    release = json.loads(release_manifest.read_text())
    release["purpose"] = "production"
    release["source_dirty"] = False
    release["source_change_sha256"] = None
    release_manifest.chmod(0o644)
    release_manifest.write_text(json.dumps(release))
    result = run_planner(tmp_path, payload)
    assert result.returncode == 0, result.stderr + result.stdout
    plan = json.loads((tmp_path / "campaign_plan.json").read_text())
    assert plan["purpose"] == "production"
    assert "production_authorization" not in plan


def test_planner_rejects_long_model_walltime(tmp_path):
    payload = definition(tmp_path)
    payload["model"]["time_limit"] = "23:30:00"
    result = run_planner(tmp_path, payload)
    assert result.returncode != 0
    assert "00:10:00..06:00:00" in result.stderr
