#!/usr/bin/env python3
"""Check that a checkout can run the supported Balfrin workflow."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import socket
import subprocess
import tempfile
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "orchestration"))
from runtime_contract import (  # noqa: E402
    S83_CAMPAIGN_PARTITIONS,
    validate_s83_partition_record,
)


REQUIRED_CONFIG = {
    "USER_ENV_ROOT",
    "REA_FDB_IMAGE",
    "FIELD_EXTRA_BIN",
    "FIELD_EXTRA_RESOURCES",
    "FIELD_EXTRA_SAMPLE",
    "ICON_GRID",
    "ICON_DOWNSCALING_DURABLE_ROOT",
    "HICAR_PRODUCTION_BRANCH",
    "HICAR_PRODUCTION_COMMIT",
    "HICAR_PRIMARY_WORKFLOW",
}


def selected_site_config(default: Path) -> Path:
    """Return the operator-selected site record, or the repository default."""
    return Path(os.environ.get("HICAR_SITE_CONFIG", default))


def load_config(path: Path, environ: dict[str, str] | None = None) -> dict[str, str]:
    """Load the deliberately small KEY=VALUE site-default format."""
    values: dict[str, str] = {}
    for line_number, raw in enumerate(path.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"{path}:{line_number}: expected KEY=VALUE")
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not key or not key.replace("_", "").isalnum():
            raise ValueError(f"{path}:{line_number}: invalid key {key!r}")
        tokens = shlex.split(raw_value, comments=True)
        if len(tokens) != 1:
            raise ValueError(f"{path}:{line_number}: value must be one token")
        values[key] = tokens[0]
    missing = sorted(REQUIRED_CONFIG - values.keys())
    if missing:
        raise ValueError(f"site configuration is missing: {', '.join(missing)}")
    active_environment = os.environ if environ is None else environ
    return {
        key: active_environment.get(key, default)
        for key, default in values.items()
    }


def command_output(arguments: list[str], timeout: int = 30) -> str:
    result = subprocess.run(
        arguments,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.stdout.strip()


def add_check(
    checks: list[dict[str, Any]],
    name: str,
    passed: bool,
    detail: str,
    *,
    required: bool = True,
) -> None:
    checks.append(
        {
            "name": name,
            "status": "PASS" if passed else ("FAIL" if required else "WARN"),
            "required": required,
            "detail": detail,
        }
    )


def check_path(
    checks: list[dict[str, Any]],
    name: str,
    path: Path,
    *,
    executable: bool = False,
    writable: bool = False,
) -> None:
    exists = path.is_file() if executable else path.exists()
    passed = exists
    requirements = ["exists"]
    if executable:
        requirements.append("executable")
        passed = passed and os.access(path, os.X_OK)
    if writable:
        requirements.append("writable")
        passed = passed and os.access(path, os.W_OK)
    add_check(
        checks,
        name,
        passed,
        f"{path} ({', '.join(requirements)})",
    )


def run_checks(
    repo_root: Path,
    config: dict[str, str],
    *,
    check_fdb: bool,
    hostname: str | None = None,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    host = hostname or socket.gethostname()
    checks: list[dict[str, Any]] = []

    add_check(
        checks,
        "Balfrin host",
        host.startswith("balfrin"),
        host,
    )
    add_check(
        checks,
        "supported login node",
        not host.startswith("balfrin-ln001"),
        "balfrin-ln001 must not be used" if host.startswith("balfrin-ln001") else host,
    )
    add_check(
        checks,
        "primary workflow",
        config["HICAR_PRIMARY_WORKFLOW"] == "preemptible",
        config["HICAR_PRIMARY_WORKFLOW"],
    )

    scratch_value = os.environ.get("SCRATCH", "")
    scratch = Path(scratch_value) if scratch_value else None
    add_check(
        checks,
        "SCRATCH",
        bool(scratch and scratch.is_absolute() and scratch.is_dir() and os.access(scratch, os.W_OK)),
        scratch_value or "SCRATCH is unset",
    )
    check_path(
        checks,
        "module tree",
        Path(config["USER_ENV_ROOT"]) / "modules",
    )
    check_path(
        checks,
        "fieldextra executable",
        Path(config["FIELD_EXTRA_BIN"]),
        executable=True,
    )
    check_path(
        checks,
        "fieldextra resources",
        Path(config["FIELD_EXTRA_RESOURCES"]),
    )
    check_path(
        checks,
        "fieldextra ecCodes sample",
        Path(config["FIELD_EXTRA_SAMPLE"]),
    )
    check_path(checks, "ICON grid", Path(config["ICON_GRID"]))
    check_path(
        checks,
        "durable project root",
        Path(config["ICON_DOWNSCALING_DURABLE_ROOT"]),
        writable=True,
    )

    for command in (
        "git",
        "sbatch",
        "scontrol",
        "squeue",
        "sacct",
        "sinfo",
        "uenv",
    ):
        resolved = shutil.which(command)
        add_check(
            checks,
            f"command: {command}",
            resolved is not None,
            resolved or "not found on PATH",
        )

    expected_commit = config["HICAR_PRODUCTION_COMMIT"]
    hicar_root = repo_root / "HICAR"
    try:
        outer_toplevel = command_output(
            ["git", "-C", str(repo_root), "rev-parse", "--show-toplevel"]
        )
        add_check(
            checks,
            "coordinator checkout",
            Path(outer_toplevel).resolve() == repo_root,
            outer_toplevel,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        add_check(checks, "coordinator checkout", False, str(exc))
    try:
        hicar_commit = command_output(
            ["git", "-C", str(hicar_root), "rev-parse", "HEAD"]
        )
        add_check(
            checks,
            "production HICAR pin",
            hicar_commit == expected_commit,
            f"expected {expected_commit}; found {hicar_commit}",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        add_check(checks, "production HICAR pin", False, str(exc))
    try:
        remote_tip = command_output(
            [
                "git",
                "-C",
                str(hicar_root),
                "rev-parse",
                f"origin/{config['HICAR_PRODUCTION_BRANCH']}",
            ]
        )
        add_check(
            checks,
            "production HICAR remote branch",
            remote_tip == expected_commit,
            f"expected {expected_commit}; found {remote_tip}",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        add_check(checks, "production HICAR remote branch", False, str(exc))

    for partition in sorted(S83_CAMPAIGN_PARTITIONS):
        try:
            record = command_output(
                ["scontrol", "show", "partition", partition, "-o"]
            )
            fields = validate_s83_partition_record(partition, record)
            add_check(
                checks,
                f"s83 partition: {partition}",
                True,
                (
                    f"State={fields['State']} "
                    f"AllowGroups={fields['AllowGroups']}"
                ),
            )
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            add_check(checks, f"s83 partition: {partition}", False, str(exc))

    if check_fdb:
        try:
            fdb = command_output(
                [
                    "uenv",
                    "run",
                    "--view=rea-l-ch1",
                    config["REA_FDB_IMAGE"],
                    "--",
                    "fdb-info",
                    "--all",
                ],
                timeout=120,
            )
            add_check(
                checks,
                "REA-L-CH1 FDB metadata",
                bool(fdb),
                f"view returned {len(fdb.splitlines())} lines",
            )
        except (OSError, subprocess.SubprocessError) as exc:
            add_check(checks, "REA-L-CH1 FDB metadata", False, str(exc))
    else:
        add_check(
            checks,
            "REA-L-CH1 FDB metadata",
            False,
            "not requested; rerun with --check-fdb before producing forcing",
            required=False,
        )

    status = (
        "PASS"
        if all(item["status"] != "FAIL" for item in checks)
        else "FAIL"
    )
    return {
        "schema_version": 1,
        "status": status,
        "purpose": "balfrin-user-preflight",
        "checked_at": datetime.now(UTC).isoformat(),
        "host": host,
        "user": os.environ.get("USER", ""),
        "repo_root": str(repo_root),
        "production": {
            "hicar_branch": config["HICAR_PRODUCTION_BRANCH"],
            "hicar_commit": expected_commit,
            "primary_workflow": config["HICAR_PRIMARY_WORKFLOW"],
        },
        "site": {
            "fdb_image": config["REA_FDB_IMAGE"],
            "durable_root": config["ICON_DOWNSCALING_DURABLE_ROOT"],
        },
        "checks": checks,
    }


def publish(path: Path, payload: dict[str, Any]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    ready = Path(f"{path}.ready")
    ready.unlink(missing_ok=True)
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
    if payload["status"] == "PASS":
        ready.touch()


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=root)
    parser.add_argument(
        "--config",
        type=Path,
        default=selected_site_config(root / "config/balfrin.env"),
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check-fdb", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    payload = run_checks(
        args.repo_root,
        config,
        check_fdb=args.check_fdb,
    )
    output = args.output
    if output is None:
        scratch = os.environ.get("SCRATCH")
        if not scratch:
            raise SystemExit("SCRATCH is unset; pass --output explicitly")
        output = Path(scratch) / "icon_hicar/onboarding/balfrin_preflight.json"
    publish(output, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
