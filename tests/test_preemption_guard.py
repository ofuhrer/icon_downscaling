from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "orchestration/preemption.py"


def test_sigterm_records_interruption_and_returns_retry_code(tmp_path):
    child_pid = tmp_path / "child.pid"
    report = tmp_path / "attempt_interrupted.json"
    completion = tmp_path / "model_chunk_completion.json.ready"
    log = tmp_path / "model.out"
    child = (
        "import os,pathlib,time;"
        f"pathlib.Path({str(child_pid)!r}).write_text(str(os.getpid()));"
        "time.sleep(30)"
    )
    process = subprocess.Popen(
        [
            sys.executable,
            str(HELPER),
            "run",
            "--attempt-id",
            "a001-test",
            "--report",
            str(report),
            "--completion-marker",
            str(completion),
            "--log",
            str(log),
            "--",
            sys.executable,
            "-c",
            child,
        ]
    )
    deadline = time.monotonic() + 10
    while not child_pid.is_file() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert child_pid.is_file()
    os.kill(process.pid, signal.SIGTERM)
    assert process.wait(timeout=10) == 75
    payload = json.loads(report.read_text())
    assert payload["status"] == "INTERRUPTED"
    assert payload["signal"] == "SIGTERM"
    assert payload["attempt_id"] == "a001-test"
    with pytest.raises(ProcessLookupError):
        os.kill(int(child_pid.read_text()), 0)


def test_published_completion_suppresses_interruption_record(tmp_path):
    completion = tmp_path / "completion.ready"
    completion.touch()
    report = tmp_path / "interruption.json"
    result = subprocess.run(
        [
            sys.executable,
            str(HELPER),
            "record",
            "--attempt-id",
            "a001-test",
            "--report",
            str(report),
            "--completion-marker",
            str(completion),
            "--signal",
            "SIGTERM",
        ],
        check=False,
    )
    assert result.returncode == 0
    assert not report.exists()
