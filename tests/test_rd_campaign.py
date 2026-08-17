from datetime import datetime

import json
import multiprocessing
import os
from pathlib import Path
import socket

import pytest
import orchestration.rd_campaign as rd_campaign
from orchestration.rd_campaign import (
    Campaign,
    ControllerLockError,
    WatchControllerLock,
    hours,
    segments,
    validate_hicar_runtime_assets,
)


@pytest.fixture(autouse=True)
def no_live_slurm_input_jobs(monkeypatch) -> None:
    monkeypatch.setattr(rd_campaign, "active_slurm_jobs", lambda: {})


def hold_watch_lock(root, config, ready, release) -> None:
    with WatchControllerLock(Path(root), Path(config)):
        ready.set()
        release.wait(10)


def die_with_watch_lock(root, config, ready) -> None:
    lock = WatchControllerLock(Path(root), Path(config))
    lock.__enter__()
    ready.set()
    os._exit(0)


def test_hours_bracket_off_hour_segment() -> None:
    assert list(
        hours(
            datetime(2020, 2, 10, 1, 30),
            datetime(2020, 2, 10, 2, 0),
        )
    ) == [
        datetime(2020, 2, 10, 1, 0),
        datetime(2020, 2, 10, 2, 0),
    ]


def test_fractional_segment_length() -> None:
    assert list(
        segments(
            datetime(2020, 2, 10, 0, 0),
            datetime(2020, 2, 10, 2, 0),
            1.5,
        )
    ) == [
        (datetime(2020, 2, 10, 0, 0), datetime(2020, 2, 10, 1, 30)),
        (datetime(2020, 2, 10, 1, 30), datetime(2020, 2, 10, 2, 0)),
    ]


def test_hicar_runtime_asset_preflight_rejects_missing_ignored_support(
    tmp_path,
) -> None:
    executable = tmp_path / "HICAR_gpu"
    executable.write_bytes(b"executable")
    support = tmp_path / "run"
    support.mkdir()
    config = {
        "hicar_executable": str(executable),
        "hicar_support_dir": str(support),
    }

    with pytest.raises(RuntimeError, match="NoahmpTable.TBL"):
        validate_hicar_runtime_assets(config)

    (support / "NoahmpTable.TBL").write_text("table")
    for name in ("rrtmg_support", "rrtmgp_support", "mp_support"):
        (support / name).mkdir()
    validate_hicar_runtime_assets(config)


def test_hicar_runtime_asset_preflight_rejects_empty_executable(tmp_path) -> None:
    executable = tmp_path / "HICAR_gpu"
    executable.touch()
    support = tmp_path / "run"
    support.mkdir()
    (support / "NoahmpTable.TBL").write_text("table")
    for name in ("rrtmg_support", "rrtmgp_support", "mp_support"):
        (support / name).mkdir()

    with pytest.raises(RuntimeError, match="empty:.*HICAR_gpu"):
        validate_hicar_runtime_assets(
            {
                "hicar_executable": str(executable),
                "hicar_support_dir": str(support),
            }
        )


def test_watch_lock_rejects_a_second_process_without_disturbing_owner(
    tmp_path,
) -> None:
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    root = tmp_path / "campaign"
    config = tmp_path / "campaign.json"
    config.write_text("{}")
    process = context.Process(
        target=hold_watch_lock,
        args=(str(root), str(config), ready, release),
    )
    process.start()
    try:
        assert ready.wait(10)
        owner_pid = int((root / "controller.pid").read_text())
        owner_host = (root / "controller.host").read_text().strip()
        assert owner_pid == process.pid
        with pytest.raises(ControllerLockError) as caught:
            with WatchControllerLock(root, config):
                pass
        assert f"pid {process.pid}" in str(caught.value)
        assert owner_host in str(caught.value)
        assert process.is_alive()
        assert int((root / "controller.pid").read_text()) == process.pid
    finally:
        release.set()
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(10)
    assert process.exitcode == 0


