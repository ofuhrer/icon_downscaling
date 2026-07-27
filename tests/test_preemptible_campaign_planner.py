from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
PLANNER = ROOT / "orchestration/prepare_preemptible_campaign.py"
RELEASE_SPEC = importlib.util.spec_from_file_location(
    "prepare_runtime_release",
    ROOT / "orchestration/prepare_runtime_release.py",
)
RELEASE_MODULE = importlib.util.module_from_spec(RELEASE_SPEC)
sys.modules[RELEASE_SPEC.name] = RELEASE_MODULE
RELEASE_SPEC.loader.exec_module(RELEASE_MODULE)


def definition(tmp_path: Path, *, nodes: int = 4, chains: int = 1) -> dict:
    return {
        "schema_version": 1,
        "campaign_id": "test-campaign",
        "campaign_root": str(tmp_path / "campaign"),
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
    python_report.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "PASS",
                "purpose": "preemptible-runtime",
                "python": sys.executable,
                "python_version": ".".join(
                    str(item) for item in sys.version_info[:3]
                ),
                "runtime_release": str(runtime_manifest),
                "runtime_release_sha256": hashlib.sha256(
                    runtime_manifest.read_bytes()
                ).hexdigest(),
                "requirements": str(requirements),
                "requirements_sha256": hashlib.sha256(
                    requirements.read_bytes()
                ).hexdigest(),
                "versions": {},
                "pip_freeze": [],
            }
        )
    )
    Path(f"{python_report}.ready").touch()
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
    assert plan["policy"]["model_node_budget"] == 44
    assert plan["policy"]["model_slots"] == 2
    assert len(plan["chains"][0]["segments"]) == 2
    assert all(segment["hours"] == 24 for segment in plan["chains"][0]["segments"])
    assert plan["policy"]["rolling_retirement"] is True
    assert plan["policy"]["preserve_restart_every_segments"] == 30
    assert plan["policy"]["max_unretired_segments_per_chain"] == 2
    assert all(
        "lifecycle_root" in segment for segment in plan["chains"][0]["segments"]
    )


def test_planner_uses_eleven_slots_for_four_node_chains(tmp_path):
    result = run_planner(tmp_path, definition(tmp_path, nodes=4))
    assert result.returncode == 0, result.stderr + result.stdout
    plan = json.loads((tmp_path / "campaign_plan.json").read_text())
    assert plan["policy"]["model_slots"] == 11


def test_planner_rejects_disabling_rolling_retirement(tmp_path):
    payload = definition(tmp_path)
    payload["policy"]["rolling_retirement"] = False
    result = run_planner(tmp_path, payload)
    assert result.returncode != 0
    assert "rolling_retirement=true" in result.stderr


def test_planner_requires_published_authorization_for_independent_chains(tmp_path):
    result = run_planner(tmp_path, definition(tmp_path, chains=2))
    assert result.returncode != 0
    assert "independent_chain_authorization" in result.stderr


def test_planner_accepts_published_independent_chain_authorization(tmp_path):
    payload = definition(tmp_path, chains=2)
    authorization = tmp_path / "authorization.json"
    authorization.write_text(
        json.dumps({"status": "PASS", "decision": "GO_INDEPENDENT_CHAINS"})
    )
    Path(f"{authorization}.ready").touch()
    payload["independent_chain_authorization"] = str(authorization)
    result = run_planner(tmp_path, payload)
    assert result.returncode == 0, result.stderr + result.stdout
    plan = json.loads((tmp_path / "campaign_plan.json").read_text())
    assert len(plan["chains"]) == 2
    assert plan["independent_chain_authorization"]["decision"] == (
        "GO_INDEPENDENT_CHAINS"
    )


def test_production_requires_the_published_annual_go_decision(tmp_path):
    payload = definition(tmp_path)
    payload["purpose"] = "production"
    result = run_planner(tmp_path, payload)
    assert result.returncode != 0
    assert "production_authorization" in result.stderr

    authorization = tmp_path / "annual_assessment.json"
    authorization.write_text(
        json.dumps(
            {
                "assessment_status": "COMPLETE",
                "decision": "GO_20_YEAR_200M_PRODUCTION",
                "authorization": {"twenty_year_200m_production": True},
            }
        )
    )
    Path(f"{authorization}.ready").touch()
    payload["production_authorization"] = str(authorization)
    release_manifest = tmp_path / "runtime-release/runtime_release.json"
    release = json.loads(release_manifest.read_text())
    release["purpose"] = "production"
    release["source_dirty"] = False
    release["source_change_sha256"] = None
    release_manifest.chmod(0o644)
    release_manifest.write_text(json.dumps(release))
    result = run_planner(tmp_path, payload)
    assert result.returncode == 0, result.stderr + result.stdout


def test_planner_rejects_long_model_walltime(tmp_path):
    payload = definition(tmp_path)
    payload["model"]["time_limit"] = "23:30:00"
    result = run_planner(tmp_path, payload)
    assert result.returncode != 0
    assert "00:10:00..06:00:00" in result.stderr
