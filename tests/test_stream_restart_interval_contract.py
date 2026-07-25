from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "scripts"
    / "run_rea_l_stream_chunk_balfrin.sbatch"
)


def test_stream_runner_supports_bounded_periodic_restart_checkpoints():
    source = RUNNER.read_text()

    assert (
        "restart_records=${STREAM_RESTART_INTERVAL_RECORDS:-$total_output_records}"
        in source
    )
    assert 'test "$restart_records" -gt 0' in source
    assert 'test "$restart_records" -le "$total_output_records"' in source
    assert "total_output_records % restart_records" in source
    assert '--restart-interval "$restart_records"' in source
    assert (
        "chunk output-record count must be divisible by "
        "STREAM_RESTART_INTERVAL_RECORDS"
    ) in source


def test_stream_runner_can_pin_the_hicar_source_commit():
    source = RUNNER.read_text()

    assert 'source_commit=$(git -C "$root" rev-parse HEAD)' in source
    assert 'test "$source_commit" != "$HICAR_EXPECTED_COMMIT"' in source
    assert "does not match HICAR_EXPECTED_COMMIT" in source
    assert 'printf \'%s\\n\' "$source_commit" > "$run/source_commit.txt"' in source


def test_stream_runner_rejects_a_stale_model_validator_before_hicar():
    source = RUNNER.read_text()

    preflight = source.index('validator_help=$("$python" "$validator" --help)')
    launch = source.index("srun --distribution=block:block")
    assert preflight < launch
    assert "required_validator_options=(" in source
    for option in (
        "--source-commit-file",
        "--source-tree-status-file",
        "--executable-digest-file",
        "--archived-plan",
        "--archived-forcing-publication",
        "--restart-continuation",
    ):
        assert option in source[preflight:launch]
    assert "model validator does not support runner option" in source
