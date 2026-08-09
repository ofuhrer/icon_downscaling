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


def expected_times(start: datetime, end: datetime) -> list[datetime]:
    return [
        start + timedelta(hours=hour)
        for hour in range(int((end - start).total_seconds() // 3600) + 1)
    ]


def decode_times(path: Path) -> list[datetime]:
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
        decoded = datetime(
            value.year,
            value.month,
            value.day,
            value.hour,
            value.minute,
            value.second,
            value.microsecond,
        )
        if decoded.microsecond:
            raise ValueError(f"{path}: non-integral-second output time {decoded.isoformat()}")
        result.append(decoded)
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


def validate_segment(attempt: Path, start: datetime, end: datetime, static: Path) -> dict:
    report_path = attempt / "segment.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if parse_time(report["start"]) != start or parse_time(report["end"]) != end:
        raise ValueError(f"{report_path}: segment interval differs from campaign config")
    if parse_time(report.get("model_end", report["end"])) != end:
        raise ValueError(f"{report_path}: segment integrated beyond its checkpoint")
    if Path(report["static"]).resolve() != static.resolve():
        raise ValueError(f"{report_path}: static file differs from campaign config")
    restart = Path(report["restart"])
    if not restart.is_file():
        raise ValueError(f"{report_path}: terminal restart is absent")
    outputs = sorted((attempt / "output").glob("*.nc"))
    if not outputs:
        raise ValueError(f"{attempt}: completed segment has no NetCDF output")
    seen: dict[datetime, Path] = {}
    for path in outputs:
        for valid in decode_times(path):
            if valid in seen:
                raise ValueError(
                    f"duplicate output time {valid.isoformat()} in {seen[valid]} and {path}"
                )
            seen[valid] = path
    return {
        "attempt": attempt,
        "report": report_path,
        "restart": restart,
        "outputs": outputs,
        "times": sorted(seen),
    }


def require_link(first: dict, second: dict) -> None:
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
    if float(config.get("segment_hours", 0)) != 12.0:
        raise ValueError("national evaluation requires 12-hour campaign segments")
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
        if end - start != timedelta(hours=24):
            raise ValueError(f"{item['name']}: evaluation window is not 24 hours")
        static = Path(item["static"])
        observation = data_root / "observations" / f"{item['name']}.csv"
        reference = data_root / "reference" / f"{item['name']}.csv"
        missing = [str(path) for path in (static, observation, reference) if not path.is_file()]
        if missing:
            raise ValueError(f"{item['name']}: required inputs are absent: {missing}")

        midpoint = start + timedelta(hours=12)
        intervals = ((start, midpoint), (midpoint, end))
        segments = []
        for index, (segment_start, segment_end) in enumerate(intervals):
            root = (
                campaign_root
                / str(item["name"])
                / (f"{index:03d}_{stamp(segment_start)}_{stamp(segment_end)}")
            )
            segment = validate_segment(completed_attempt(root), segment_start, segment_end, static)
            expected = expected_times(segment_start, segment_end)
            if index:
                expected = expected[1:]
            if segment["times"] != expected:
                raise ValueError(
                    f"{item['name']}/{index}: output times must be exactly "
                    f"{expected[0].isoformat()}..{expected[-1].isoformat()} ({len(expected)})"
                )
            segments.append(segment)
        require_link(segments[0], segments[1])
        combined = segments[0]["times"] + segments[1]["times"]
        if combined != expected_times(start, end) or len(set(combined)) != 25:
            raise ValueError(f"{item['name']}: campaign outputs are not exact unique leads 0..24")
        outputs = [path for segment in segments for path in segment["outputs"]]
        season_dir = output_root / label
        seasons[label] = {
            "name": item["name"],
            "start": start,
            "end": end,
            "static": static,
            "observation": observation,
            "reference": reference,
            "segments": segments,
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
            "static": file_record(item["static"]),
            "observations": file_record(item["observation"], hashed=True),
            "rea_l_native": file_record(item["reference"], hashed=True),
            "segments": [
                {
                    "attempt": str(segment["attempt"].resolve()),
                    "segment_json": file_record(segment["report"], hashed=True),
                    "restart": file_record(segment["restart"]),
                    "outputs": [file_record(path) for path in segment["outputs"]],
                    "output_times": [value.isoformat() for value in segment["times"]],
                }
                for segment in item["segments"]
            ],
        }
    return {
        "commands": commands,
        "inputs": inputs,
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
