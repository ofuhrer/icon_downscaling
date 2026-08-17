#!/usr/bin/env python3
"""Create and validate compact evidence for a HICAR restart transition.

The receipt is written into the completed successor attempt.  Once it exists
and validates, the predecessor's terminal restart and the successor's staged
input symlink are no longer needed as provenance payloads.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
from tempfile import NamedTemporaryFile


SCHEMA = "hicar.restart-transition/v1"
RECEIPT_NAME = "restart_transition.json"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")


def digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def parse_time(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"timestamp must be a string, got {type(value).__name__}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"invalid timestamp {value!r}") from error
    return parsed.replace(tzinfo=None)


def _read_json_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read JSON object {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def _segment_report(attempt: Path) -> tuple[Path, dict]:
    attempt = attempt.resolve()
    complete = attempt / "segment.complete"
    report_path = attempt / "segment.json"
    if not complete.is_file():
        raise ValueError(f"{attempt}: segment.complete is absent")
    report = _read_json_object(report_path)
    for key in ("start", "end", "restart"):
        if key not in report:
            raise ValueError(f"{report_path}: missing {key!r}")
    parse_time(report["start"])
    parse_time(report["end"])
    return report_path, report


def _commit(value: object, label: str) -> str:
    if not isinstance(value, str) or COMMIT_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase 40-character Git commit")
    return value


def _sha(value: object, label: str) -> str:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def source_commit(repo_root: Path) -> str:
    repo_root = repo_root.resolve()
    commit = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "-C", str(repo_root), "status", "--porcelain"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    if dirty:
        raise ValueError(f"attestor repository is dirty: {repo_root}")
    return _commit(commit, "attestor commit")


def campaign_coordinator_commit(campaign_root: Path) -> str:
    manifest_path = campaign_root.resolve() / "campaign.json"
    manifest = _read_json_object(manifest_path)
    try:
        commit = manifest["coordinator_source"]["commit"]
    except (KeyError, TypeError) as error:
        raise ValueError(f"{manifest_path}: coordinator source commit is absent") from error
    return _commit(commit, "campaign coordinator commit")


def receipt_path(successor_attempt: Path) -> Path:
    return successor_attempt.resolve() / RECEIPT_NAME


def _identity(attempt: Path, index: int, report_path: Path, report: dict) -> dict:
    return {
        "index": index,
        "segment": attempt.parent.name,
        "attempt": attempt.name,
        "start": report["start"],
        "end": report["end"],
        "segment_json_sha256": digest(report_path),
    }


def _expected_segment_name(index: int, report: dict) -> str:
    start = parse_time(report["start"])
    end = parse_time(report["end"])
    return f"{index:03d}_{start:%Y%m%d_%H%M}_{end:%Y%m%d_%H%M}"


def _validate_attempt_location(attempt: Path, season: str, index: int, report: dict) -> None:
    if attempt.parent.parent.name != season or attempt.parent.name != _expected_segment_name(
        index, report
    ):
        raise ValueError(f"{attempt}: attempt path does not match season/index/time identity")


def _terminal_path(attempt: Path, report: dict) -> Path:
    terminal = Path(report["restart"])
    if not terminal.is_absolute():
        raise ValueError("segment terminal restart path must be absolute")
    if terminal.parent.resolve() != (attempt / "restart").resolve() or terminal.suffix != ".nc":
        raise ValueError("segment terminal restart path is outside its restart directory")
    return terminal


def _matching_restart_link(successor_attempt: Path, target: Path) -> Path:
    restart_dir = successor_attempt / "restart"
    links = [
        path
        for path in restart_dir.glob("*.nc")
        if path.is_symlink() and path.resolve(strict=True) == target
    ]
    if len(links) != 1:
        raise ValueError(
            f"{successor_attempt}: expected exactly one restart symlink to {target}, "
            f"found {len(links)}"
        )
    return links[0]


def build_receipt(
    predecessor_attempt: Path,
    successor_attempt: Path,
    *,
    season: str,
    predecessor_index: int,
    successor_index: int,
    campaign_commit: str,
    attestor_commit: str,
) -> dict:
    """Build a receipt only after checking both completed attempts and live bytes."""
    predecessor_attempt = predecessor_attempt.resolve()
    successor_attempt = successor_attempt.resolve()
    if not isinstance(season, str) or not season:
        raise ValueError("season must be a non-empty string")
    predecessor_index = _integer(predecessor_index, "predecessor index")
    successor_index = _integer(successor_index, "successor index")
    if successor_index != predecessor_index + 1:
        raise ValueError("successor index must immediately follow predecessor index")
    campaign_commit = _commit(campaign_commit, "campaign coordinator commit")
    attestor_commit = _commit(attestor_commit, "attestor commit")

    first_path, first = _segment_report(predecessor_attempt)
    second_path, second = _segment_report(successor_attempt)
    _validate_attempt_location(predecessor_attempt, season, predecessor_index, first)
    _validate_attempt_location(successor_attempt, season, successor_index, second)
    checkpoint = parse_time(first["end"])
    if parse_time(second["start"]) != checkpoint:
        raise ValueError("successor start does not equal predecessor checkpoint")
    terminal = _terminal_path(predecessor_attempt, first)
    if not terminal.is_file() or terminal.is_symlink():
        raise ValueError(f"{terminal}: predecessor terminal restart is not a regular file")
    terminal = terminal.resolve(strict=True)
    link = _matching_restart_link(successor_attempt, terminal)
    link_target = link.resolve(strict=True)
    terminal_size = terminal.stat().st_size
    link_size = link.stat().st_size
    terminal_sha = digest(terminal)
    # Resolving the symlink to this exact regular file establishes identical
    # bytes.  Do not reread a multi-gigabyte restart through the symlink.
    if link_target != terminal or link_size != terminal_size:
        raise ValueError("successor restart input is not byte-identical to predecessor restart")

    return {
        "schema": SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "transition": {
            "season": season,
            "checkpoint_time": first["end"],
            "predecessor": _identity(
                predecessor_attempt, predecessor_index, first_path, first
            ),
            "successor": _identity(successor_attempt, successor_index, second_path, second),
        },
        "restart": {
            "predecessor_terminal": {
                "path": str(terminal),
                "size_bytes": terminal_size,
                "sha256": terminal_sha,
            },
            "successor_input_link": {
                "path": str(link),
                "resolved_target": str(link_target),
            },
        },
        "source": {
            "campaign_coordinator_commit": campaign_commit,
            "attestor_commit": attestor_commit,
        },
    }


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as stream:
            json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
            temporary = Path(stream.name)
        try:
            # Both paths are in one directory.  Linking the fully fsynced
            # temporary file publishes atomically and cannot replace a receipt
            # produced concurrently by another controller.
            os.link(temporary, path)
        except FileExistsError as error:
            raise ValueError(f"refusing to replace existing transition receipt: {path}") from error
        temporary.unlink()
        temporary = None
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def publish_receipt(
    predecessor_attempt: Path,
    successor_attempt: Path,
    *,
    season: str,
    predecessor_index: int,
    successor_index: int,
    campaign_commit: str,
    attestor_commit: str,
) -> Path:
    """Atomically publish a receipt, refusing to replace an existing one."""
    path = receipt_path(successor_attempt)
    if path.exists():
        raise ValueError(f"refusing to replace existing transition receipt: {path}")
    value = build_receipt(
        predecessor_attempt,
        successor_attempt,
        season=season,
        predecessor_index=predecessor_index,
        successor_index=successor_index,
        campaign_commit=campaign_commit,
        attestor_commit=attestor_commit,
    )
    _atomic_json(path, value)
    return path


def _expect_mapping(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _validate_identity(
    value: object,
    *,
    label: str,
    attempt: Path,
    index: int,
    report_path: Path,
    report: dict,
) -> None:
    identity = _expect_mapping(value, label)
    expected = {
        "index": index,
        "segment": attempt.parent.name,
        "attempt": attempt.name,
        "start": report["start"],
        "end": report["end"],
        "segment_json_sha256": digest(report_path),
    }
    if identity != expected:
        raise ValueError(f"{label} does not match completed segment identity")


def validate_receipt(
    path: Path,
    predecessor_attempt: Path,
    successor_attempt: Path,
    *,
    season: str,
    predecessor_index: int,
    successor_index: int,
    campaign_commit: str,
    verify_retained_payload: bool = True,
) -> dict:
    """Validate a receipt and optionally reread any retained restart payload."""
    predecessor_attempt = predecessor_attempt.resolve()
    successor_attempt = successor_attempt.resolve()
    expected_receipt = receipt_path(successor_attempt)
    path = Path(os.path.abspath(path))
    if path != expected_receipt or path.is_symlink() or not path.is_file():
        raise ValueError(f"transition receipt must be a regular file at {expected_receipt}")
    receipt = _read_json_object(path)
    if receipt.get("schema") != SCHEMA:
        raise ValueError(f"{path}: unsupported or missing receipt schema")
    if set(receipt) != {"schema", "created_at", "transition", "restart", "source"}:
        raise ValueError(f"{path}: receipt has missing or unexpected top-level fields")
    parse_time(receipt["created_at"])
    predecessor_index = _integer(predecessor_index, "predecessor index")
    successor_index = _integer(successor_index, "successor index")
    if successor_index != predecessor_index + 1:
        raise ValueError("successor index must immediately follow predecessor index")
    campaign_commit = _commit(campaign_commit, "campaign coordinator commit")

    first_path, first = _segment_report(predecessor_attempt)
    second_path, second = _segment_report(successor_attempt)
    _validate_attempt_location(predecessor_attempt, season, predecessor_index, first)
    _validate_attempt_location(successor_attempt, season, successor_index, second)
    checkpoint = parse_time(first["end"])
    if parse_time(second["start"]) != checkpoint:
        raise ValueError("successor start does not equal predecessor checkpoint")
    transition = _expect_mapping(receipt["transition"], "transition")
    if set(transition) != {"season", "checkpoint_time", "predecessor", "successor"}:
        raise ValueError("transition has missing or unexpected fields")
    if transition["season"] != season:
        raise ValueError("receipt season does not match campaign")
    if parse_time(transition["checkpoint_time"]) != checkpoint:
        raise ValueError("receipt checkpoint does not match predecessor end")
    _validate_identity(
        transition["predecessor"],
        label="receipt predecessor",
        attempt=predecessor_attempt,
        index=predecessor_index,
        report_path=first_path,
        report=first,
    )
    _validate_identity(
        transition["successor"],
        label="receipt successor",
        attempt=successor_attempt,
        index=successor_index,
        report_path=second_path,
        report=second,
    )

    restart = _expect_mapping(receipt["restart"], "restart")
    if set(restart) != {"predecessor_terminal", "successor_input_link"}:
        raise ValueError("restart evidence has missing or unexpected fields")
    terminal = _expect_mapping(restart["predecessor_terminal"], "predecessor terminal")
    successor_input = _expect_mapping(
        restart["successor_input_link"], "successor input link"
    )
    record_fields = {"path", "size_bytes", "sha256"}
    if set(terminal) != record_fields or set(successor_input) != {
        "path",
        "resolved_target",
    }:
        raise ValueError("restart file evidence has missing or unexpected fields")
    expected_terminal = str(_terminal_path(predecessor_attempt, first).resolve(strict=False))
    if terminal["path"] != expected_terminal:
        raise ValueError("receipt terminal path does not match predecessor segment")
    terminal_size = _integer(terminal["size_bytes"], "terminal size")
    terminal_sha = _sha(terminal["sha256"], "terminal SHA-256")
    if terminal_size <= 0:
        raise ValueError("restart receipt does not attest a nonempty file")
    input_path = Path(successor_input["path"])
    restart_dir = (successor_attempt / "restart").resolve()
    if input_path.parent.resolve() != restart_dir or input_path.suffix != ".nc":
        raise ValueError("receipt successor input path is outside its restart directory")
    if successor_input["resolved_target"] != terminal["path"]:
        raise ValueError("receipt successor link target does not match predecessor restart")

    source = _expect_mapping(receipt["source"], "source")
    if set(source) != {
        "campaign_coordinator_commit",
        "attestor_commit",
    }:
        raise ValueError("receipt source has missing or unexpected fields")
    if _commit(source["campaign_coordinator_commit"], "receipt campaign commit") != campaign_commit:
        raise ValueError("receipt campaign coordinator commit does not match campaign")
    _commit(source["attestor_commit"], "receipt attestor commit")

    terminal_path = Path(terminal["path"])
    if os.path.lexists(terminal_path):
        if terminal_path.is_symlink() or not terminal_path.is_file():
            raise ValueError("retained predecessor restart is not a regular file")
        if terminal_path.stat().st_size != terminal_size:
            raise ValueError("retained predecessor restart differs from transition receipt")
        if verify_retained_payload and digest(terminal_path) != terminal_sha:
            raise ValueError("retained predecessor restart differs from transition receipt")
    if os.path.lexists(input_path):
        if not input_path.is_symlink():
            raise ValueError("retained successor restart input is not a symlink")
        try:
            resolved = input_path.resolve(strict=True)
        except FileNotFoundError as error:
            raise ValueError("retained successor restart input is a broken symlink") from error
        if (
            str(resolved) != successor_input["resolved_target"]
            or resolved.stat().st_size != terminal_size
        ):
            raise ValueError("retained successor restart input differs from transition receipt")
    return receipt


def _completed_attempt(segment_root: Path) -> Path | None:
    marked = sorted(path.parent for path in segment_root.glob("attempt-*/segment.complete"))
    if not marked:
        return None
    if len(marked) != 1:
        raise ValueError(f"{segment_root}: expected at most one completed attempt")
    return marked[0]


def backfill_campaign(config_path: Path, repo_root: Path) -> list[Path]:
    """Publish receipts for every adjacent pair of currently completed segments."""
    document = _read_json_object(config_path.resolve())
    config = document.get("config", document)
    if not isinstance(config, dict):
        raise ValueError("campaign configuration must be a JSON object")
    campaign_root = Path(config["root"]).resolve()
    campaign_commit = campaign_coordinator_commit(campaign_root)
    attestor_commit = source_commit(repo_root)
    segment_hours = float(config["segment_hours"])
    if segment_hours <= 0:
        raise ValueError("segment_hours must be positive")
    created = []
    for season in config["seasons"]:
        name = season["name"]
        start = parse_time(season["start"])
        end = parse_time(season["end"])
        segment_delta = timedelta(hours=segment_hours)
        if end <= start or (end - start) % segment_delta:
            raise ValueError(f"{name}: interval is not an exact multiple of segment_hours")
        attempts = []
        index = 0
        current = start
        while current < end:
            following = current + segment_delta
            segment_root = (
                campaign_root
                / name
                / f"{index:03d}_{current:%Y%m%d_%H%M}_{following:%Y%m%d_%H%M}"
            )
            attempts.append(_completed_attempt(segment_root))
            current = following
            index += 1
        for predecessor_index, (first, second) in enumerate(zip(attempts, attempts[1:])):
            if first is None or second is None:
                continue
            target = receipt_path(second)
            if target.exists():
                validate_receipt(
                    target,
                    first,
                    second,
                    season=name,
                    predecessor_index=predecessor_index,
                    successor_index=predecessor_index + 1,
                    campaign_commit=campaign_commit,
                    verify_retained_payload=False,
                )
                continue
            created.append(
                publish_receipt(
                    first,
                    second,
                    season=name,
                    predecessor_index=predecessor_index,
                    successor_index=predecessor_index + 1,
                    campaign_commit=campaign_commit,
                    attestor_commit=attestor_commit,
                )
            )
    return created


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-config", required=True, type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    try:
        created = backfill_campaign(args.campaign_config, args.repo_root)
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        parser.error(str(error))
    print(json.dumps({"created": [str(path) for path in created]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