def test_watch_lock_recovers_after_ungraceful_owner_death(tmp_path) -> None:
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    root = tmp_path / "campaign"
    config = tmp_path / "campaign.json"
    config.write_text("{}")
    process = context.Process(
        target=die_with_watch_lock,
        args=(str(root), str(config), ready),
    )
    process.start()
    assert ready.wait(10)
    process.join(10)
    assert process.exitcode == 0
    assert int((root / "controller.pid").read_text()) == process.pid

    with WatchControllerLock(root, config):
        owner = json.loads((root / "controller.lock").read_text())
        assert owner["pid"] == os.getpid()
        assert owner["host"] == socket.gethostname()
        assert int((root / "controller.pid").read_text()) == os.getpid()
    assert not (root / "controller.pid").exists()
    assert not (root / "controller.host").exists()


def campaign(
    tmp_path,
    *,
    full_season_input_lists: bool,
    input_lookahead_segments=None,
    seasons=None,
    segment_hours=12,
    max_active_inputs=2,
    input_cpus=4,
    input_column_workers=1,
    input_rbf_backend="numpy",
    input_exclusive=False,
    use_sparse_lbc=True,
    radiation_scheme="rrtmgp",
    model_partition="preemptible",
    max_active_models=None,
    model_max_partition_fraction=None,
    max_wind_speed_ms=None,
    allow_missing_restart_domain_provenance=False,
    acc_synchronous=False,
    defer_uploads=False,
    gpu_metrics_interval_seconds=0,
    output_profile="evaluation",
    output_interval=600,
) -> Campaign:
    config = {
        "root": str(tmp_path / "campaign"),
        "repo_root": str(tmp_path / "source"),
        "forcing_dir": str(tmp_path / "forcing"),
        "python": "python3",
        "hicar_executable": "HICAR_gpu",
        "hicar_support_dir": "run",
        "hicar_build_provenance": "build.txt",
        "rbf_weights": "weights.nc",
        "full_season_input_lists": full_season_input_lists,
        "segment_hours": segment_hours,
        "max_active_inputs": max_active_inputs,
        "input_cpus": input_cpus,
        "input_column_workers": input_column_workers,
        "input_rbf_backend": input_rbf_backend,
        "input_exclusive": input_exclusive,
        "use_sparse_lbc": use_sparse_lbc,
        "radiation_update_interval": 600,
        "radiation_scheme": radiation_scheme,
        "model_partition": model_partition,
        "allow_missing_restart_domain_provenance": (allow_missing_restart_domain_provenance),
        "acc_synchronous": acc_synchronous,
        "defer_uploads": defer_uploads,
        "gpu_metrics_interval_seconds": gpu_metrics_interval_seconds,
        "output_profile": output_profile,
        "output_interval": output_interval,
        "seasons": seasons
        or [
            {
                "name": "autumn",
                "start": "2020-10-02T00:00:00",
                "end": "2020-10-03T00:00:00",
                "static": "static.nc",
                "static_sha256": "a" * 64,
            }
        ],
    }
    if max_active_models is not None:
        config["max_active_models"] = max_active_models
    if model_max_partition_fraction is not None:
        config["model_max_partition_fraction"] = model_max_partition_fraction
    if max_wind_speed_ms is not None:
        config["max_wind_speed_ms"] = max_wind_speed_ms
    if input_lookahead_segments is not None:
        config["input_lookahead_segments"] = input_lookahead_segments
    path = tmp_path / "campaign.json"
    path.write_text(json.dumps(config))
    return Campaign(path)


def test_wind_climatology_campaign_requires_hourly_aligned_segments(tmp_path) -> None:
    with pytest.raises(ValueError, match="output_interval=3600"):
        campaign(
            tmp_path,
            full_season_input_lists=False,
            output_profile="wind_climatology",
            output_interval=600,
        )

    configured = campaign(
        tmp_path,
        full_season_input_lists=False,
        output_profile="wind_climatology",
        output_interval=3600,
    )
    assert configured.output_profile == "wind_climatology"
    assert configured.output_interval == 3600

    with pytest.raises(ValueError, match="whole UTC hours"):
        campaign(
            tmp_path,
            full_season_input_lists=False,
            output_profile="wind_climatology",
            output_interval=3600,
            segment_hours=1.5,
        )


