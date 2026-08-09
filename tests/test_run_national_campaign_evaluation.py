import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_national_campaign_evaluation.py"
SPEC = importlib.util.spec_from_file_location("national_evaluation_driver", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


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
        end = start + MODULE.timedelta(hours=24)
        static = tmp_path / "static" / f"{name}.nc"
        static.parent.mkdir(exist_ok=True)
        static.write_bytes(b"static")
        (data_root / "observations").mkdir(parents=True, exist_ok=True)
        (data_root / "reference").mkdir(parents=True, exist_ok=True)
        (data_root / "observations" / f"{name}.csv").write_text("observations\n")
        (data_root / "reference" / f"{name}.csv").write_text("reference\n")
        previous_restart = None
        for index, (segment_start, segment_end) in enumerate(
            ((start, start + MODULE.timedelta(hours=12)), (start + MODULE.timedelta(hours=12), end))
        ):
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
            times = MODULE.expected_times(segment_start, segment_end)
            if index:
                times = times[1:]
            output_times[output] = times
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
                "static": str(static),
            }
        )
    config = tmp_path / "campaign.json"
    config.write_text(
        json.dumps({"root": str(campaign_root), "segment_hours": 12, "seasons": config_seasons})
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
    commands = manifest["commands"]
    assert len(commands) == 9
    assert all(
        command[command.index("--overlap-policy") + 1] == "error" for command in commands[:4]
    )
    assert commands[4].count("--metric") == 5
    assert set(commands[4][commands[4].index("--metric") + 1 :: 2]) >= set(MODULE.METRICS)
    assert all("--include-optimistic-best-cell" not in command for command in commands[5:])
    assert all(
        len(item["segments"][0]["output_times"]) == 13
        and len(item["segments"][1]["output_times"]) == 12
        for item in manifest["inputs"].values()
    )


@pytest.mark.parametrize("mode", ("duplicate", "missing"))
def test_rejects_duplicate_or_missing_join_time(tmp_path, monkeypatch, mode):
    config, data_root, output_root, output_times = build_campaign(tmp_path)
    second = next(
        path for path in output_times if "winter" in str(path) and "segment-1" in path.name
    )
    if mode == "duplicate":
        output_times[second] = [MODULE.parse_time("2020-01-15T12:00:00"), *output_times[second]]
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
