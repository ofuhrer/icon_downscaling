#!/usr/bin/env python3
"""Validate a completed national campaign and run its station evaluation."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import NamedTemporaryFile

try:
    from scripts.restart_transition_provenance import (
        campaign_coordinator_commit,
        receipt_path,
        validate_receipt,
    )
except ModuleNotFoundError:  # Direct execution places scripts/, not the repository, on sys.path.
    from restart_transition_provenance import (
        campaign_coordinator_commit,
        receipt_path,
        validate_receipt,
    )


SEASON_LABELS = {
    "winter": "DJF",
    "djf": "DJF",
    "spring": "MAM",
    "mam": "MAM",
    "summer": "JJA",
    "jja": "JJA",
    "autumn": "SON",
    "son": "SON",
}
METRICS = (
    "temperature_2m_height_adjusted_k",
    "relative_humidity_2m_percent",
    "surface_pressure_height_adjusted_pa",
    "precipitation_interval_kg_m2",
    "wind_speed_10m_m_s",
    "wind_vector",
)
DIAGNOSTIC_METRICS = (
    "u_wind_10m_m_s",
    "v_wind_10m_m_s",
)


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def stamp(value: datetime) -> str:
    return value.strftime("%Y%m%d_%H%M")


def expected_times(
    start: datetime, end: datetime, interval_seconds: int = 3600
) -> list[datetime]:
    duration_seconds = int((end - start).total_seconds())
    if (
        interval_seconds <= 0
        or duration_seconds < 0
        or duration_seconds % interval_seconds
    ):
        raise ValueError("output interval must exactly divide the campaign interval")
    return [
        start + timedelta(seconds=offset)
        for offset in range(0, duration_seconds + 1, interval_seconds)
    ]


def decode_times(path: Path) -> list[tuple[datetime, float]]:
    """Decode output times and normalize HICAR's 0.432 s serialization offset.

    HICAR writes time after adding 5e-6 day in ``output_obj.F90``.  This is a
    serializer artifact, not an integrated timestep.  Only a timestamp within
    half a second of an exact second is normalized; the subsequent campaign
    check still requires that second to be an exact output-cadence slot.
    """
    import netCDF4

    with netCDF4.Dataset(path) as dataset:
        variable = dataset.variables["time"]
        values = netCDF4.num2date(
            variable[:],
            variable.units,
            calendar=getattr(variable, "calendar", "standard"),
            only_use_cftime_datetimes=False,
            only_use_python_datetimes=True,
        )
    result = []
    for value in values:
        raw = datetime(
            value.year,
            value.month,
            value.day,
            value.hour,
            value.minute,
            value.second,
            value.microsecond,
        )
        exact_second = raw.replace(microsecond=0)
        offset = (raw - exact_second).total_seconds()
        if abs(offset) > 0.5:
            raise ValueError(
                f"{path}: output time {raw.isoformat()} is more than 0.5 s "
                "from an exact second"
            )
        result.append((exact_second, offset))
    return result


def digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def file_record(path: Path, *, hashed: bool = False) -> dict:
    path = path.resolve()
    result = {"path": str(path), "size_bytes": path.stat().st_size}
    if hashed:
        result["sha256"] = digest(path)
    return result


def completed_attempt(segment_root: Path) -> Path:
    marked = sorted(path.parent for path in segment_root.glob("attempt-*/segment.complete"))
    if len(marked) != 1:
        raise ValueError(
            f"{segment_root}: expected exactly one completed attempt, found {len(marked)}"
        )
    if not (marked[0] / "segment.json").is_file():
        raise ValueError(f"{marked[0]}: segment.complete lacks segment.json")
    return marked[0]


def validate_segment(
    attempt: Path,
    start: datetime,
    end: datetime,
    static: Path,
    *,
    require_restart: bool,
) -> dict:
    report_path = attempt / "segment.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if parse_time(report["start"]) != start or parse_time(report["end"]) != end:
        raise ValueError(f"{report_path}: segment interval differs from campaign config")
    if parse_time(report.get("model_end", report["end"])) != end:
        raise ValueError(f"{report_path}: segment integrated beyond its checkpoint")
    if Path(report["static"]).resolve() != static.resolve():
        raise ValueError(f"{report_path}: static file differs from campaign config")
    restart = Path(report["restart"])
    if require_restart and not restart.is_file():
        raise ValueError(f"{report_path}: final seasonal restart is absent")
    outputs = sorted((attempt / "output").glob("*.nc"))
    if not outputs:
        raise ValueError(f"{attempt}: completed segment has no NetCDF output")
    seen: dict[datetime, Path] = {}
    serializer_offsets: list[float] = []
    for path in outputs:
        for valid, offset in decode_times(path):
            if valid in seen:
                raise ValueError(
                    f"duplicate output time {valid.isoformat()} in {seen[valid]} and {path}"
                )
            seen[valid] = path
            serializer_offsets.append(offset)
    return {
        "attempt": attempt,
        "report": report_path,
        "report_data": report,
        "restart": restart,
        "outputs": outputs,
        "times": sorted(seen),
        "time_files": seen,
        "serializer_offsets_seconds": serializer_offsets,
    }


def require_live_link(first: dict, second: dict) -> dict:
    if not first["restart"].is_file():
        raise ValueError(
            f"{second['attempt']}: predecessor restart is absent and no receipt exists"
        )
    target = first["restart"].resolve()
    links = [
        path
        for path in (second["attempt"] / "restart").glob("*.nc")
        if path.is_symlink() and path.resolve() == target
    ]
    if len(links) != 1:
        raise ValueError(
            f"{second['attempt']}: continuation is not linked exactly once to {target}"
        )
    return {
        "mode": "live_restart",
        "checkpoint_time": first["report_data"]["end"],
        "predecessor_terminal": str(target),
        "successor_input": str(links[0]),
    }


def require_transition(
    first: dict,
    second: dict,
    *,
    season: str,
    predecessor_index: int,
    campaign_commit: str,
) -> dict:
    path = receipt_path(second["attempt"])
    if not path.exists():
        return require_live_link(first, second)
    validate_receipt(
        path,
        first["attempt"],
        second["attempt"],
        season=season,
        predecessor_index=predecessor_index,
        successor_index=predecessor_index + 1,
        campaign_commit=campaign_commit,
    )
    return {
        "mode": "durable_receipt",
        "checkpoint_time": first["report_data"]["end"],
        "receipt": file_record(path, hashed=True),
    }


def source_identity(repo_root: Path) -> dict:
    commit = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "-C", str(repo_root), "status", "--porcelain"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
    )
    if dirty:
        raise ValueError(f"evaluation repository is dirty: {repo_root}")
    return {"repo_root": str(repo_root.resolve()), "commit": commit}


def command_plan(
    config_path: Path, repo_root: Path, data_root: Path, output_root: Path, python: str
) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    campaign_root = Path(config["root"])
    campaign_commit = campaign_coordinator_commit(campaign_root)
    if float(config.get("segment_hours", 0)) != 12.0:
        raise ValueError("national evaluation requires 12-hour campaign segments")
    output_interval = int(config.get("output_interval", 3600))
    if output_interval <= 0:
        raise ValueError("campaign output_interval must be positive")
    scripts = {
        "evaluator": repo_root / "case_studies/swiss_200m/validation/compare_hicar_rea_l_to_smn.py",
        "postprocessor": repo_root / "scripts/national_campaign_postprocess.py",
        "footprint": repo_root / "scripts/diagnose_station_wind_footprints.py",
    }
    missing_scripts = [str(path) for path in scripts.values() if not path.is_file()]
    if missing_scripts:
        raise ValueError("evaluation scripts are absent: " + ", ".join(missing_scripts))

    seasons = {}
    for item in config["seasons"]:
        label = SEASON_LABELS.get(str(item["name"]).lower())
        if label is None or label in seasons:
            raise ValueError(f"invalid or duplicate season {item['name']!r}")
        start, end = parse_time(item["start"]), parse_time(item["end"])
        evaluation_start = parse_time(item.get("evaluation_start", item["start"]))
        evaluation_end = parse_time(item.get("evaluation_end", item["end"]))
        if end <= start or (end - start).total_seconds() % (12 * 3600):
            raise ValueError(
                f"{item['name']}: campaign interval is not a positive multiple of 12 hours"
            )
        if not start <= evaluation_start < evaluation_end <= end:
            raise ValueError(f"{item['name']}: evaluation window lies outside the campaign")
        evaluation_duration_seconds = (evaluation_end - evaluation_start).total_seconds()
        if evaluation_duration_seconds <= 0 or evaluation_duration_seconds % 3600:
            raise ValueError(
                f"{item['name']}: evaluation window is not a positive whole-hour interval"
            )
        if any(
            (value - start).total_seconds() % 3600
            for value in (evaluation_start, evaluation_end)
        ):
            raise ValueError(f"{item['name']}: evaluation bounds are not whole-hour leads")
        static = Path(item["static"])
        observation = data_root / "observations" / f"{item['name']}.csv"
        reference = data_root / "reference" / f"{item['name']}.csv"
        missing = [str(path) for path in (static, observation, reference) if not path.is_file()]
        if missing:
            raise ValueError(f"{item['name']}: required inputs are absent: {missing}")

        intervals = []
        segment_start = start
        while segment_start < end:
            segment_end = segment_start + timedelta(hours=12)
            intervals.append((segment_start, segment_end))
            segment_start = segment_end
        segments = []
        for index, (segment_start, segment_end) in enumerate(intervals):
            root = (
                campaign_root
                / str(item["name"])
                / (f"{index:03d}_{stamp(segment_start)}_{stamp(segment_end)}")
            )
            segment = validate_segment(
                completed_attempt(root),
                segment_start,
                segment_end,
                static,
                require_restart=index == len(intervals) - 1,
            )
            expected = expected_times(segment_start, segment_end, output_interval)
            if index:
                expected = expected[1:]
            if segment["times"] != expected:
                raise ValueError(
                    f"{item['name']}/{index}: output times must be exactly "
                    f"{expected[0].isoformat()}..{expected[-1].isoformat()} ({len(expected)})"
                )
            segments.append(segment)
        transitions = [
            require_transition(
                first,
                second,
                season=str(item["name"]),
                predecessor_index=index,
                campaign_commit=campaign_commit,
            )
            for index, (first, second) in enumerate(zip(segments, segments[1:]))
        ]
        combined = [value for segment in segments for value in segment["times"]]
        complete_expected = expected_times(start, end, output_interval)
        if combined != complete_expected or len(set(combined)) != len(complete_expected):
            raise ValueError(
                f"{item['name']}: campaign outputs are not exact unique "
                f"{output_interval}-second leads"
            )
        evaluation_times = expected_times(evaluation_start, evaluation_end, 3600)
        absent_evaluation_times = sorted(set(evaluation_times) - set(combined))
        if absent_evaluation_times:
            raise ValueError(
                f"{item['name']}: hourly evaluation times are absent from campaign output"
            )
        time_files = {
            valid: path
            for segment in segments
            for valid, path in segment["time_files"].items()
        }
        outputs = list(dict.fromkeys(time_files[valid] for valid in evaluation_times))
        season_dir = output_root / label
        seasons[label] = {
            "name": item["name"],
            "start": start,
            "end": end,
            "evaluation_start": evaluation_start,
            "evaluation_end": evaluation_end,
            "evaluation_times": evaluation_times,
            "static": static,
            "observation": observation,
            "reference": reference,
            "segments": segments,
            "transitions": transitions,
            "outputs": outputs,
            "evaluator_report": season_dir / "evaluator.json",
            "footprint_report": season_dir / "wind_footprints.json",
        }
    if set(seasons) != {"DJF", "MAM", "JJA", "SON"}:
        raise ValueError("campaign config must contain exactly DJF, MAM, JJA, and SON")

    commands = []
    for label in ("DJF", "MAM", "JJA", "SON"):
        item = seasons[label]
        command = [
            python,
            str(scripts["evaluator"]),
            "--event-name",
            f"national-{label}",
            "--static-file",
            str(item["static"]),
            "--native-reference-csv",
            str(item["reference"]),
            "--observations",
            str(item["observation"]),
            "--report",
            str(item["evaluator_report"]),
            "--overlap-policy",
            "error",
            "--simulation-start",
            item["start"].isoformat(),
            "--evaluation-start",
            item["evaluation_start"].isoformat(),
            "--evaluation-end",
            item["evaluation_end"].isoformat(),
        ]
        for path in item["outputs"]:
            command.extend(["--output-file", str(path)])
        commands.append(command)
    national_summary = output_root / "national_summary.json"
    station_csv = output_root / "station_season_metrics.csv"
    postprocess = [python, str(scripts["postprocessor"])]
    for label in ("DJF", "MAM", "JJA", "SON"):
        postprocess.extend(["--report", f"{label}={seasons[label]['evaluator_report']}"])
    for metric in METRICS + DIAGNOSTIC_METRICS:
        postprocess.extend(["--metric", metric])
    postprocess.extend(
        ["--output-csv", str(station_csv), "--output-summary", str(national_summary)]
    )
    commands.append(postprocess)
    for label in ("DJF", "MAM", "JJA", "SON"):
        item = seasons[label]
        command = [
            python,
            str(scripts["footprint"]),
            "--evaluator-report",
            str(item["evaluator_report"]),
            "--static-file",
            str(item["static"]),
            "--observations",
            str(item["observation"]),
            "--report",
            str(item["footprint_report"]),
        ]
        for path in item["outputs"]:
            command.extend(["--output-file", str(path)])
        commands.append(command)

    inputs = {}
    for label, item in seasons.items():
        inputs[label] = {
            "name": item["name"],
            "start": item["start"].isoformat(),
            "end": item["end"].isoformat(),
            "evaluation_start": item["evaluation_start"].isoformat(),
            "evaluation_end": item["evaluation_end"].isoformat(),
            "evaluation_times": [
                value.isoformat() for value in item["evaluation_times"]
            ],
            "static": file_record(item["static"]),
            "observations": file_record(item["observation"], hashed=True),
            "rea_l_native": file_record(item["reference"], hashed=True),
            "segments": [
                {
                    "attempt": str(segment["attempt"].resolve()),
                    "segment_json": file_record(segment["report"], hashed=True),
                    "terminal_restart": (
                        file_record(segment["restart"])
                        if segment["restart"].is_file()
                        else {"path": str(segment["restart"]), "retained": False}
                    ),
                    "outputs": [file_record(path) for path in segment["outputs"]],
                    "output_times": [value.isoformat() for value in segment["times"]],
                    "serializer_time_offset_seconds": {
                        "minimum": min(segment["serializer_offsets_seconds"]),
                        "maximum": max(segment["serializer_offsets_seconds"]),
                    },
                }
                for segment in item["segments"]
            ],
            "restart_transitions": item["transitions"],
        }
    return {
        "commands": commands,
        "inputs": inputs,
        "temporal_validation": {
            "campaign_output_interval_seconds": output_interval,
            "evaluation_sample_interval_seconds": 3600,
            "serializer_time_normalization": (
                "Raw NetCDF times may carry HICAR output_obj.F90's 5e-6-day "
                "(0.432 s) serialization offset. The raw signed offset is recorded "
                "per segment, normalized only within 0.5 s of an exact second, and "
                "the normalized time must still match an exact campaign cadence slot."
            ),
        },
        "outputs": {
            "national_summary": str(national_summary),
            "station_csv": str(station_csv),
            "footprints": {label: str(item["footprint_report"]) for label, item in seasons.items()},
            "evaluators": {label: str(item["evaluator_report"]) for label, item in seasons.items()},
        },
    }


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def validate_all_station_coverage(path: Path) -> dict:
    """Require the evaluator itself to prove that no observed site was dropped."""
    report = json.loads(path.read_text(encoding="utf-8"))
    observed = int(report["observation_inventory"]["site_count"])
    mapping = report["station_mapping"]
    mapped = int(mapping["site_count"])
    excluded = int(mapping["excluded_outside_domain_site_count"])
    listed = len(mapping["sites"])
    if excluded != 0 or mapped != observed or listed != observed:
        raise ValueError(
            f"{path}: all-station coverage failed: observed={observed}, "
            f"mapped={mapped}, listed={listed}, excluded={excluded}"
        )
    return {
        "observation_site_count": observed,
        "mapped_site_count": mapped,
        "listed_site_count": listed,
        "excluded_outside_domain_site_count": excluded,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-config", required=True, type=Path)
    parser.add_argument("--observation-reference-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        plan = command_plan(
            args.campaign_config.resolve(),
            args.repo_root.resolve(),
            args.observation_reference_root.resolve(),
            args.output_root.resolve(),
            args.python,
        )
        manifest = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "dry_run": args.dry_run,
            "source": source_identity(args.repo_root.resolve()),
            "campaign_config": file_record(args.campaign_config, hashed=True),
            **plan,
        }
        if not args.dry_run:
            labels = ("DJF", "MAM", "JJA", "SON")
            evaluator_paths = [
                Path(plan["outputs"]["evaluators"][label]) for label in labels
            ]
            coverage = {}
            for index, command in enumerate(plan["commands"]):
                subprocess.run(command, cwd=args.repo_root, check=True)
                if index < len(evaluator_paths):
                    coverage[labels[index]] = validate_all_station_coverage(
                        evaluator_paths[index]
                    )
            manifest["all_station_coverage"] = coverage
        atomic_json(args.output_root / "evaluation_manifest.json", manifest)
    except (
        OSError,
        ValueError,
        KeyError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
    ) as error:
        parser.error(str(error))
    print(
        json.dumps(manifest, indent=2, sort_keys=True)
        if args.dry_run
        else f"Wrote {args.output_root / 'evaluation_manifest.json'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
