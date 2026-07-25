from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "validation"
    / "validate_restart_initialization_source.py"
)
WRAPPER = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "scripts"
    / "validate_restart_initialization_source_balfrin.sbatch"
)
SPEC = importlib.util.spec_from_file_location(
    "validate_restart_initialization_source", SCRIPT
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_restart_source_scope_is_narrow() -> None:
    assert MODULE.EXPECTED_CHANGED_FILES == {
        "src/physics/lsm_driver.F90",
        "src/physics/pbl_driver.F90",
    }


def test_artifact_identity_includes_size_and_checksum(tmp_path: Path) -> None:
    path = tmp_path / "artifact.bin"
    path.write_bytes(b"restart evidence")

    identity = MODULE.artifact(path)

    assert identity["path"] == str(path)
    assert identity["size_bytes"] == len(b"restart evidence")
    assert identity["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_only_passing_qualification_receives_ready_marker(
    tmp_path: Path,
) -> None:
    report = tmp_path / "qualification.json"

    digest = MODULE.publish(report, {"status": "PASS"})
    ready = Path(f"{report}.ready")

    assert ready.read_text().strip() == digest
    assert hashlib.sha256(report.read_bytes()).hexdigest() == digest

    MODULE.publish(report, {"status": "FAIL"})

    assert report.is_file()
    assert not ready.exists()


def test_balfrin_wrapper_freezes_all_source_evidence_ids() -> None:
    text = WRAPPER.read_text()

    assert "#SBATCH --partition=pp-short" in text
    for name in (
        "HICAR_RESTART_CHILD_COMMIT",
        "HICAR_RESTART_BUILD_JOB_ID",
        "HICAR_RESTART_BRIDGE_JOB_ID",
        "HICAR_RESTART_NATIONAL_JOB_ID",
    ):
        assert f"${{{name}:?" in text
    assert "--restart-tolerances" in text
    assert "test ! -e \"$output\"" in text
    assert "import netCDF4, numpy, yaml" in text
