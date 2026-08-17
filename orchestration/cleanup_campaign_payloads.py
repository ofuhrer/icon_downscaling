#!/usr/bin/env python3
"""Plan and apply fail-closed rolling cleanup of a segmented HICAR campaign.

The planner deliberately keeps compact validation/publication receipts.  It
only selects large forcing payloads that precede the first incomplete segment
of their season and that are not referenced by any live Slurm job.  Dry-run is
the default.  Applying a plan requires the exact plan digest and repeats all
frontier, scheduler, receipt, and path checks immediately before unlinking.
"""

from __future__ import annotations

import argparse
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Iterable

from orchestration.rd_campaign import Campaign, hours, segments, stamp
from scripts.restart_transition_provenance import (
    campaign_coordinator_commit,
    receipt_path,
    validate_receipt,
)


PLAN_SCHEMA = "hicar-campaign-payload-cleanup-plan-v1"
CAMPAIGN_JOB_NAME = re.compile(r"(?:hc|hp)-[A-Za-z0-9_-]+")
JOB_ID = re.compile(r"[0-9]+")
SHA256 = re.compile(r"[0-9a-f]{64}")


class CleanupSafetyError(RuntimeError):
    """Raised when cleanup cannot prove that every proposed unlink is safe."""


class CleanupApplyLock(AbstractContextManager["CleanupApplyLock"]):
    """Prevent concurrent cleanup processes from racing over one campaign."""

    def __init__(self, root: Path):
        self.path = root / "cleanup.lock"
        self._stream = None

    def __enter__(self) -> "CleanupApplyLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+")
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            stream.close()
            raise CleanupSafetyError(f"another cleanup process owns {self.path}") from None
        self._stream = stream
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self._stream is not None:
            fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
            self._stream.close()
            self._stream = None


@dataclass(frozen=True)
class ActiveJob:
    job_id: str
    state: str
    name: str


