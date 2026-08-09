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


def campaign(tmp_path, *, full_season_input_lists: bool) -> Campaign:
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
        "radiation_update_interval": 600,
        "seasons": [{
            "name": "autumn",
            "start": "2020-10-02T00:00:00",
            "end": "2020-10-03T00:00:00",
            "static": "static.nc",
        }],
    }
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


def test_radiation_update_interval_is_explicit_in_model_environment(
    tmp_path, monkeypatch
) -> None:
    configured = campaign(tmp_path, full_season_input_lists=False)
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
