import importlib.util
import json
from pathlib import Path
import sys

import netCDF4
import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_national_campaign_evaluation.py"
SPEC = importlib.util.spec_from_file_location("national_evaluation_driver", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def write_time_file(path, offset_seconds):
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("time", 1)
        time = dataset.createVariable("time", "f8", ("time",))
        time.units = "seconds since 2020-01-15 00:00:00"
        time.calendar = "standard"
        time[:] = [offset_seconds]


def test_decode_times_normalizes_only_the_known_subsecond_serializer_offset(tmp_path):
    accepted = tmp_path / "accepted.nc"
    write_time_file(accepted, 0.432)

    decoded = MODULE.decode_times(accepted)

    assert decoded[0][0] == MODULE.parse_time("2020-01-15T00:00:00")
    assert decoded[0][1] == pytest.approx(0.432)

    rejected = tmp_path / "rejected.nc"
    write_time_file(rejected, 0.6)
    with pytest.raises(ValueError, match="more than 0.5 s"):
        MODULE.decode_times(rejected)


def build_campaign(tmp_path):
    campaign_root = tmp_path / "campaign"
    data_root = tmp_path / "data"
    output_root = tmp_path / "evaluation"
    seasons = (
        ("winter", "2020-01-15T00:00:00"),
        ("spring", "2020-04-29T00:00:00"),
        ("summer", "2020-07-01T00:00:00"),
        ("autumn", "2020-10-02T00:00:00"),
    )
    config_seasons = []
    output_times = {}
    for name, raw_start in seasons:
        start = MODULE.parse_time(raw_start)
        end = start + MODULE.timedelta(hours=48)
        evaluation_start = start + MODULE.timedelta(hours=24)
        static = tmp_path / "static" / f"{name}.nc"
        static.parent.mkdir(exist_ok=True)
        static.write_bytes(b"static")
        (data_root / "observations").mkdir(parents=True, exist_ok=True)
        (data_root / "reference").mkdir(parents=True, exist_ok=True)
        (data_root / "observations" / f"{name}.csv").write_text("observations\n")
        (data_root / "reference" / f"{name}.csv").write_text("reference\n")
        previous_restart = None
        for index in range(4):
            segment_start = start + MODULE.timedelta(hours=12 * index)
            segment_end = segment_start + MODULE.timedelta(hours=12)
            segment_root = (
                campaign_root
                / name
                / (f"{index:03d}_{MODULE.stamp(segment_start)}_{MODULE.stamp(segment_end)}")
            )
            attempt = segment_root / "attempt-1"
            (attempt / "output").mkdir(parents=True)
            (attempt / "restart").mkdir()
            output = attempt / "output" / f"segment-{index}.nc"
            output.write_bytes(b"not-netcdf")
            times = MODULE.expected_times(segment_start, segment_end, 600)
            if index:
                times = times[1:]
            output_times[output] = [(value, 0.432) for value in times]
            restart = attempt / "restart" / f"terminal-{index}.nc"
            restart.write_bytes(b"restart")
            if previous_restart is not None:
                (attempt / "restart" / "input.nc").symlink_to(previous_restart)
            report = {
                "start": segment_start.isoformat(),
                "end": segment_end.isoformat(),
                "static": str(static),
                "restart": str(restart),
            }
            (attempt / "segment.json").write_text(json.dumps(report))
            (attempt / "segment.complete").touch()
            previous_restart = restart
        config_seasons.append(
            {
                "name": name,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "evaluation_start": evaluation_start.isoformat(),
                "evaluation_end": end.isoformat(),
                "static": str(static),
            }
        )
    config = tmp_path / "campaign.json"
    config.write_text(
        json.dumps(
            {
                "root": str(campaign_root),
                "segment_hours": 12,
                "output_interval": 600,
                "seasons": config_seasons,
            }
        )
    )
    return config, data_root, output_root, output_times


def test_dry_run_builds_complete_command_plan_without_netcdf(tmp_path, monkeypatch):
    config, data_root, output_root, output_times = build_campaign(tmp_path)
    monkeypatch.setattr(MODULE, "decode_times", lambda path: output_times[path])
    monkeypatch.setattr(
        MODULE, "source_identity", lambda path: {"repo_root": str(path), "commit": "a" * 40}
    )

    assert (
        MODULE.main(
            [
                "--campaign-config",
                str(config),
                "--observation-reference-root",
                str(data_root),
                "--output-root",
                str(output_root),
                "--repo-root",
                str(ROOT),
                "--python",
                "python3",
                "--dry-run",
            ]
        )
        == 0
    )
    manifest = json.loads((output_root / "evaluation_manifest.json").read_text())
    assert manifest["temporal_validation"]["campaign_output_interval_seconds"] == 600
    assert "0.432 s" in manifest["temporal_validation"]["serializer_time_normalization"]
    commands = manifest["commands"]
    assert len(commands) == 9
    assert all(
        command[command.index("--overlap-policy") + 1] == "error" for command in commands[:4]
    )
    assert all(
        command[command.index("--simulation-start") + 1].endswith("00:00:00")
        and command[command.index("--evaluation-start") + 1].endswith("00:00:00")
        and command[command.index("--evaluation-end") + 1].endswith("00:00:00")
        and command.count("--output-file") == 3
        for command in commands[:4]
    )
    assert commands[4].count("--metric") == len(
        MODULE.METRICS + MODULE.DIAGNOSTIC_METRICS
    )
    assert set(commands[4][commands[4].index("--metric") + 1 :: 2]) >= set(
        MODULE.METRICS + MODULE.DIAGNOSTIC_METRICS
    )
    assert all("--include-optimistic-best-cell" not in command for command in commands[5:])
    assert all(
        len(item["segments"]) == 4
        and len(item["segments"][0]["output_times"]) == 73
        and all(len(segment["output_times"]) == 72 for segment in item["segments"][1:])
        and len(item["evaluation_times"]) == 25
        and all(
            segment["serializer_time_offset_seconds"]
            == {"minimum": 0.432, "maximum": 0.432}
            for segment in item["segments"]
        )
        for item in manifest["inputs"].values()
    )


def test_all_station_coverage_requires_exact_observed_mapping(tmp_path):
    report = tmp_path / "evaluator.json"
    report.write_text(json.dumps({
        "observation_inventory": {"site_count": 3},
        "station_mapping": {
            "site_count": 3,
            "excluded_outside_domain_site_count": 0,
            "sites": [{"key": key} for key in ("A:1", "B:1", "C:1")],
        },
    }))
    assert MODULE.validate_all_station_coverage(report) == {
        "observation_site_count": 3,
        "mapped_site_count": 3,
        "listed_site_count": 3,
        "excluded_outside_domain_site_count": 0,
    }

    payload = json.loads(report.read_text())
    payload["station_mapping"]["site_count"] = 2
    payload["station_mapping"]["sites"] = payload["station_mapping"]["sites"][:2]
    payload["station_mapping"]["excluded_outside_domain_site_count"] = 1
    report.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="all-station coverage failed"):
        MODULE.validate_all_station_coverage(report)


