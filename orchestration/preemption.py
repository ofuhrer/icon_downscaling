#!/usr/bin/env python3
"""Run a command with fail-closed Balfrin pre-emption handling."""

from __future__ import annotations

import argparse
import json
import os
import selectors
import signal
import socket
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PREEMPTED_EXIT_CODE = 75
PREEMPTION_SIGNALS = {
    signal.SIGTERM: "SIGTERM",
    signal.SIGUSR1: "SIGUSR1",
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def interruption_payload(
    *,
    attempt_id: str,
    signal_name: str,
    command: list[str],
    completion_marker: Path,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "INTERRUPTED",
        "attempt_id": attempt_id,
        "signal": signal_name,
        "observed_at": utc_now(),
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
        "slurm_array_job_id": os.environ.get("SLURM_ARRAY_JOB_ID"),
        "slurm_array_task_id": os.environ.get("SLURM_ARRAY_TASK_ID"),
        "slurm_restart_count": os.environ.get("SLURM_RESTART_COUNT", "0"),
        "completion_marker": str(completion_marker.resolve()),
        "completion_published": completion_marker.is_file(),
        "command": command,
        "recovery": (
            "Retry in a new immutable attempt from the last validator-published "
            "checkpoint; never reuse this attempt directory."
        ),
    }


def record_interruption(
    *,
    report: Path,
    attempt_id: str,
    signal_name: str,
    completion_marker: Path,
    command: list[str],
) -> bool:
    if completion_marker.is_file():
        return False
    write_json_atomic(
        report,
        interruption_payload(
            attempt_id=attempt_id,
            signal_name=signal_name,
            command=command,
            completion_marker=completion_marker,
        ),
    )
    return True


def terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def run_guarded(args: argparse.Namespace) -> int:
    if not args.command:
        raise SystemExit("a command is required after --")
    report = args.report.resolve()
    completion_marker = args.completion_marker.resolve()
    log_path = args.log.resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    child: subprocess.Popen[bytes] | None = None
    observed_signal: str | None = None

    def handle_signal(signum: int, _frame: object) -> None:
        nonlocal observed_signal
        signal_name = PREEMPTION_SIGNALS.get(signum, f"SIGNAL_{signum}")
        if completion_marker.is_file():
            return
        observed_signal = signal_name
        record_interruption(
            report=report,
            attempt_id=args.attempt_id,
            signal_name=signal_name,
            completion_marker=completion_marker,
            command=args.command,
        )
        if child is not None:
            terminate_process_group(child)

    previous_handlers = {
        signum: signal.signal(signum, handle_signal)
        for signum in PREEMPTION_SIGNALS
    }
    try:
        child = subprocess.Popen(
            args.command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        assert child.stdout is not None
        selector = selectors.DefaultSelector()
        selector.register(child.stdout, selectors.EVENT_READ)
        with log_path.open("ab", buffering=0) as log:
            while True:
                events = selector.select(timeout=1)
                if not events:
                    if child.poll() is None:
                        continue
                    block = os.read(child.stdout.fileno(), 1024 * 1024)
                else:
                    block = os.read(child.stdout.fileno(), 1024 * 1024)
                if not block and child.poll() is not None:
                    break
                if not block:
                    continue
                log.write(block)
                sys.stdout.buffer.write(block)
                sys.stdout.buffer.flush()
        selector.close()
        return_code = child.wait()
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)

    signal_return_codes = {
        -signal.SIGTERM,
        -signal.SIGUSR1,
        128 + signal.SIGTERM,
        128 + signal.SIGUSR1,
    }
    if (
        not completion_marker.is_file()
        and (observed_signal is not None or return_code in signal_return_codes)
    ):
        if observed_signal is None:
            observed_signal = (
                "SIGTERM"
                if return_code in {-signal.SIGTERM, 128 + signal.SIGTERM}
                else "SIGUSR1"
            )
            record_interruption(
                report=report,
                attempt_id=args.attempt_id,
                signal_name=observed_signal,
                completion_marker=completion_marker,
                command=args.command,
            )
        return PREEMPTED_EXIT_CODE
    return return_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command_name", required=True)

    run = subparsers.add_parser("run")
    run.add_argument("--attempt-id", required=True)
    run.add_argument("--report", type=Path, required=True)
    run.add_argument("--completion-marker", type=Path, required=True)
    run.add_argument("--log", type=Path, required=True)
    run.add_argument("command", nargs=argparse.REMAINDER)

    record = subparsers.add_parser("record")
    record.add_argument("--attempt-id", required=True)
    record.add_argument("--report", type=Path, required=True)
    record.add_argument("--completion-marker", type=Path, required=True)
    record.add_argument("--signal", dest="signal_name", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command_name == "run":
        if args.command and args.command[0] == "--":
            args.command = args.command[1:]
        return run_guarded(args)
    created = record_interruption(
        report=args.report.resolve(),
        attempt_id=args.attempt_id,
        signal_name=args.signal_name,
        completion_marker=args.completion_marker.resolve(),
        command=[],
    )
    return PREEMPTED_EXIT_CODE if created else 0


if __name__ == "__main__":
    raise SystemExit(main())
