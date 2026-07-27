from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "case_studies/swiss_200m/scripts"


def test_model_runner_has_fail_closed_preemption_path():
    runner = (SCRIPTS / "run_rea_l_stream_chunk_balfrin.sbatch").read_text()
    assert "STREAM_PREEMPTIBLE_ATTEMPT" in runner
    assert "attempt_interrupted.json" in runner
    assert 'preemption_helper" run' in runner
    assert "--completion-marker" in runner
    assert "STREAM_RESTART_INPUT_REPORT" in runner


def test_watcher_hands_off_to_exactly_one_afterany_successor():
    watcher = (SCRIPTS / "watch_preemptible_campaign_balfrin.sbatch").read_text()
    assert "#SBATCH --partition=pp-long" in watcher
    assert "#SBATCH --no-requeue" in watcher
    assert '--dependency="afterany:${SLURM_JOB_ID:?}"' in watcher
    assert "HICAR_CAMPAIGN_CHAIN_WATCHER=1" in watcher


def test_cpu_pool_stays_on_bounded_postprocessing_partition():
    worker = (
        SCRIPTS / "run_preemptible_campaign_cpu_task_balfrin.sbatch"
    ).read_text()
    assert "#SBATCH --partition=pp-short" in worker
    assert "#SBATCH --no-requeue" in worker


def test_forcing_uses_frozen_site_grid_and_validator():
    producer = (
        SCRIPTS / "produce_rea_l_stream_record_balfrin.sbatch"
    ).read_text()
    assert "load_balfrin_site_config.sh" in producer
    assert (
        "validator=${HICAR_FORCING_VALIDATOR:-$repo_root/"
        "case_studies/swiss_200m/validation/validate_forcing.py}"
    ) in producer
    assert (
        "grid_file=${HICAR_FIELD_EXTRA_GRID:-$repo_root/"
        "case_studies/swiss_200m/config/fieldextra_target_grid.txt}"
    ) in producer
    assert '$source_case/validation/validate_forcing.py' not in producer


def test_recovery_probe_is_engineering_only_and_uses_the_signal_guard():
    probe = (
        SCRIPTS / "run_preemptible_recovery_probe_balfrin.sbatch"
    ).read_text()
    assert "#SBATCH --partition=preemptible" in probe
    assert "#SBATCH --no-requeue" in probe
    assert "Engineering-only" in probe
    assert 'helper" run' in probe
    assert "model_chunk_completion.json.ready" in probe
    assert "HICAR_gpu" not in probe


def test_real_hicar_recovery_qualification_is_controller_only():
    wrapper = (
        SCRIPTS / "qualify_hicar_preemptible_recovery_balfrin.sbatch"
    ).read_text()
    qualifier = (
        ROOT / "orchestration/qualify_hicar_preemptible_recovery.py"
    ).read_text()
    assert "#SBATCH --partition=pp-long" in wrapper
    assert "#SBATCH --no-requeue" in wrapper
    assert "--execute" in wrapper
    assert 'cancellation_plan = ("TERM", "KILL")' in qualifier
    assert '"--batch"' in qualifier
    assert "matching_restart_evidence" in qualifier
    assert '"assessment": "ENGINEERING_ONLY"' in qualifier
