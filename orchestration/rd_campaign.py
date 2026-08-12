#!/usr/bin/env python3
"""Small filesystem-driven controller for restartable seasonal R&D runs."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import subprocess
import time
from typing import Iterator


TIME = "%Y-%m-%dT%H:%M:%S"


def parse_time(value: str) -> datetime:
    return datetime.strptime(value, TIME)


def stamp(value: datetime) -> str:
    return value.strftime("%Y%m%d_%H%M")


def hours(start: datetime, end: datetime):
    value = start.replace(minute=0, second=0, microsecond=0)
    final = end.replace(minute=0, second=0, microsecond=0)
    if final < end:
        final += timedelta(hours=1)
    while value <= final:
        yield value
        value += timedelta(hours=1)


def segments(start: datetime, end: datetime, length_hours: float):
    value = start
    while value < end:
        following = min(value + timedelta(hours=length_hours), end)
        yield value, following
        value = following


def run(command: list[str]) -> str:
    return subprocess.run(
        command, check=True, text=True, stdout=subprocess.PIPE
    ).stdout.strip()


def source_identity(path: Path) -> dict[str, object]:
    commit = run(["git", "-C", str(path), "rev-parse", "HEAD"])
    status = run(["git", "-C", str(path), "status", "--short"])
    digest = hashlib.sha256()
    digest.update(
        subprocess.run(
            ["git", "-C", str(path), "diff", "--binary", "HEAD"],
            check=True,
            stdout=subprocess.PIPE,
        ).stdout
    )
    untracked = run(
        ["git", "-C", str(path), "ls-files", "--others", "--exclude-standard"]
    ).splitlines()
    for relative in sorted(untracked):
        digest.update(relative.encode() + b"\0")
        digest.update((path / relative).read_bytes())
    return {
        "commit": commit,
        "dirty": bool(status),
        "working_tree_sha256": digest.hexdigest(),
    }


def slurm_state(job_file: Path) -> str:
    if not job_file.is_file():
        return "NOT_SUBMITTED"
    job = job_file.read_text().strip()
    active = subprocess.run(
        ["squeue", "-h", "-j", job, "-o", "%T"], text=True, stdout=subprocess.PIPE
    ).stdout.strip()
    if active:
        return active.splitlines()[0]
    result = subprocess.run(
        ["sacct", "-n", "-X", "-j", job, "-o", "State", "--starttime", "now-7days"],
        text=True, stdout=subprocess.PIPE,
    ).stdout.strip()
    return result.split()[0].split("+")[0] if result else "UNKNOWN"


def slurm_partition(job_file: Path) -> str:
    job = job_file.read_text().strip()
    active = subprocess.run(
        ["squeue", "-h", "-j", job, "-o", "%P"], text=True, stdout=subprocess.PIPE
    ).stdout.strip()
    if active:
        return active.splitlines()[0]
    result = subprocess.run(
        ["sacct", "-n", "-X", "-j", job, "-o", "Partition", "--starttime", "now-7days"],
        text=True, stdout=subprocess.PIPE,
    ).stdout.strip()
    return result.split()[0] if result else "unknown"


def submitted_attempt(directory: Path, maximum: int) -> tuple[int, str] | None:
    for attempt in range(maximum, 0, -1):
        job_file = directory / f"attempt-{attempt}.job"
        if job_file.is_file():
            return attempt, slurm_state(job_file)
    return None


def validate_partition(partition: str) -> None:
    description = run(["scontrol", "show", "partition", partition, "-o"])
    fields = dict(
        item.split("=", 1) for item in description.split() if "=" in item
    )
    allowed = set(fields.get("AllowGroups", "").split(","))
    if not ({"ALL", "s83"} & allowed):
        raise RuntimeError(
            f"partition {partition} is not currently allowed for exact group s83"
        )


def submit(
    script: Path,
    environment: dict[str, str],
    job_name: str,
    *,
    partition: str,
    sbatch_options: tuple[str, ...] = (),
) -> str:
    validate_partition(partition)
    exports = ["ALL", *(f"{key}={value}" for key, value in environment.items())]
    return run(
        [
            "sbatch", "--parsable", "--partition", partition,
            "--job-name", job_name, *sbatch_options,
            "--export=" + ",".join(exports), str(script),
        ]
    ).split(";")[0]


@dataclass(frozen=True)
class Season:
    name: str
    start: datetime
    end: datetime
    static: Path


class Campaign:
    def __init__(self, config_path: Path):
        self.config_path = config_path.resolve()
        self.config = json.loads(config_path.read_text())
        self.root = Path(self.config["root"])
        self.repo = Path(self.config["repo_root"])
        self.forcing = Path(self.config["forcing_dir"])
        self.segment_hours = float(self.config.get("segment_hours", 24))
        self.max_attempts = int(self.config.get("max_attempts", 4))
        self.input_partitions = self.config.get("input_partitions", ["pp-short"])
        self.max_active_inputs = int(self.config.get("max_active_inputs", 2))
        self.input_cpus = int(self.config.get("input_cpus", 4))
        self.input_column_workers = int(
            self.config.get("input_column_workers", 1)
        )
        self.input_memory = str(self.config.get("input_memory", "64G"))
        self.input_time = str(self.config.get("input_time", "01:00:00"))
        self.input_exclusive = bool(self.config.get("input_exclusive", False))
        self.model_nodes = int(self.config.get("model_nodes", 2))
        self.model_time = str(self.config.get("model_time", "06:00:00"))
        self.radiation_update_interval = float(
            self.config.get("radiation_update_interval", 600.0)
        )
        self.radiation_scheme = str(
            self.config.get("radiation_scheme", "rrtmgp")
        ).lower()
        self.use_sparse_lbc = bool(self.config.get("use_sparse_lbc", True))
        self.full_season_input_lists = bool(
            self.config.get("full_season_input_lists", False)
        )
        lookahead = self.config.get("input_lookahead_segments")
        if lookahead is not None and (
            isinstance(lookahead, bool) or not isinstance(lookahead, int)
        ):
            raise ValueError("input_lookahead_segments must be a non-negative integer")
        self.input_lookahead_segments = lookahead
        self.seasons = [
            Season(item["name"], parse_time(item["start"]), parse_time(item["end"]), Path(item["static"]))
            for item in self.config["seasons"]
        ]
        if (
            self.segment_hours <= 0
            or self.max_attempts <= 0
            or self.max_active_inputs <= 0
            or self.input_cpus <= 0
            or self.input_column_workers <= 0
            or self.model_nodes <= 0
            or self.radiation_update_interval <= 0
        ):
            raise ValueError(
                "segment length, attempts, resource counts, and radiation cadence "
                "must be positive"
            )
        if not self.input_partitions:
            raise ValueError("input_partitions must not be empty")
        if self.radiation_scheme not in {"rrtmgp", "rrtmg"}:
            raise ValueError("radiation_scheme must be rrtmgp or rrtmg")
        if self.input_column_workers > self.input_cpus:
            raise ValueError("input_column_workers must not exceed input_cpus")
        if (
            self.input_lookahead_segments is not None
            and self.input_lookahead_segments < 0
        ):
            raise ValueError("input_lookahead_segments must be a non-negative integer")
        if self.input_lookahead_segments is not None and self.full_season_input_lists:
            raise ValueError(
                "bounded input look-ahead requires segment-local input lists"
            )

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.forcing.mkdir(parents=True, exist_ok=True)
        coordinator_source = source_identity(self.repo)
        hicar_source = source_identity(self.repo / "HICAR")
        if coordinator_source["dirty"] or hicar_source["dirty"]:
            raise RuntimeError("campaigns require clean, pinned coordinator and HICAR worktrees")
        provenance = Path(self.config["hicar_build_provenance"])
        if not provenance.is_file() or not Path(f"{provenance}.ready").is_file():
            raise RuntimeError(f"missing complete HICAR build provenance: {provenance}")
        build = dict(
            line.split("=", 1) for line in provenance.read_text().splitlines()
            if "=" in line and not line.startswith("---")
        )
        if build.get("source_commit") != hicar_source["commit"]:
            raise RuntimeError("HICAR executable was not built from the pinned submodule commit")
        empty_diff = hashlib.sha256(b"").hexdigest()
        if build.get("source_diff_sha256", empty_diff) != empty_diff:
            raise RuntimeError("campaign executable was built from an uncommitted HICAR patch")
        manifest = self.root / "campaign.json"
        if not manifest.exists():
            payload = {
                "config": self.config,
                "coordinator_source": coordinator_source,
                "hicar_source": hicar_source,
                "hicar_build_provenance": str(provenance),
            }
            manifest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    def paths(self, season: Season, when: datetime) -> tuple[Path, Path]:
        # LBC products embed the complete runtime-domain hash, including the
        # season-specific land initialization. Never alias products by valid
        # time alone.
        forcing = self.forcing / season.name / f"rea_l_hicar_{stamp(when)}.nc"
        return forcing, forcing.with_suffix(".lbc.nc")

    def input_candidates(
        self, *, bounded: bool = True
    ) -> Iterator[tuple[Season, datetime, Path]]:
        """Yield unbounded or segment-bounded input work in deterministic order."""
        if self.input_lookahead_segments is None or not bounded:
            unique: dict[tuple[str, datetime], tuple[Season, Path]] = {}
            for season in self.seasons:
                for when in hours(season.start, season.end):
                    unique.setdefault((season.name, when), (season, season.static))
            for key in sorted(unique):
                season, static = unique[key]
                yield season, key[1], static
            return

        # Build one ordered, de-duplicated horizon per active season. Adjacent
        # segments share their endpoint forcing record, so preserve it only
        # once. Interleave the season horizons to prevent a long first season
        # from occupying the complete submission cap.
        horizons: list[list[tuple[Season, datetime, Path]]] = []
        for season in self.seasons:
            windows = list(segments(season.start, season.end, self.segment_hours))
            first_incomplete = next(
                (
                    index
                    for index, (start, end) in enumerate(windows)
                    if self.completed_attempt(
                        self.root
                        / season.name
                        / f"{index:03d}_{stamp(start)}_{stamp(end)}"
                    )
                    is None
                ),
                None,
            )
            if first_incomplete is None:
                continue
            final = min(
                len(windows),
                first_incomplete + self.input_lookahead_segments + 1,
            )
            seen: set[datetime] = set()
            horizon: list[tuple[Season, datetime, Path]] = []
            for start, end in windows[first_incomplete:final]:
                for when in hours(start, end):
                    if when not in seen:
                        seen.add(when)
                        horizon.append((season, when, season.static))
            horizons.append(horizon)

        for position in range(max((len(item) for item in horizons), default=0)):
            for horizon in horizons:
                if position < len(horizon):
                    yield horizon[position]

    def prepare_inputs(self, *, bounded: bool = True) -> int:
        input_jobs = self.root / "input_jobs"
        input_jobs.mkdir(parents=True, exist_ok=True)
        active_by_partition = {partition: 0 for partition in self.input_partitions}
        per_partition = max(1, self.max_active_inputs // len(self.input_partitions))
        submitted = 0
        for season, when, static in self.input_candidates(bounded=bounded):
            season_name = season.name
            forcing, boundary = self.paths(season, when)
            if Path(f"{forcing}.ready").is_file() and (
                not self.use_sparse_lbc or Path(f"{boundary}.ready").is_file()
            ):
                continue
            directory = input_jobs / season_name / stamp(when)
            directory.mkdir(parents=True, exist_ok=True)
            previous = submitted_attempt(directory, self.max_attempts)
            if previous and previous[1] in {"PENDING", "RUNNING", "CONFIGURING", "COMPLETING"}:
                partition = slurm_partition(directory / f"attempt-{previous[0]}.job")
                active_by_partition[partition] = active_by_partition.get(partition, 0) + 1
                continue
            attempt = 1 if previous is None else previous[0] + 1
            if attempt > self.max_attempts:
                raise RuntimeError(f"input {when.strftime(TIME)} failed {self.max_attempts} times")
            if sum(active_by_partition.values()) >= self.max_active_inputs:
                continue
            partition = next(
                (item for item in self.input_partitions if active_by_partition[item] < per_partition),
                None,
            )
            if partition is None:
                continue
            job = submit(
                self.repo / "case_studies/swiss_200m/scripts/produce_hicarprep_target_record_balfrin.sbatch",
                {
                    "REPO_ROOT": str(self.repo),
                    "VALID_TIME": when.strftime(TIME),
                    "HICAR_FORCING_OUTPUT": str(forcing),
                    "HICAR_STATIC_DOMAIN": str(static),
                    "HICARPREP_RBF_WEIGHTS": self.config["rbf_weights"],
                    "HICARPREP_VECTOR_WEIGHTS": self.config.get("vector_weights", ""),
                    "HICARPREP_COLUMN_WORKERS": str(self.input_column_workers),
                    "HICARPREP_WRITE_LBC": "1" if self.use_sparse_lbc else "0",
                    "HICAR_PYTHON": self.config["python"],
                },
                "hp-" + when.strftime("%m%d%H"),
                partition=partition,
                sbatch_options=(
                    f"--cpus-per-task={self.input_cpus}",
                    f"--mem={self.input_memory}",
                    f"--time={self.input_time}",
                    *(("--exclusive",) if self.input_exclusive else ()),
                ),
            )
            (directory / f"attempt-{attempt}.job").write_text(job + "\n")
            active_by_partition[partition] += 1
            submitted += 1
        return submitted

    def inputs_complete(self) -> bool:
        return all(
            Path(f"{forcing}.ready").is_file()
            and (not self.use_sparse_lbc or Path(f"{boundary}.ready").is_file())
            for season in self.seasons
            for when in hours(season.start, season.end)
            for forcing, boundary in (self.paths(season, when),)
        )

    def completed_attempt(self, segment_root: Path) -> Path | None:
        for path in sorted(segment_root.glob("attempt-*"), reverse=True):
            if (path / "segment.complete").is_file():
                return path
        return None

    def segment_input_plan(
        self, season: Season, segment_root: Path, start: datetime, end: datetime
    ) -> tuple[list[tuple[Path, Path]], Path, Path]:
        """Return the records and list paths used by one model segment."""
        list_start = season.start if self.full_season_input_lists else start
        list_end = season.end if self.full_season_input_lists else end
        records = [self.paths(season, when) for when in hours(list_start, list_end)]
        list_root = self.root / season.name if self.full_season_input_lists else segment_root
        return records, list_root / "forcing.txt", list_root / "lbc.txt"

    def submit_segments(self) -> int:
        submitted = 0
        for season in self.seasons:
            previous_restart: Path | None = None
            for index, (start, end) in enumerate(segments(season.start, season.end, self.segment_hours)):
                segment_root = self.root / season.name / f"{index:03d}_{stamp(start)}_{stamp(end)}"
                segment_root.mkdir(parents=True, exist_ok=True)
                completed = self.completed_attempt(segment_root)
                if completed:
                    report = json.loads((completed / "segment.json").read_text())
                    previous_restart = Path(report["restart"])
                    continue
                # A chain is intentionally serial; another season may proceed independently.
                if index and previous_restart is None:
                    break
                records, forcing_list, boundary_list = self.segment_input_plan(
                    season, segment_root, start, end
                )
                if not all(
                    Path(f"{forcing}.ready").is_file()
                    and (
                        not self.use_sparse_lbc
                        or Path(f"{boundary}.ready").is_file()
                    )
                    for forcing, boundary in records
                ):
                    break
                forcing_list.write_text("".join(f'"{item[0]}"\n' for item in records))
                if self.use_sparse_lbc:
                    boundary_list.write_text("".join(f'"{item[1]}"\n' for item in records))
                else:
                    boundary_list.unlink(missing_ok=True)
                previous = submitted_attempt(segment_root, self.max_attempts)
                if previous and previous[1] in {"PENDING", "RUNNING", "CONFIGURING", "COMPLETING"}:
                    break
                attempt = 1 if previous is None else previous[0] + 1
                if attempt > self.max_attempts:
                    raise RuntimeError(f"segment {season.name}/{index} failed {self.max_attempts} times")
                run_dir = segment_root / f"attempt-{attempt}"
                run_dir.mkdir(exist_ok=True)
                environment = {
                    "REPO_ROOT": str(self.repo),
                    "HICAR_EXE": self.config["hicar_executable"],
                    "HICAR_SUPPORT_DIR": self.config["hicar_support_dir"],
                    "HICAR_STATIC_FILE": str(season.static),
                    "HICAR_PYTHON": self.config["python"],
                    "FORCING_FILE_LIST": str(forcing_list),
                    "SPARSE_LBC_FILE_LIST": (
                        str(boundary_list) if self.use_sparse_lbc else ""
                    ),
                    "SEGMENT_START": start.strftime(TIME),
                    "SEGMENT_END": end.strftime(TIME),
                    "SEGMENT_RUN_DIR": str(run_dir),
                    "RESTART_INPUT": str(previous_restart or ""),
                    "OUTPUT_PROFILE": self.config.get("output_profile", "evaluation"),
                    "OUTPUT_INTERVAL": str(self.config.get("output_interval", 600)),
                    "HICAR_RADIATION_UPDATE_INTERVAL": str(
                        self.radiation_update_interval
                    ),
                    "HICAR_RADIATION_SCHEME": self.radiation_scheme,
                    "HICAR_DISABLE_SX": "1" if self.config.get("disable_sx", False) else "0",
                    "HICAR_ALPHA_CONST": str(self.config.get("alpha_const", -1.0)),
                    "HICAR_ALLOW_INPUT_SUPERSET": (
                        "1" if self.full_season_input_lists else "0"
                    ),
                    "HICAR_ACC_SYNCHRONOUS": (
                        "1" if self.config.get("acc_synchronous", False) else "0"
                    ),
                }
                job = submit(
                    self.repo / "case_studies/swiss_200m/scripts/run_rea_l_stream_chunk_balfrin.sbatch",
                    environment, f"hc-{season.name[:3]}-{index:03d}-a{attempt}",
                    partition="preemptible",
                    sbatch_options=(
                        f"--nodes={self.model_nodes}",
                        f"--time={self.model_time}",
                        "--no-requeue",
                    ),
                )
                (segment_root / f"attempt-{attempt}.job").write_text(job + "\n")
                submitted += 1
                break
        return submitted

    def status(self) -> dict:
        result = {}
        for season in self.seasons:
            total = complete = 0
            for index, (start, end) in enumerate(segments(season.start, season.end, self.segment_hours)):
                total += 1
                root = self.root / season.name / f"{index:03d}_{stamp(start)}_{stamp(end)}"
                complete += self.completed_attempt(root) is not None
            result[season.name] = {"complete_segments": complete, "total_segments": total}
        return result

    def complete(self) -> bool:
        return all(item["complete_segments"] == item["total_segments"] for item in self.status().values())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--watch", action="store_true", help="poll and retry until all chains finish")
    parser.add_argument(
        "--inputs-only",
        action="store_true",
        help="prepare forcing but do not submit model segments",
    )
    parser.add_argument("--poll-seconds", type=int, default=60)
    args = parser.parse_args()
    campaign = Campaign(args.config)
    campaign.initialize()
    while True:
        # --inputs-only retains its established meaning of preparing the full
        # campaign. A bounded horizon advances only through completed model
        # segments and would otherwise stall forever in inputs-only mode.
        campaign.prepare_inputs(bounded=not args.inputs_only)
        if not args.inputs_only:
            campaign.submit_segments()
        print(json.dumps(campaign.status(), indent=2, sort_keys=True), flush=True)
        done = campaign.inputs_complete() if args.inputs_only else campaign.complete()
        if done or not args.watch:
            return 0
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
