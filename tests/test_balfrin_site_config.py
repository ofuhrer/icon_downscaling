from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
LOADER = ROOT / "scripts/load_balfrin_site_config.sh"


def source(environment: dict[str, str] | None = None):
    active = os.environ.copy()
    for name in ("USER_ENV_ROOT", "REA_FDB_IMAGE", "HICAR_COMMIT", "HICAR_SITE_CONFIG"):
        active.pop(name, None)
    active.update(environment or {})
    return subprocess.run(
        ["bash", "-c", f"set -eu; . {LOADER}; printf '%s\n' \"$USER_ENV_ROOT\" \"$REA_FDB_IMAGE\" \"$HICAR_COMMIT\""],
        env=active, text=True, capture_output=True,
    )


def test_loader_supplies_minimal_defaults() -> None:
    result = source()
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "/mch-environment/v8", "fdb/5.18:v1", "5d5574959f5c62feb183d184ab6ef99d2adfce80"
    ]


def test_loader_preserves_override() -> None:
    result = source({"REA_FDB_IMAGE": "fdb/operator:v1"})
    assert result.returncode == 0
    assert result.stdout.splitlines()[1] == "fdb/operator:v1"


def test_maintained_slurm_scripts_load_site_config_before_modules() -> None:
    scripts = ROOT / "case_studies/swiss_200m/scripts"
    for path in scripts.glob("*.sbatch"):
        text = path.read_text()
        if "load_balfrin_site_config.sh" in text:
            assert text.index("load_balfrin_site_config.sh") < text.index("/etc/profile.d/modules.sh"), path.name