def test_full_season_input_plan_is_shared_across_segments(tmp_path) -> None:
    configured = campaign(tmp_path, full_season_input_lists=True)
    season = configured.seasons[0]
    first_root = configured.root / "autumn" / "first"
    second_root = configured.root / "autumn" / "second"
    first = configured.segment_input_plan(
        season, first_root, season.start, datetime(2020, 10, 2, 12)
    )
    second = configured.segment_input_plan(
        season, second_root, datetime(2020, 10, 2, 12), season.end
    )
    assert len(first[0]) == 25
    assert first == second
    assert first[1] == configured.root / "autumn" / "forcing.txt"


def test_default_input_plan_remains_segment_local(tmp_path) -> None:
    configured = campaign(tmp_path, full_season_input_lists=False)
    season = configured.seasons[0]
    segment_root = configured.root / "autumn" / "first"
    records, forcing_list, boundary_list = configured.segment_input_plan(
        season, segment_root, season.start, datetime(2020, 10, 2, 12)
    )
    assert len(records) == 13
    assert forcing_list == segment_root / "forcing.txt"
    assert boundary_list == segment_root / "lbc.txt"


def test_sparse_lbc_defaults_to_disabled(tmp_path) -> None:
    configured = campaign(tmp_path, full_season_input_lists=False)
    payload = json.loads(configured.config_path.read_text())
    payload.pop("use_sparse_lbc")
    configured.config_path.write_text(json.dumps(payload))
    defaulted = Campaign(configured.config_path)
    assert defaulted.use_sparse_lbc is False


def test_bounded_input_horizon_shares_endpoints_and_applies_backpressure(
    tmp_path, monkeypatch
) -> None:
    configured = campaign(
        tmp_path,
        full_season_input_lists=False,
        input_lookahead_segments=1,
        segment_hours=1,
        max_active_inputs=10,
        input_column_workers=2,
        seasons=[
            {
                "name": "autumn",
                "start": "2020-10-02T00:00:00",
                "end": "2020-10-02T03:00:00",
                "static": "autumn.nc",
            }
        ],
    )
    submitted = []

    def fake_submit(script, environment, job_name, *, partition, sbatch_options=()):
        submitted.append(environment)
        return str(1000 + len(submitted))

    monkeypatch.setattr(rd_campaign, "submit", fake_submit)
    assert configured.prepare_inputs() == 3
    assert [item["VALID_TIME"] for item in submitted] == [
        "2020-10-02T00:00:00",
        "2020-10-02T01:00:00",
        "2020-10-02T02:00:00",
    ]
    assert {item["HICARPREP_COLUMN_WORKERS"] for item in submitted} == {"2"}

    for when in hours(datetime(2020, 10, 2, 0), datetime(2020, 10, 2, 2)):
        forcing, boundary = configured.paths(configured.seasons[0], when)
        forcing.parent.mkdir(parents=True, exist_ok=True)
        Path(f"{forcing}.ready").touch()
        Path(f"{boundary}.ready").touch()
    assert configured.prepare_inputs() == 0

    first_segment = configured.root / "autumn" / "000_20201002_0000_20201002_0100" / "attempt-1"
    first_segment.mkdir(parents=True)
    (first_segment / "segment.complete").touch()
    assert configured.prepare_inputs() == 1
    assert submitted[-1]["VALID_TIME"] == "2020-10-02T03:00:00"


def test_bounded_input_submission_is_fair_and_season_state_does_not_collide(
    tmp_path, monkeypatch
) -> None:
    configured = campaign(
        tmp_path,
        full_season_input_lists=False,
        input_lookahead_segments=0,
        segment_hours=1,
        max_active_inputs=2,
        seasons=[
            {
                "name": name,
                "start": "2020-10-02T00:00:00",
                "end": "2020-10-02T01:00:00",
                "static": f"{name}.nc",
            }
            for name in ("autumn", "winter")
        ],
    )
    submitted = []

    def fake_submit(script, environment, job_name, *, partition, sbatch_options=()):
        submitted.append(environment)
        return str(2000 + len(submitted))

    monkeypatch.setattr(rd_campaign, "submit", fake_submit)
    assert configured.prepare_inputs() == 2
    assert [Path(item["HICAR_FORCING_OUTPUT"]).parent.name for item in submitted] == [
        "autumn",
        "winter",
    ]
    for season in ("autumn", "winter"):
        assert (
            configured.root / "input_jobs" / season / "20201002_0000" / "attempt-1.job"
        ).is_file()


