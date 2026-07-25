import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "case_studies" / "swiss_200m" / "scripts"


def test_restart_overlap_runner_uses_hard_link_and_separate_restart_directory():
    script = (SCRIPTS / "run_rea_l_restart_overlap_balfrin.sbatch").read_text()

    assert "#SBATCH --partition=normal" in script
    assert "STREAM_RESTART_SOURCE_CHECKPOINT" in script
    assert 'ln "$source_checkpoint" "$target"' in script
    assert 'test "$source_checkpoint" -ef "$target"' in script
    assert 'export STREAM_RESTART_DIR="$restart_dir"' in script
    assert 'exec bash "$runner"' in script


def test_restart_overlap_runner_can_invoke_read_only_stream_runner(tmp_path):
    source = tmp_path / "source_checkpoint.nc"
    source.write_bytes(b"restart")
    restart_dir = tmp_path / "restart"
    runner = tmp_path / "read_only_runner.sbatch"
    capture = tmp_path / "capture.txt"
    runner.write_text(
        "#!/bin/bash\n"
        'printf "%s\\n%s\\n" "$STREAM_RESTART_DIR" "$STREAM_RESTART_FROM" '
        '> "$TEST_CAPTURE"\n'
    )
    runner.chmod(0o444)
    environment = {
        **os.environ,
        "HICAR_STREAM_RUNNER": str(runner),
        "STREAM_RESTART_SOURCE_CHECKPOINT": str(source),
        "STREAM_RESTART_DIR": str(restart_dir),
        "STREAM_RESTART_FROM": "2020-07-03T00:00:00",
        "TEST_CAPTURE": str(capture),
    }

    result = subprocess.run(
        [
            "bash",
            str(SCRIPTS / "run_rea_l_restart_overlap_balfrin.sbatch"),
        ],
        env=environment,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    target = restart_dir / source.name
    assert source.samefile(target)
    assert capture.read_text().splitlines() == [
        str(restart_dir),
        "2020-07-03T00:00:00",
    ]


def test_event_restart_comparator_requires_both_published_completions():
    script = (SCRIPTS / "compare_event_restart_trajectory_balfrin.sbatch").read_text()

    assert "#SBATCH --partition=pp-long" in script
    assert '"$restarted.ready"' in script
    assert '"$continuous.ready"' in script
    assert "--segmented-completion" in script
    assert "--reference-completion" in script
    assert 'test -s "$report.ready"' in script
