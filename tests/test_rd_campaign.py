from datetime import datetime

import json
from pathlib import Path

import orchestration.rd_campaign as rd_campaign
from orchestration.rd_campaign import Campaign, hours, segments


def test_hours_bracket_off_hour_segment() -> None:
    assert list(hours(
        datetime(2020, 2, 10, 1, 30),
        datetime(2020, 2, 10, 2, 0),
    )) == [
        datetime(2020, 2, 10, 1, 0),
        datetime(2020, 2, 10, 2, 0),
    ]


def test_fractional_segment_length() -> None:
    assert list(segments(
        datetime(2020, 2, 10, 0, 0),
        datetime(2020, 2, 10, 2, 0),
        1.5,
    )) == [
        (datetime(2020, 2, 10, 0, 0), datetime(2020, 2, 10, 1, 30)),
        (datetime(2020, 2, 10, 1, 30), datetime(2020, 2, 10, 2, 0)),
    ]


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
    input_exclusive=False,
    use_sparse_lbc=True,
    radiation_scheme="rrtmgp",
    model_partition="preemptible",
    max_active_models=None,
    model_max_partition_fraction=None,
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
        "input_exclusive": input_exclusive,
        "use_sparse_lbc": use_sparse_lbc,
        "radiation_update_interval": 600,
        "radiation_scheme": radiation_scheme,
        "model_partition": model_partition,
        "seasons": seasons or [{
            "name": "autumn",
            "start": "2020-10-02T00:00:00",
            "end": "2020-10-03T00:00:00",
            "static": "static.nc",
        }],
    }
    if max_active_models is not None:
        config["max_active_models"] = max_active_models
    if model_max_partition_fraction is not None:
        config["model_max_partition_fraction"] = model_max_partition_fraction
    if input_lookahead_segments is not None:
        config["input_lookahead_segments"] = input_lookahead_segments
    path = tmp_path / "campaign.json"
    path.write_text(json.dumps(config))
    return Campaign(path)


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
        seasons=[{
            "name": "autumn",
            "start": "2020-10-02T00:00:00",
            "end": "2020-10-02T03:00:00",
            "static": "autumn.nc",
        }],
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

    first_segment = (
        configured.root / "autumn" / "000_20201002_0000_20201002_0100" / "attempt-1"
    )
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


def test_regular_relaxation_publishes_and_requires_only_forcing(
    tmp_path, monkeypatch
) -> None:
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

    for when in hours(configured.seasons[0].start, configured.seasons[0].end):
        forcing, _ = configured.paths(configured.seasons[0], when)
        forcing.parent.mkdir(parents=True, exist_ok=True)
        Path(f"{forcing}.ready").touch()
    assert configured.inputs_complete()
    assert configured.prepare_inputs() == 0


def test_regular_relaxation_submits_segment_without_boundary_list(
    tmp_path, monkeypatch
) -> None:
    configured = campaign(
        tmp_path,
        full_season_input_lists=False,
        use_sparse_lbc=False,
        segment_hours=1,
        seasons=[{
            "name": "autumn",
            "start": "2020-10-02T00:00:00",
            "end": "2020-10-02T01:00:00",
            "static": "autumn.nc",
        }],
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


def test_inputs_only_override_can_prepare_beyond_bounded_horizon(
    tmp_path, monkeypatch
) -> None:
    configured = campaign(
        tmp_path,
        full_season_input_lists=False,
        input_lookahead_segments=0,
        segment_hours=1,
        max_active_inputs=10,
        seasons=[{
            "name": "autumn",
            "start": "2020-10-02T00:00:00",
            "end": "2020-10-02T03:00:00",
            "static": "autumn.nc",
        }],
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


def test_radiation_configuration_is_explicit_in_model_environment(
    tmp_path, monkeypatch
) -> None:
    configured = campaign(
        tmp_path, full_season_input_lists=False, radiation_scheme="rrtmg"
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
    assert submitted[0]["HICAR_BUILD_PROVENANCE"] == "build.txt"


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