def test_bounded_input_cap_counts_active_jobs_outside_current_horizon(
    tmp_path, monkeypatch
) -> None:
    configured = campaign(
        tmp_path,
        full_season_input_lists=False,
        input_lookahead_segments=0,
        segment_hours=1,
        max_active_inputs=1,
        seasons=[
            {
                "name": "autumn",
                "start": "2020-10-02T00:00:00",
                "end": "2020-10-02T03:00:00",
                "static": "autumn.nc",
            }
        ],
    )
    completed = configured.root / "autumn" / "000_20201002_0000_20201002_0100" / "attempt-1"
    completed.mkdir(parents=True)
    (completed / "segment.complete").touch()
    old_job = configured.root / "input_jobs" / "autumn" / "20201002_0000" / "attempt-1.job"
    old_job.parent.mkdir(parents=True)
    old_job.write_text("9876\n")

    monkeypatch.setattr(
        rd_campaign,
        "active_slurm_jobs",
        lambda: {"9876": ("RUNNING", "pp-long")},
    )
    submitted = []
    monkeypatch.setattr(rd_campaign, "submit", lambda *args, **kwargs: submitted.append(args))

    assert configured.prepare_inputs() == 0
    assert submitted == []


def test_bounded_lookahead_rejects_full_season_lists(tmp_path) -> None:
    try:
        campaign(
            tmp_path,
            full_season_input_lists=True,
            input_lookahead_segments=1,
        )
    except ValueError as error:
        assert "segment-local" in str(error)
    else:
        raise AssertionError("bounded preparation accepted full-season model input lists")


def test_input_column_workers_cannot_oversubscribe_allocated_cpus(tmp_path) -> None:
    try:
        campaign(
            tmp_path,
            full_season_input_lists=False,
            input_cpus=4,
            input_column_workers=8,
        )
    except ValueError as error:
        assert "input_cpus" in str(error)
    else:
        raise AssertionError("column workers exceeded the producer CPU allocation")


def test_input_exclusive_adds_slurm_placement_option(tmp_path, monkeypatch) -> None:
    configured = campaign(
        tmp_path,
        full_season_input_lists=False,
        max_active_inputs=1,
        input_exclusive=True,
    )
    options = []

    def fake_submit(script, environment, job_name, *, partition, sbatch_options=()):
        options.append(sbatch_options)
        return "2999"

    monkeypatch.setattr(rd_campaign, "submit", fake_submit)
    assert configured.prepare_inputs() == 1
    assert options and "--exclusive" in options[0]


def test_regular_relaxation_publishes_and_requires_only_forcing(tmp_path, monkeypatch) -> None:
    configured = campaign(
        tmp_path,
        full_season_input_lists=False,
        use_sparse_lbc=False,
        max_active_inputs=1,
    )
    submitted = []

    def fake_submit(script, environment, job_name, *, partition, sbatch_options=()):
        submitted.append(environment)
        return "3999"

    monkeypatch.setattr(rd_campaign, "submit", fake_submit)
    assert configured.prepare_inputs() == 1
    assert submitted[0]["HICARPREP_WRITE_LBC"] == "0"
    assert submitted[0]["HICARPREP_RBF_BACKEND"] == "numpy"
    assert submitted[0]["HICARPREP_RBF_THREADS"] == "1"
    assert submitted[0]["NUMBA_CACHE_DIR"].endswith("/campaign/input_numba_cache")
    assert submitted[0]["HICAR_STATIC_SHA256"] == "a" * 64

    for when in hours(configured.seasons[0].start, configured.seasons[0].end):
        forcing, _ = configured.paths(configured.seasons[0], when)
        forcing.parent.mkdir(parents=True, exist_ok=True)
        Path(f"{forcing}.ready").touch()
    assert configured.inputs_complete()
    assert configured.prepare_inputs() == 0


