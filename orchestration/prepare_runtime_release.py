#!/usr/bin/env python3
"""Publish an immutable runtime release for a Balfrin campaign."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

try:
    from runtime_contract import REQUIRED_RUNTIME_PATHS, sha256
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from runtime_contract import REQUIRED_RUNTIME_PATHS, sha256


def git_output(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def write_json_atomic(path: Path, payload: dict) -> None:
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


def build_release(
    source_root: Path,
    output_root: Path,
    purpose: str,
    declared_root: Path | None = None,
) -> dict:
    source_root = source_root.resolve()
    output_root = output_root.resolve()
    release_root = (
        declared_root.resolve() if declared_root is not None else output_root
    )
    if output_root.exists():
        raise ValueError(f"release root already exists: {output_root}")
    if output_root == source_root or source_root in output_root.parents:
        raise ValueError("release root must not be inside the source repository")
    if purpose not in {"engineering", "production"}:
        raise ValueError("purpose must be engineering or production")

    status = git_output(
        source_root,
        "status",
        "--porcelain",
        "--untracked-files=all",
        "--",
        *REQUIRED_RUNTIME_PATHS,
    )
    if purpose == "production" and status:
        raise ValueError("production runtime source is not clean")

    temporary = output_root.with_name(
        f".{output_root.name}.partial.{os.getpid()}"
    )
    if temporary.exists():
        raise ValueError(f"release staging path already exists: {temporary}")
    temporary.mkdir(parents=True)
    try:
        files = []
        for relative in REQUIRED_RUNTIME_PATHS:
            source = (source_root / relative).resolve()
            if source_root not in source.parents or not source.is_file():
                raise ValueError(f"required runtime source is missing: {source}")
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            source_mode = source.stat().st_mode & 0o777
            immutable_mode = 0o555 if source_mode & 0o111 else 0o444
            target.chmod(immutable_mode)
            files.append(
                {
                    "path": relative,
                    "sha256": sha256(target),
                    "size_bytes": target.stat().st_size,
                    "mode": f"{immutable_mode:04o}",
                }
            )

        diff_digest = None
        if status:
            diff = subprocess.run(
                [
                    "git",
                    "-C",
                    str(source_root),
                    "diff",
                    "--binary",
                    "--",
                    *REQUIRED_RUNTIME_PATHS,
                ],
                check=True,
                capture_output=True,
            ).stdout
            untracked = [
                line[3:]
                for line in status.splitlines()
                if line.startswith("?? ")
            ]
            hasher = hashlib.sha256()
            hasher.update(diff)
            for relative in sorted(untracked):
                path = source_root / relative
                hasher.update(relative.encode())
                hasher.update(path.read_bytes())
            diff_digest = hasher.hexdigest()

        manifest = {
            "schema_version": 1,
            "status": "PASS",
            "purpose": purpose,
            "release_root": str(release_root),
            "created_at": datetime.now(UTC).isoformat(),
            "source_root": str(source_root),
            "source_commit": git_output(source_root, "rev-parse", "HEAD"),
            "source_dirty": bool(status),
            "source_change_sha256": diff_digest,
            "files": files,
        }
        manifest_path = temporary / "runtime_release.json"
        write_json_atomic(manifest_path, manifest)
        manifest_path.chmod(0o444)
        Path(f"{manifest_path}.ready").touch()
        output_root.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temporary, output_root)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument(
        "--declared-root",
        type=Path,
        help="Final absolute deployment root when building a relocatable stage.",
    )
    parser.add_argument(
        "--purpose",
        required=True,
        choices=("engineering", "production"),
    )
    args = parser.parse_args()
    payload = build_release(
        args.source_root,
        args.output_root,
        args.purpose,
        args.declared_root,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