@pytest.mark.parametrize("mode", ("duplicate", "missing"))
def test_rejects_duplicate_or_missing_join_time(tmp_path, monkeypatch, mode):
    config, data_root, output_root, output_times = build_campaign(tmp_path)
    second = next(
        path for path in output_times if "winter" in str(path) and "segment-1" in path.name
    )
    if mode == "duplicate":
        output_times[second] = [
            (MODULE.parse_time("2020-01-15T12:00:00"), 0.432),
            *output_times[second],
        ]
    else:
        output_times[second] = output_times[second][1:]
    monkeypatch.setattr(MODULE, "decode_times", lambda path: output_times[path])

    with pytest.raises(ValueError, match="output times must be exactly"):
        MODULE.command_plan(config, ROOT, data_root, output_root, "python3")


def test_rejects_unlinked_or_multiply_completed_segment(tmp_path, monkeypatch):
    config, data_root, output_root, output_times = build_campaign(tmp_path)
    monkeypatch.setattr(MODULE, "decode_times", lambda path: output_times[path])
    campaign = json.loads(config.read_text())
    root = Path(campaign["root"])
    start = MODULE.parse_time(campaign["seasons"][0]["start"])
    second_root = (
        root
        / "winter"
        / f"001_{MODULE.stamp(start + MODULE.timedelta(hours=12))}_{MODULE.stamp(start + MODULE.timedelta(hours=24))}"
    )
    (second_root / "attempt-1" / "restart" / "input.nc").unlink()
    with pytest.raises(ValueError, match="continuation is not linked"):
        MODULE.command_plan(config, ROOT, data_root, output_root, "python3")

    duplicate = second_root / "attempt-2"
    duplicate.mkdir()
    (duplicate / "segment.complete").touch()
    with pytest.raises(ValueError, match="exactly one completed attempt"):
        MODULE.completed_attempt(second_root)