def test_campaign_rejects_invalid_static_publication_digest(tmp_path) -> None:
    with pytest.raises(ValueError, match="static_sha256"):
        campaign(
            tmp_path,
            full_season_input_lists=False,
            seasons=[
                {
                    "name": "autumn",
                    "start": "2020-10-02T00:00:00",
                    "end": "2020-10-02T01:00:00",
                    "static": "static.nc",
                    "static_sha256": "not-a-digest",
                }
            ],
        )


def test_regular_relaxation_submits_segment_without_boundary_list(tmp_path, monkeypatch) -> None:
    configured = campaign(
        tmp_path,
        full_season_input_lists=False,
        use_sparse_lbc=False,
        segment_hours=1,
        seasons=[
            {
                "name": "autumn",
                "start": "2020-10-02T00:00:00",
                "end": "2020-10-02T01:00:00",
                "static": "autumn.nc",
            }
        ],
    )
    for when in hours(configured.seasons[0].start, configured.seasons[0].end):
        forcing, _ = configured.paths(configured.seasons[0], when)
        forcing.parent.mkdir(parents=True, exist_ok=True)
        Path(f"{forcing}.ready").touch()

    submitted = []

    def fake_submit(script, environment, job_name, *, partition, sbatch_options=()):
        submitted.append(environment)
        return "4000"

    monkeypatch.setattr(rd_campaign, "submit", fake_submit)
    assert configured.submit_segments() == 1
    assert submitted[0]["SPARSE_LBC_FILE_LIST"] == ""
    segment = configured.root / "autumn" / "000_20201002_0000_20201002_0100"
    assert (segment / "forcing.txt").is_file()
    assert not (segment / "lbc.txt").exists()


def test_inputs_only_override_can_prepare_beyond_bounded_horizon(tmp_path, monkeypatch) -> None:
    configured = campaign(
        tmp_path,
        full_season_input_lists=False,
        input_lookahead_segments=0,
        segment_hours=1,
        max_active_inputs=10,
        seasons=[
            {
                "name": "autumn",
                "start": "2020-10-02T00:00:00",
                "end": "2020-10-02T03:00:00",
                "static": "autumn.nc",
            }
        ],
    )
    submitted = []

    def fake_submit(script, environment, job_name, *, partition, sbatch_options=()):
        submitted.append(environment)
        return str(3000 + len(submitted))

    monkeypatch.setattr(rd_campaign, "submit", fake_submit)
    assert configured.prepare_inputs(bounded=False) == 4
    assert [item["VALID_TIME"] for item in submitted] == [
        "2020-10-02T00:00:00",
        "2020-10-02T01:00:00",
        "2020-10-02T02:00:00",
        "2020-10-02T03:00:00",
    ]


def test_radiation_configuration_is_explicit_in_model_environment(tmp_path, monkeypatch) -> None:
    configured = campaign(
        tmp_path,
        full_season_input_lists=False,
        radiation_scheme="rrtmg",
        max_wind_speed_ms=30.0,
        allow_missing_restart_domain_provenance=True,
        defer_uploads=True,
        gpu_metrics_interval_seconds=2,
    )
    configured.config["seasons"][0]["end"] = "2020-10-02T01:00:00"
    configured.seasons = [
        rd_campaign.Season(
            "autumn",
            datetime(2020, 10, 2, 0),
            datetime(2020, 10, 2, 1),
            tmp_path / "static.nc",
        )
    ]
    configured.radiation_update_interval = 600.0
    for when in hours(configured.seasons[0].start, configured.seasons[0].end):
        forcing, boundary = configured.paths(configured.seasons[0], when)
        forcing.parent.mkdir(parents=True, exist_ok=True)
        for path in (forcing, boundary):
            path.touch()
            Path(f"{path}.ready").touch()

    submitted = []

    def fake_submit(script, environment, job_name, *, partition, sbatch_options=()):
        submitted.append(environment)
        return "12345"

    monkeypatch.setattr(rd_campaign, "submit", fake_submit)
    assert configured.submit_segments() == 1
    assert submitted[0]["HICAR_RADIATION_UPDATE_INTERVAL"] == "600.0"
    assert submitted[0]["HICAR_RADIATION_SCHEME"] == "rrtmg"
    assert submitted[0]["HICAR_ALPHA_CONST"] == "1.0"
    assert submitted[0]["HICAR_MAX_WIND_SPEED_MS"] == "30.0"
    assert submitted[0]["HICAR_ALLOW_MISSING_RESTART_DOMAIN_PROVENANCE"] == "1"
    assert submitted[0]["HICAR_DEFER_UPLOADS"] == "1"
    assert submitted[0]["HICAR_GPU_METRICS_INTERVAL_SECONDS"] == "2"
    assert submitted[0]["HICAR_BUILD_PROVENANCE"] == "build.txt"


