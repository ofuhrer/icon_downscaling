#!/usr/bin/env python3
"""Publish a minimal immutable runtime for the Swiss static A/B/C audit."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


FILES = (
    "scripts/prepare_static_inputs.py",
    "case_studies/swiss_200m/validation/validate_domain_plan.py",
    "case_studies/swiss_200m/config/domain.json",
    "case_studies/swiss_200m/fixed_parameters/audit_static_candidate.py",
    "case_studies/swiss_200m/fixed_parameters/audit_static_abc_balfrin.sbatch",
    "case_studies/swiss_200m/fixed_parameters/bootstrap_static_python_balfrin.sbatch",
    "case_studies/swiss_200m/fixed_parameters/prepare_static_abc_balfrin.sbatch",
    "scripts/load_balfrin_site_config.sh",
    "config/balfrin.env",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(root), *args), check=True, capture_output=True, text=True
    ).stdout.strip()


def publish(repo_root: Path, output_root: Path) -> Path:
    identities = []
    for relative in FILES:
        source = repo_root / relative
        if not source.is_file():
            raise ValueError(f"runtime source file is missing: {source}")
        identities.append({
            "path": relative,
            "size_bytes": source.stat().st_size,
            "sha256": sha256(source),
        })
    canonical = json.dumps(identities, sort_keys=True, separators=(",", ":"))
    runtime_id = "static-abc-" + hashlib.sha256(canonical.encode()).hexdigest()[:16]
    destination = output_root / runtime_id
    manifest = destination / "runtime_manifest.json"
    if destination.exists():
        if not manifest.is_file() or not Path(f"{manifest}.ready").is_file():
            raise ValueError(f"existing runtime is not published: {destination}")
        existing = json.loads(manifest.read_text(encoding="utf-8"))
        if existing.get("files") != identities:
            raise ValueError(f"existing runtime identity differs: {destination}")
        return destination

    destination.mkdir(parents=True)
    for identity in identities:
        target = destination / identity["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repo_root / identity["path"], target)
        target.chmod(0o555 if target.suffix in {".py", ".sh", ".sbatch"} else 0o444)
    payload = {
        "schema": "hicar-static-qualification-runtime/v1",
        "runtime_id": runtime_id,
        "source_repository": str(repo_root.resolve()),
        "source_commit": git(repo_root, "rev-parse", "HEAD"),
        "source_worktree_clean": not bool(git(repo_root, "status", "--porcelain")),
        "files": identities,
    }
    descriptor, temporary = tempfile.mkstemp(prefix=".runtime_manifest.", dir=destination)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, manifest)
    finally:
        Path(temporary).unlink(missing_ok=True)
    digest = sha256(manifest)
    Path(f"{manifest}.ready").write_text(
        f"sha256 {digest}  {manifest.name}\n", encoding="utf-8"
    )
    for directory, subdirs, files in os.walk(destination, topdown=False):
        for name in files:
            Path(directory, name).chmod(0o444)
        for name in subdirs:
            Path(directory, name).chmod(0o555)
    destination.chmod(0o555)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args()
    try:
        destination = publish(args.repo_root.resolve(), args.output_root.resolve())
    except (ValueError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc)) from exc
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
