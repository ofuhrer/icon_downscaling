from __future__ import annotations

from datetime import datetime
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "create_balfrin_smoke_campaign",
    ROOT / "scripts/create_balfrin_smoke_campaign.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_site_configuration_path_honors_environment(monkeypatch, tmp_path):
    replacement = tmp_path / "balfrin.env"
    monkeypatch.setenv("HICAR_SITE_CONFIG", str(replacement))
    assert MODULE.selected_site_config(ROOT / "config/balfrin.env") == replacement


def test_definition_is_goal_sized_and_preemptible_controller_compatible(tmp_path):
    payload = MODULE.definition_payload(
        campaign_id="smoke",
        campaign_root=tmp_path / "campaign",
        runtime_manifest=tmp_path / "release/runtime_release.json",
        python_report=tmp_path / "python.json",
        hicar_root=tmp_path / "HICAR",
        build_root=tmp_path / "build",
        static_file=tmp_path / "static.nc",
        expected_commit="a" * 40,
        start=datetime(2020, 7, 1),
        hours=2,
        segment_hours=1,
        output_profile="routine",
    )
    assert payload["purpose"] == "qualification"
    assert payload["goal"]["outcome"].startswith("Verify that the current runtime")
    assert payload["goal"]["resource_rationale"].startswith("One four-node")
    assert payload["model"]["expected_hicar_commit"] == "a" * 40
    assert payload["model"]["nodes"] == 4
    assert payload["model"]["time_limit"] == "01:00:00"
    assert payload["model"]["output_profile"] == "routine"
    assert payload["policy"]["model_node_budget"] == 4
    assert "model_slots" not in payload["policy"]
    assert payload["policy"]["cpu_slots"] == 1
    assert payload["policy"]["segment_hours"] == 1
    assert payload["policy"]["max_model_attempts"] == 0
    assert payload["chains"] == [
        {
            "chain_id": "smoke",
            "start": "2020-07-01T00:00:00",
            "end": "2020-07-01T02:00:00",
            "rea_l_land_initialization": True,
        }
    ]


def test_definition_rejects_non_dividing_segment_length(tmp_path):
    try:
        MODULE.definition_payload(
            campaign_id="smoke",
            campaign_root=tmp_path / "campaign",
            runtime_manifest=tmp_path / "release/runtime_release.json",
            python_report=tmp_path / "python.json",
            hicar_root=tmp_path / "HICAR",
            build_root=tmp_path / "build",
            static_file=tmp_path / "static.nc",
            expected_commit="a" * 40,
            start=datetime(2020, 7, 1),
            hours=3,
            segment_hours=2,
        )
    except ValueError as exc:
        assert "divisible" in str(exc)
    else:
        raise AssertionError("non-dividing segment length was accepted")


def test_definition_writer_is_idempotent_but_refuses_replacement(tmp_path):
    path = tmp_path / "definition.json"
    MODULE.write_json_atomic(path, {"schema_version": 1})
    MODULE.write_json_atomic(path, {"schema_version": 1})
    try:
        MODULE.write_json_atomic(path, {"schema_version": 2})
    except ValueError as exc:
        assert "refusing to replace" in str(exc)
    else:
        raise AssertionError("replacement was not rejected")


def test_build_verification_binds_commit_executable_and_builder(tmp_path):
    hicar_root = tmp_path / "HICAR"
    hicar_root.mkdir()
    subprocess.run(["git", "-C", str(hicar_root), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(hicar_root), "config", "user.name", "Test"],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(hicar_root),
            "config",
            "user.email",
            "test@example.invalid",
        ],
        check=True,
    )
    tracked = hicar_root / "source.txt"
    tracked.write_text("source")
    subprocess.run(["git", "-C", str(hicar_root), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(hicar_root), "commit", "-qm", "source"],
        check=True,
    )
    commit = subprocess.check_output(
        ["git", "-C", str(hicar_root), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    build_root = tmp_path / "build"
    build_root.mkdir()
    executable = build_root / "HICAR_gpu"
    executable.write_bytes(b"executable")
    executable.chmod(0o500)
    builder_digest = hashlib.sha256(b"builder").hexdigest()
    provenance = build_root / "hicar_build_provenance.txt"
    provenance.write_text(
        "\n".join(
            (
                f"source_commit={commit}",
                "variant=gpu-nccl",
                builder_digest,
                f"executable={executable}",
                f"{MODULE.sha256(executable)}  {executable}",
            )
        )
    )
    Path(f"{provenance}.ready").touch()
    MODULE.verify_build(hicar_root, build_root, commit, builder_digest)
