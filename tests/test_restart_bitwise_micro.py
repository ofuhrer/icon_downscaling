from __future__ import annotations

from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RUNNER = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "scripts"
    / "run_restart_bitwise_micro_balfrin.sbatch"
)


def test_restart_bitwise_micro_uses_cpu_and_frozen_source() -> None:
    text = RUNNER.read_text()

    assert "#SBATCH --partition=pp-short" in text
    assert "${HICAR_SOURCE_ROOT:?" in text
    assert "${HICAR_BUILD_ROOT:?" in text
    assert "${HICAR_EXPECTED_COMMIT:?" in text
    assert 'git -C "$source_root" diff --quiet' in text
    assert 'grep -qx "source_commit=$expected_commit"' in text
    assert 'tester_cwd="$source_root/.tester-cwd"' in text
    assert 'cd "$tester_cwd"' in text
    assert 'srun --nodes=1 --ntasks=4 "$tester" all' in text
    assert "grep -c '\\[PASSED\\]'" in text
    assert "! grep -Eq '\\[FAILED\\]|^STOP '" in text
    assert 'test_venv="$source_root/tests/Test_Cases/venv"' in text
    assert '"${test_venv}.invalid.${SLURM_JOB_ID}"' in text


def test_restart_bitwise_micro_requires_zero_tolerance() -> None:
    text = RUNNER.read_text()

    assert "test_reproducibility.sh . restart" in text
    assert "--tolerance 0.0" in text
    assert "--last-timestep-only" in text
    assert '"status": status' in text
    assert '"different_variables": different' in text
    assert 'touch "${report}.ready"' in text
    assert 'rm -f "${report}.ready"' in text
    assert "not a Swiss-national or production-cadence qualification" in text


def test_restart_bitwise_micro_shell_syntax() -> None:
    subprocess.run(["bash", "-n", str(RUNNER)], check=True)
