from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
LOADER = ROOT / "scripts/load_balfrin_site_config.sh"
PRIMARY_WRAPPERS = (
    "bootstrap_preemptible_python_balfrin.sbatch",
    "build_hicar_balfrin.sbatch",
    "compress_hicar_stream_output_balfrin.sbatch",
    "finalize_rea_l_stream_chunk_balfrin.sbatch",
    "produce_rea_l_stream_record_balfrin.sbatch",
    "run_preemptible_recovery_probe_balfrin.sbatch",
    "run_preemptible_campaign_cpu_task_balfrin.sbatch",
    "run_rea_l_stream_chunk_balfrin.sbatch",
    "validate_solver_event_balfrin.sbatch",
    "watch_preemptible_campaign_balfrin.sbatch",
)


def source_loader(environment: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    active = os.environ.copy()
    if environment:
        active.update(environment)
    return subprocess.run(
        [
            "bash",
            "-c",
            (
                f"set -euo pipefail; . {LOADER!s}; "
                "printf '%s\\n' \"$USER_ENV_ROOT\" \"$REA_FDB_IMAGE\" "
                "\"$HICAR_PRODUCTION_COMMIT\""
            ),
        ],
        env=active,
        text=True,
        capture_output=True,
    )


def test_loader_publishes_repository_defaults():
    environment = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "USER_ENV_ROOT",
            "REA_FDB_IMAGE",
            "HICAR_PRODUCTION_COMMIT",
            "HICAR_SITE_CONFIG",
        }
    }
    result = subprocess.run(
        [
            "bash",
            "-c",
            (
                f"set -euo pipefail; . {LOADER!s}; "
                "printf '%s\\n' \"$USER_ENV_ROOT\" \"$REA_FDB_IMAGE\" "
                "\"$HICAR_PRODUCTION_COMMIT\""
            ),
        ],
        env=environment,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "/mch-environment/v8",
        "fdb/5.19:v2",
        "7700c97a0248abcc1db055ef04c22e1ff9ec6d22",
    ]


def test_loader_preserves_explicit_environment_override():
    result = source_loader({"REA_FDB_IMAGE": "fdb/operator:v1"})
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[1] == "fdb/operator:v1"


def test_primary_wrappers_load_site_defaults_before_modules():
    script_root = ROOT / "case_studies/swiss_200m/scripts"
    for name in PRIMARY_WRAPPERS:
        text = (script_root / name).read_text()
        loader_index = text.index("load_balfrin_site_config.sh")
        module_index = text.index("/etc/profile.d/modules.sh")
        assert loader_index < module_index, name


def test_reusable_forcing_converter_loads_site_defaults():
    text = (ROOT / "scripts/prepare_icon_inputs.sh").read_text()
    assert '. "$SCRIPT_DIR/load_balfrin_site_config.sh"' in text