def test_negative_gpu_metrics_interval_is_rejected(tmp_path) -> None:
    with pytest.raises(ValueError, match="resource counts"):
        campaign(
            tmp_path,
            full_season_input_lists=False,
            gpu_metrics_interval_seconds=-1,
        )


def test_defer_uploads_rejects_synchronous_openacc(tmp_path) -> None:
    with pytest.raises(ValueError, match="cannot both be enabled"):
        campaign(
            tmp_path,
            full_season_input_lists=False,
            acc_synchronous=True,
            defer_uploads=True,
        )


def test_invalid_maximum_wind_speed_is_rejected(tmp_path) -> None:
    try:
        campaign(
            tmp_path,
            full_season_input_lists=False,
            max_wind_speed_ms=float("nan"),
        )
    except ValueError as error:
        assert "max_wind_speed_ms" in str(error)
    else:
        raise AssertionError("non-finite maximum wind speed was accepted")


def test_unknown_radiation_scheme_is_rejected(tmp_path) -> None:
    try:
        campaign(
            tmp_path,
            full_season_input_lists=False,
            radiation_scheme="unknown",
        )
    except ValueError as error:
        assert "radiation_scheme" in str(error)
    else:
        raise AssertionError("unknown radiation scheme was accepted")


def test_model_submission_is_bounded_globally(tmp_path, monkeypatch) -> None:
    configured = campaign(
        tmp_path,
        full_season_input_lists=False,
        max_active_models=1,
        segment_hours=1,
        seasons=[
            {
                "name": name,
                "start": "2020-10-02T00:00:00",
                "end": "2020-10-02T01:00:00",
                "static": f"{name}.nc",
            }
            for name in ("autumn", "winter")
        ],
    )
    for season in configured.seasons:
        for when in hours(season.start, season.end):
            forcing, boundary = configured.paths(season, when)
            forcing.parent.mkdir(parents=True, exist_ok=True)
            for path in (forcing, boundary):
                path.touch()
                Path(f"{path}.ready").touch()

    submitted = []

    def fake_submit(script, environment, job_name, *, partition, sbatch_options=()):
        submitted.append((job_name, partition))
        return "12345"

    monkeypatch.setattr(rd_campaign, "submit", fake_submit)
    monkeypatch.setattr(rd_campaign, "slurm_state", lambda _: "PENDING")
    assert configured.submit_segments() == 1
    assert configured.submit_segments() == 0
    assert submitted == [("hc-aut-000-a1", "preemptible")]


def test_model_partition_fraction_fails_closed(tmp_path, monkeypatch) -> None:
    configured = campaign(
        tmp_path,
        full_season_input_lists=False,
        model_partition="normal",
        max_active_models=2,
        model_max_partition_fraction=0.5,
    )
    monkeypatch.setattr(
        rd_campaign,
        "validate_partition",
        lambda _: {"AllowGroups": "ALL", "TotalNodes": "44"},
    )
    configured.model_nodes = 12
    try:
        configured.validate_model_partition_capacity()
    except RuntimeError as error:
        assert "24/44" in str(error)
        assert "50%" in str(error)
    else:
        raise AssertionError("model partition fraction limit was not enforced")
