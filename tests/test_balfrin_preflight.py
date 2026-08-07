from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "balfrin_preflight",
    ROOT / "scripts/balfrin_preflight.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_repository_site_configuration_selects_supported_production_line():
    config = MODULE.load_config(ROOT / "config/balfrin.env", {})
    assert config["HICAR_PRIMARY_WORKFLOW"] == "preemptible"
    assert config["HICAR_PRODUCTION_BRANCH"] == "feature/icon_downscaling"
    assert config["HICAR_PRODUCTION_COMMIT"] == (
        "6bd302f8b97062cd43c1b8d4e59bd3cf0dc8ae07"
    )
    assert config["ICON_DOWNSCALING_DURABLE_ROOT"].startswith("/store_new/")


def test_environment_can_override_a_site_default():
    config = MODULE.load_config(
        ROOT / "config/balfrin.env",
        {"REA_FDB_IMAGE": "fdb/test:v1"},
    )
    assert config["REA_FDB_IMAGE"] == "fdb/test:v1"


def test_site_configuration_path_honors_environment(monkeypatch, tmp_path):
    replacement = tmp_path / "balfrin.env"
    monkeypatch.setenv("HICAR_SITE_CONFIG", str(replacement))
    assert MODULE.selected_site_config(ROOT / "config/balfrin.env") == replacement


def test_ready_marker_is_published_only_for_a_passing_report(tmp_path):
    path = tmp_path / "preflight.json"
    MODULE.publish(path, {"status": "FAIL"})
    assert path.is_file()
    assert not Path(f"{path}.ready").exists()
    MODULE.publish(path, {"status": "PASS"})
    assert Path(f"{path}.ready").is_file()


def test_check_path_requires_write_permission(monkeypatch, tmp_path):
    checks = []
    monkeypatch.setattr(
        os,
        "access",
        lambda _path, mode: mode != os.W_OK,
    )
    MODULE.check_path(
        checks,
        "durable",
        tmp_path,
        writable=True,
    )
    assert checks == [
        {
            "name": "durable",
            "status": "FAIL",
            "required": True,
            "detail": f"{tmp_path} (exists, writable)",
        }
    ]
