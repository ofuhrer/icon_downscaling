from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import stat
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestration"))
RELEASE_SPEC = importlib.util.spec_from_file_location(
    "prepare_runtime_release",
    ROOT / "orchestration/prepare_runtime_release.py",
)
RELEASE = importlib.util.module_from_spec(RELEASE_SPEC)
sys.modules[RELEASE_SPEC.name] = RELEASE
RELEASE_SPEC.loader.exec_module(RELEASE)
CONTRACT_SPEC = importlib.util.spec_from_file_location(
    "runtime_contract",
    ROOT / "orchestration/runtime_contract.py",
)
CONTRACT = importlib.util.module_from_spec(CONTRACT_SPEC)
sys.modules[CONTRACT_SPEC.name] = CONTRACT
CONTRACT_SPEC.loader.exec_module(CONTRACT)


def test_engineering_release_is_hash_bound_and_immutable(tmp_path):
    release_root = tmp_path / "release"
    RELEASE.build_release(ROOT, release_root, "engineering")
    manifest = release_root / "runtime_release.json"
    payload = CONTRACT.validate_runtime_release(
        manifest, expected_root=release_root
    )
    assert payload["status"] == "PASS"
    target = release_root / CONTRACT.REQUIRED_RUNTIME_PATHS[0]
    assert not target.stat().st_mode & stat.S_IWUSR

    target.chmod(0o644)
    target.write_text(target.read_text() + "\n")
    with pytest.raises(ValueError, match="checksum changed"):
        CONTRACT.validate_runtime_release(manifest, expected_root=release_root)


def test_production_release_requires_a_clean_runtime_source(tmp_path):
    source = tmp_path / "source"
    for relative in CONTRACT.REQUIRED_RUNTIME_PATHS:
        destination = source / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(
        ["git", "-C", str(source), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(source), "config", "user.name", "Test"],
        check=True,
    )
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(source), "commit", "-qm", "runtime"], check=True
    )
    clean_release = tmp_path / "clean-release"
    RELEASE.build_release(source, clean_release, "production")
    CONTRACT.validate_runtime_release(
        clean_release / "runtime_release.json",
        expected_root=clean_release,
        production=True,
    )

    dirty_path = source / CONTRACT.REQUIRED_RUNTIME_PATHS[0]
    dirty_path.write_text(dirty_path.read_text() + "\n")
    with pytest.raises(ValueError, match="not clean"):
        RELEASE.build_release(source, tmp_path / "dirty-release", "production")


def test_release_stage_can_be_relocated_to_its_declared_root(tmp_path):
    stage = tmp_path / "stage"
    deployed = tmp_path / "deployed"
    RELEASE.build_release(
        ROOT,
        stage,
        "engineering",
        declared_root=deployed,
    )
    shutil.copytree(stage, deployed)
    payload = CONTRACT.validate_runtime_release(
        deployed / "runtime_release.json",
        expected_root=deployed,
    )
    assert payload["release_root"] == str(deployed)