def _canonical_json(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def plan_sha256(payload: dict[str, Any]) -> str:
    unsigned = dict(payload)
    unsigned.pop("plan_sha256", None)
    return hashlib.sha256(_canonical_json(unsigned)).hexdigest()


def _sha256_small(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def query_active_jobs() -> list[ActiveJob]:
    """Return one authoritative user-wide active-job snapshot or fail closed."""
    user = os.environ.get("USER", "")
    if not user:
        raise CleanupSafetyError("USER is unset; cannot scope the Slurm job query")
    result = subprocess.run(
        ["squeue", "-h", "-u", user, "-o", "%A|%T|%j"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode:
        raise CleanupSafetyError(f"cannot query active Slurm jobs: {result.stderr.strip()}")
    jobs = []
    for line in result.stdout.splitlines():
        parts = line.strip().split("|", 2)
        if len(parts) != 3 or not JOB_ID.fullmatch(parts[0]):
            raise CleanupSafetyError(f"cannot parse squeue record: {line!r}")
        # squeue only returns jobs that still have live scheduler state. Keep
        # unfamiliar/new Slurm states conservative instead of maintaining a
        # potentially incomplete allow-list.
        jobs.append(ActiveJob(parts[0], parts[1].upper(), parts[2]))
    return jobs


def _job_files(root: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for path in root.glob("**/attempt-*.job"):
        if not path.is_file() or path.is_symlink():
            raise CleanupSafetyError(f"job record is not a regular file: {path}")
        value = path.read_text(encoding="utf-8").strip()
        if not JOB_ID.fullmatch(value):
            raise CleanupSafetyError(f"invalid Slurm job id in {path}: {value!r}")
        previous = result.setdefault(value, path)
        if previous != path:
            raise CleanupSafetyError(f"duplicate Slurm job id {value}: {previous}, {path}")
    return result


def _active_references(
    campaign: Campaign, active_jobs: Iterable[ActiveJob]
) -> tuple[set[Path], set[Path], set[Path], list[dict[str, str]]]:
    """Resolve live model input lists and producer targets from job records."""
    records = _job_files(campaign.root)
    model_forcing: set[Path] = set()
    model_restarts: set[Path] = set()
    producer_targets: set[Path] = set()
    mapped: list[dict[str, str]] = []
    seasons = {item.name: item for item in campaign.seasons}
    active_ids = {item.job_id for item in active_jobs}

    for job in active_jobs:
        job_file = records.get(job.job_id)
        if job_file is None:
            if CAMPAIGN_JOB_NAME.fullmatch(job.name):
                raise CleanupSafetyError(
                    f"active campaign-like job {job.job_id} ({job.name}) has no job record"
                )
            continue
        try:
            relative = job_file.relative_to(campaign.root)
        except ValueError as error:
            raise CleanupSafetyError(f"job record escapes campaign root: {job_file}") from error
        parts = relative.parts
        if parts[0] == "input_jobs":
            if len(parts) != 4:
                raise CleanupSafetyError(f"unexpected input-job record path: {job_file}")
            season = seasons.get(parts[1])
            if season is None:
                raise CleanupSafetyError(f"input job has unknown season: {job_file}")
            try:
                when = datetime.strptime(parts[2], "%Y%m%d_%H%M")
            except ValueError as error:
                raise CleanupSafetyError(f"invalid input-job timestamp: {job_file}") from error
            forcing, boundary = campaign.paths(season, when)
            producer_targets.add(forcing)
            if campaign.use_sparse_lbc:
                producer_targets.add(boundary)
            kind = "input"
        elif len(parts) == 3:
            season = seasons.get(parts[0])
            if season is None:
                raise CleanupSafetyError(f"model job has unknown season: {job_file}")
            windows = list(segments(season.start, season.end, campaign.segment_hours))
            matching = [
                index
                for index, (start, end) in enumerate(windows)
                if job_file.parent.name == f"{index:03d}_{stamp(start)}_{stamp(end)}"
            ]
            if len(matching) != 1:
                raise CleanupSafetyError(f"model job has invalid segment path: {job_file}")
            index = matching[0]
            forcing_list = job_file.parent / "forcing.txt"
            if not forcing_list.is_file() or forcing_list.is_symlink():
                raise CleanupSafetyError(f"live model job lacks regular forcing list: {job_file}")
            for raw in forcing_list.read_text(encoding="utf-8").splitlines():
                value = raw.strip()
                if len(value) < 3 or value[0] != '"' or value[-1] != '"':
                    raise CleanupSafetyError(f"invalid forcing-list entry in {forcing_list}")
                model_forcing.add(Path(value[1:-1]))
            if index:
                previous_start, previous_end = windows[index - 1]
                previous_root = (
                    campaign.root
                    / season.name
                    / f"{index - 1:03d}_{stamp(previous_start)}_{stamp(previous_end)}"
                )
                previous = _validated_completed_attempt(
                    campaign, previous_root, previous_start, previous_end
                )
                if previous is None:
                    raise CleanupSafetyError(
                        f"live model job lacks a completed predecessor: {job_file}"
                    )
                report = json.loads((previous / "segment.json").read_text(encoding="utf-8"))
                model_restarts.add(Path(report["restart"]).resolve(strict=False))
            run_restart = job_file.parent / job_file.stem / "restart"
            if run_restart.is_dir():
                for link in run_restart.glob("*.nc"):
                    if link.is_symlink():
                        try:
                            model_restarts.add(link.resolve(strict=True))
                        except FileNotFoundError as error:
                            raise CleanupSafetyError(
                                f"live model job has a broken restart link: {link}"
                            ) from error
            kind = "model"
        else:
            raise CleanupSafetyError(f"unexpected active-job record path: {job_file}")
        mapped.append(
            {
                "job_id": job.job_id,
                "kind": kind,
                "name": job.name,
                "record": str(job_file),
                "state": job.state,
            }
        )

    # A recorded active job missing from squeue is harmless: it is no longer
    # live.  Conversely, every active HICAR/hicarprep job must map above.
    if not active_ids.issuperset(item["job_id"] for item in mapped):
        raise AssertionError("internal active-job mapping inconsistency")
    return (
        model_forcing,
        model_restarts,
        producer_targets,
        sorted(mapped, key=lambda item: item["job_id"]),
    )


def _validated_completed_attempt(
    campaign: Campaign, segment_root: Path, start: datetime, end: datetime
) -> Path | None:
    attempt = campaign.completed_attempt(segment_root)
    if attempt is None:
        return None
    for name in ("segment.json", "segment_validation.json"):
        path = attempt / name
        if not path.is_file() or path.is_symlink():
            raise CleanupSafetyError(f"complete marker lacks regular {name}: {attempt}")
    try:
        segment = json.loads((attempt / "segment.json").read_text(encoding="utf-8"))
        validation = json.loads(
            (attempt / "segment_validation.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as error:
        raise CleanupSafetyError(f"cannot read completed-segment evidence: {attempt}") from error
    expected_start = start.strftime("%Y-%m-%dT%H:%M:%S")
    expected_end = end.strftime("%Y-%m-%dT%H:%M:%S")
    for payload, label in ((segment, "segment"), (validation, "validation")):
        if payload.get("start") != expected_start or payload.get("end") != expected_end:
            raise CleanupSafetyError(f"{label} times do not match segment path: {attempt}")
        if int(payload.get("forcing_records", -1)) != len(list(hours(start, end))):
            raise CleanupSafetyError(f"{label} forcing count is inconsistent: {attempt}")
    return attempt


def _season_frontiers(
    campaign: Campaign,
) -> tuple[
    dict[str, datetime | None],
    dict[str, list[Path]],
    list[dict[str, Any]],
]:
    frontiers: dict[str, datetime | None] = {}
    completed_attempts: dict[str, list[Path]] = {}
    evidence: list[dict[str, Any]] = []
    for season in campaign.seasons:
        windows = list(segments(season.start, season.end, campaign.segment_hours))
        completed: list[Path | None] = []
        for index, (start, end) in enumerate(windows):
            root = campaign.root / season.name / f"{index:03d}_{stamp(start)}_{stamp(end)}"
            completed.append(_validated_completed_attempt(campaign, root, start, end))
        first_incomplete = next((i for i, item in enumerate(completed) if item is None), None)
        if first_incomplete is not None and any(
            item is not None for item in completed[first_incomplete + 1 :]
        ):
            raise CleanupSafetyError(f"non-contiguous completed chain for season {season.name}")
        frontier = None if first_incomplete is None else windows[first_incomplete][0]
        frontiers[season.name] = frontier
        completed_attempts[season.name] = [
            item for item in completed if item is not None
        ]
        latest = None
        if first_incomplete not in (None, 0):
            latest = str(completed[first_incomplete - 1])
        elif first_incomplete is None and completed:
            latest = str(completed[-1])
        evidence.append(
            {
                "complete_segments": len(windows) if first_incomplete is None else first_incomplete,
                "first_incomplete_start": (
                    None if frontier is None else frontier.strftime("%Y-%m-%dT%H:%M:%S")
                ),
                "latest_completed_attempt": latest,
                "season": season.name,
                "total_segments": len(windows),
            }
        )
    return frontiers, completed_attempts, evidence


def _forcing_manifest(
    campaign: Campaign, season_name: str, when: datetime, forcing: Path
) -> tuple[Path, dict[str, Any], str]:
    manifest = Path(f"{forcing}.hicarprep-manifest.json")
    if not manifest.is_file() or manifest.is_symlink():
        raise CleanupSafetyError(f"forcing payload lacks regular publication manifest: {forcing}")
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CleanupSafetyError(f"cannot read forcing manifest: {manifest}") from error
    if payload.get("schema") != "hicarprep-target-forcing-manifest-v1":
        raise CleanupSafetyError(f"unsupported forcing manifest schema: {manifest}")
    if payload.get("status") != "PASS":
        raise CleanupSafetyError(f"forcing manifest does not record PASS: {manifest}")
    expected_time = when.strftime("%Y-%m-%dT%H:%M:%S")
    if payload.get("valid_time") != expected_time:
        raise CleanupSafetyError(f"forcing manifest time mismatch: {manifest}")
    output = payload.get("output")
    if not isinstance(output, dict) or Path(str(output.get("path", ""))) != forcing:
        raise CleanupSafetyError(f"forcing manifest path mismatch: {manifest}")
    digest = str(output.get("sha256", ""))
    if not SHA256.fullmatch(digest) or payload.get("forcing_sha256") != digest:
        raise CleanupSafetyError(f"forcing manifest digest mismatch: {manifest}")
    static_digest = next(
        item.static_sha256 for item in campaign.seasons if item.name == season_name
    )
    static = payload.get("static")
    if static_digest and (
        not isinstance(static, dict) or static.get("sha256") != static_digest
    ):
        raise CleanupSafetyError(f"forcing manifest static identity mismatch: {manifest}")
    return manifest, payload, _sha256_small(manifest)


def _regular_file_stat(path: Path) -> os.stat_result | None:
    try:
        result = path.lstat()
    except FileNotFoundError:
        return None
    if path.is_symlink() or not path.is_file():
        raise CleanupSafetyError(f"cleanup target is not a regular file: {path}")
    return result


def _restart_cleanup_targets(
    campaign: Campaign,
    completed_attempts: dict[str, list[Path]],
    model_restarts: set[Path],
) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[dict[str, Any]]]:
    """Validate transition receipts and select only non-latest checkpoints."""
    campaign_commit = campaign_coordinator_commit(campaign.root)
    targets: list[dict[str, Any]] = []
    blockers: list[dict[str, str]] = []
    retained: list[dict[str, Any]] = []
    for season in campaign.seasons:
        attempts = completed_attempts[season.name]
        if not attempts:
            continue
        latest_report = json.loads(
            (attempts[-1] / "segment.json").read_text(encoding="utf-8")
        )
        latest = Path(latest_report["restart"])
        latest_stat = _regular_file_stat(latest)
        if latest_stat is None or latest_stat.st_size <= 0:
            raise CleanupSafetyError(
                f"latest completed checkpoint must remain present and nonempty: {latest}"
            )
        retained.append(
            {
                "bytes": latest_stat.st_size,
                "path": str(latest),
                "reason": "latest_completed_checkpoint",
                "season": season.name,
            }
        )

        for predecessor_index, (predecessor, successor) in enumerate(
            zip(attempts, attempts[1:])
        ):
            receipt = receipt_path(successor)
            try:
                evidence = validate_receipt(
                    receipt,
                    predecessor,
                    successor,
                    season=season.name,
                    predecessor_index=predecessor_index,
                    successor_index=predecessor_index + 1,
                    campaign_commit=campaign_commit,
                )
            except (OSError, ValueError, KeyError, TypeError) as error:
                raise CleanupSafetyError(
                    f"restart transition is not durably attested: {receipt}: {error}"
                ) from error
            restart = evidence["restart"]
            terminal_record = restart["predecessor_terminal"]
            input_record = restart["successor_input_link"]
            terminal = Path(terminal_record["path"])
            input_link = Path(input_record["path"])
            terminal_stat = _regular_file_stat(terminal)
            input_stat = input_link.lstat() if os.path.lexists(input_link) else None
            if input_stat is not None and not input_link.is_symlink():
                raise CleanupSafetyError(
                    f"successor restart input is not a symlink: {input_link}"
                )
            if terminal_stat is None:
                if input_stat is not None:
                    raise CleanupSafetyError(
                        f"deleted predecessor restart left a successor link: {input_link}"
                    )
                # Both large payload and staging link were already pruned. The
                # receipt validation above still proves the transition.
                continue
            if terminal_stat.st_size <= 0:
                raise CleanupSafetyError(f"restart cleanup target is empty: {terminal}")
            if terminal.resolve(strict=True) in model_restarts:
                blockers.append(
                    {"path": str(terminal), "reason": "referenced_by_live_model"}
                )
                continue
            targets.append(
                {
                    "bytes": terminal_stat.st_size,
                    "device": terminal_stat.st_dev,
                    "inode": terminal_stat.st_ino,
                    "input_link": str(input_link),
                    "input_link_device": None if input_stat is None else input_stat.st_dev,
                    "input_link_inode": None if input_stat is None else input_stat.st_ino,
                    "input_link_mtime_ns": (
                        None if input_stat is None else input_stat.st_mtime_ns
                    ),
                    "kind": "restart",
                    "mtime_ns": terminal_stat.st_mtime_ns,
                    "path": str(terminal),
                    "predecessor_attempt": str(predecessor),
                    "predecessor_index": predecessor_index,
                    "receipt": str(receipt),
                    "receipt_sha256": _sha256_small(receipt),
                    "restart_sha256": terminal_record["sha256"],
                    "season": season.name,
                    "successor_attempt": str(successor),
                    "successor_index": predecessor_index + 1,
                }
            )
    return targets, blockers, retained


def build_plan(campaign: Campaign, active_jobs: Iterable[ActiveJob]) -> dict[str, Any]:
    """Build a conservative cleanup plan without modifying the filesystem."""
    if campaign.full_season_input_lists:
        raise CleanupSafetyError("rolling cleanup requires exact segment-local forcing lists")
    if campaign.use_sparse_lbc:
        raise CleanupSafetyError("rolling cleanup for sparse-LBC pairs is not implemented")
    if not campaign.root.is_absolute() or not campaign.forcing.is_absolute():
        raise CleanupSafetyError("campaign root and forcing directory must be absolute")
    if campaign.root == Path("/") or campaign.forcing == Path("/"):
        raise CleanupSafetyError("refusing broad cleanup root")
    season_names = [item.name for item in campaign.seasons]
    if len(set(season_names)) != len(season_names):
        raise CleanupSafetyError("campaign season names must be unique")

    model_forcing, model_restarts, producer_targets, mapped_jobs = _active_references(
        campaign, active_jobs
    )
    frontiers, completed_attempts, frontier_evidence = _season_frontiers(campaign)
    targets: list[dict[str, Any]] = []
    blockers: list[dict[str, str]] = []

    forcing_root = campaign.forcing.resolve()
    for season in campaign.seasons:
        expected_parent = (campaign.forcing / season.name).resolve()
        try:
            expected_parent.relative_to(forcing_root)
        except ValueError as error:
            raise CleanupSafetyError(f"season forcing directory escapes forcing root: {season.name}") from error
        frontier = frontiers[season.name]
        final = season.end if frontier is None else frontier
        for when in hours(season.start, final):
            if frontier is not None and when >= frontier:
                continue
            forcing, _ = campaign.paths(season, when)
            if forcing.parent.resolve() != expected_parent:
                raise CleanupSafetyError(f"forcing path has unexpected parent: {forcing}")
            if forcing.name != f"rea_l_hicar_{stamp(when)}.nc":
                raise CleanupSafetyError(f"forcing path has unexpected name: {forcing}")
            stat = _regular_file_stat(forcing)
            ready = Path(f"{forcing}.ready")
            ready_stat = _regular_file_stat(ready)
            if ready_stat is not None and ready_stat.st_size != 0:
                raise CleanupSafetyError(f"forcing ready marker is not empty: {ready}")
            if stat is None and ready_stat is None:
                continue
            if forcing in model_forcing:
                blockers.append({"path": str(forcing), "reason": "referenced_by_live_model"})
                continue
            if forcing in producer_targets:
                blockers.append({"path": str(forcing), "reason": "target_of_live_producer"})
                continue
            manifest, manifest_payload, manifest_digest = _forcing_manifest(
                campaign, season.name, when, forcing
            )
            targets.append(
                {
                    "bytes": 0 if stat is None else stat.st_size,
                    "device": None if stat is None else stat.st_dev,
                    "forcing_sha256": manifest_payload["forcing_sha256"],
                    "inode": None if stat is None else stat.st_ino,
                    "kind": "forcing",
                    "manifest": str(manifest),
                    "manifest_sha256": manifest_digest,
                    "mtime_ns": None if stat is None else stat.st_mtime_ns,
                    "path": str(forcing),
                    "ready": str(ready),
                    "season": season.name,
                    "valid_time": when.strftime("%Y-%m-%dT%H:%M:%S"),
                }
            )

    restart_targets, restart_blockers, retained_restarts = _restart_cleanup_targets(
        campaign, completed_attempts, model_restarts
    )
    targets.extend(restart_targets)
    blockers.extend(restart_blockers)

    config_digest = _sha256_small(campaign.config_path)
    plan: dict[str, Any] = {
        "active_jobs": mapped_jobs,
        "blockers": sorted(blockers, key=lambda item: (item["path"], item["reason"])),
        "campaign_config": str(campaign.config_path),
        "campaign_config_sha256": config_digest,
        "campaign_root": str(campaign.root),
        "forcing_root": str(campaign.forcing),
        "frontiers": frontier_evidence,
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "schema": PLAN_SCHEMA,
        "retained_restarts": retained_restarts,
        "summary": {
            "blocked_count": len(blockers),
            "target_bytes": sum(item["bytes"] for item in targets),
            "target_count": len(targets),
        },
        "targets": sorted(targets, key=lambda item: item["path"]),
    }
    plan["plan_sha256"] = plan_sha256(plan)
    return plan


def _target_key(item: dict[str, Any]) -> tuple[str, str]:
    return str(item.get("kind")), str(item.get("path"))


def _validate_loaded_plan(payload: dict[str, Any], expected_digest: str) -> None:
    if payload.get("schema") != PLAN_SCHEMA:
        raise CleanupSafetyError("cleanup plan has an unsupported schema")
    actual = plan_sha256(payload)
    if payload.get("plan_sha256") != actual or expected_digest != actual:
        raise CleanupSafetyError(
            f"cleanup plan digest mismatch: expected {expected_digest}, recomputed {actual}"
        )
    if not SHA256.fullmatch(expected_digest):
        raise CleanupSafetyError("--apply requires a lowercase SHA-256 digest")


def apply_plan(
    campaign: Campaign,
    planned: dict[str, Any],
    expected_digest: str,
    active_jobs: Iterable[ActiveJob],
) -> dict[str, Any]:
    """Recompute safety and unlink only an unchanged, still-safe planned subset."""
    with CleanupApplyLock(campaign.root):
        _validate_loaded_plan(planned, expected_digest)
        if planned.get("campaign_config") != str(campaign.config_path):
            raise CleanupSafetyError("cleanup plan belongs to a different campaign config")
        if planned.get("campaign_config_sha256") != _sha256_small(campaign.config_path):
            raise CleanupSafetyError("campaign config changed after the cleanup dry-run")
        if planned.get("blockers"):
            raise CleanupSafetyError("refusing to apply a plan that recorded blockers")

        current = build_plan(campaign, active_jobs)
        if current.get("blockers"):
            raise CleanupSafetyError("live cleanup recheck found blockers")
        current_by_key = {_target_key(item): item for item in current["targets"]}
        planned_targets = planned.get("targets")
        if not isinstance(planned_targets, list):
            raise CleanupSafetyError("cleanup plan targets must be a list")
        for target in planned_targets:
            if not isinstance(target, dict):
                raise CleanupSafetyError("cleanup plan contains a non-object target")
            key = _target_key(target)
            live = current_by_key.get(key)
            if live is None:
                raise CleanupSafetyError(f"planned target is no longer present and safe: {key[1]}")
            if target != live:
                changed = sorted(
                    key for key in set(target) | set(live) if target.get(key) != live.get(key)
                )
                raise CleanupSafetyError(
                    f"planned target changed ({', '.join(changed)}): {key[1]}"
                )

        deleted = []
        marker_only_repairs = []
        restart_links_removed = []
        deleted_bytes = 0
        for target in planned_targets:
            path = Path(target["path"])
            if target["kind"] == "forcing":
                ready = Path(target["ready"])
                # Invalidate publication first. A crash can leave an unready
                # payload, which cleanup or regeneration can safely recover.
                ready.unlink(missing_ok=True)
                _fsync_directory(path.parent)
                if path.exists():
                    path.unlink()
                    deleted.append(str(path))
                    deleted_bytes += int(target["bytes"])
                else:
                    marker_only_repairs.append(str(path))
                _fsync_directory(path.parent)
            elif target["kind"] == "restart":
                input_link = Path(target["input_link"])
                # Remove the staging symlink first. If interrupted, the
                # receipt can still validate the retained terminal payload.
                if os.path.lexists(input_link):
                    if not input_link.is_symlink():
                        raise CleanupSafetyError(
                            f"restart input changed before unlink: {input_link}"
                        )
                    input_link.unlink()
                    restart_links_removed.append(str(input_link))
                    _fsync_directory(input_link.parent)
                path.unlink()
                deleted.append(str(path))
                deleted_bytes += int(target["bytes"])
            else:
                raise CleanupSafetyError(f"unsupported cleanup target kind: {target['kind']}")
            _fsync_directory(path.parent)

    return {
        "applied_plan_sha256": expected_digest,
        "deleted_bytes": deleted_bytes,
        "deleted_count": len(deleted),
        "deleted_paths": deleted,
        "manifests_retained": [
            item["manifest"] for item in planned_targets if item["kind"] == "forcing"
        ],
        "marker_only_repair_count": len(marker_only_repairs),
        "marker_only_repair_paths": marker_only_repairs,
        "restart_links_removed": restart_links_removed,
        "restart_receipts_retained": [
            item["receipt"] for item in planned_targets if item["kind"] == "restart"
        ],
        "schema": "hicar-campaign-payload-cleanup-result-v1",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument(
        "--plan-output", type=Path, help="atomically write the dry-run plan as JSON"
    )
    parser.add_argument(
        "--plan-file", type=Path, help="dry-run plan to authenticate and apply"
    )
    parser.add_argument(
        "--apply",
        metavar="PLAN_SHA256",
        help="apply --plan-file after live revalidation; omitted means dry-run",
    )
    args = parser.parse_args()
    campaign = Campaign(args.config)

    try:
        jobs = query_active_jobs()
        if args.apply is None:
            if args.plan_file is not None:
                parser.error("--plan-file is only valid with --apply")
            plan = build_plan(campaign, jobs)
            if args.plan_output is not None:
                _atomic_json(args.plan_output, plan)
            print(json.dumps(plan, indent=2, sort_keys=True))
            return 0
        if args.plan_file is None:
            parser.error("--apply requires --plan-file")
        if args.plan_output is not None:
            parser.error("--plan-output cannot be combined with --apply")
        planned = json.loads(args.plan_file.read_text(encoding="utf-8"))
        result = apply_plan(campaign, planned, args.apply, jobs)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (CleanupSafetyError, OSError, json.JSONDecodeError) as error:
        print(f"cleanup refused: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
