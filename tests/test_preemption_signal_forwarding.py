import signal
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORWARDER = ROOT / "scripts/preemption_signal_forwarding.sh"


def test_batch_shell_forwards_term_and_preserves_guard_status(tmp_path):
    started = tmp_path / "started"
    observed = tmp_path / "observed"
    child = (
        "import pathlib,signal,sys,time;"
        "observed=pathlib.Path(sys.argv[1]);"
        "started=pathlib.Path(sys.argv[2]);"
        "signal.signal(signal.SIGTERM,"
        "lambda *_:(observed.write_text('TERM'),sys.exit(75)));"
        "started.touch();"
        "time.sleep(60)"
    )
    process = subprocess.Popen(
        [
            "bash",
            "-c",
            'set -euo pipefail; . "$1"; shift; '
            "run_with_preemption_signal_forwarding \"$@\"",
            "bash",
            str(FORWARDER),
            sys.executable,
            "-c",
            child,
            str(observed),
            str(started),
        ]
    )
    deadline = time.monotonic() + 5
    while not started.is_file() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert started.is_file()

    process.send_signal(signal.SIGTERM)
    assert process.wait(timeout=5) == 75
    assert observed.read_text() == "TERM"


def test_batch_shell_returns_successful_guard_status():
    result = subprocess.run(
        [
            "bash",
            "-c",
            '. "$1"; shift; run_with_preemption_signal_forwarding "$@"',
            "bash",
            str(FORWARDER),
            sys.executable,
            "-c",
            "raise SystemExit(0)",
        ],
        check=False,
        timeout=5,
    )
    assert result.returncode == 0
