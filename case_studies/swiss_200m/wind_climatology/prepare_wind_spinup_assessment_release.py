#!/usr/bin/env python3
"""Build an immutable, checksum-bound wind-spinup assessment runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RUNTIME_PATHS = (
    "config/balfrin.env",
    "scripts/load_balfrin_site_config.sh",
    "scripts/hicar_domain_to_fieldextra_grid.py",
    "scripts/prepare_icon_inputs.sh",
    "scripts/reduce_hicar_wind_climatology.py",
    "case_studies/swiss_200m/config/fieldextra_target_grid.txt",
    "case_studies/swiss_200m/config/hicar_swiss_200m.nml.in",
    "case_studies/swiss_200m/scripts/gpu_rank_wrapper.sh",
    "case_studies/swiss_200m/scripts/produce_rea_l_stream_record_balfrin.sbatch",
    "case_studies/swiss_200m/scripts/render_hicar_namelist.py",
    "case_studies/swiss_200m/scripts/run_rea_l_stream_chunk_balfrin.sbatch",
    "case_studies/swiss_200m/streaming/validate_model_chunk.py",
    "case_studies/swiss_200m/validation/validate_forcing.py",
    "case_studies/swiss_200m/wind_climatology/experiments/bridge_spinup_plateau_gate_v1.json",
    "case_studies/swiss_200m/wind_climatology/prepare_rea_l_plateau_gate.py",
    "case_studies/swiss_200m/wind_climatology/assess_rea_l_plateau_case.py",
    "case_studies/swiss_200m/wind_climatology/prepare_wind_spinup_results.py",
    "case_studies/swiss_200m/wind_climatology/assess_wind_spinup_convergence.py",
    "case_studies/swiss_200m/wind_climatology/finalize_wind_spinup_decision.py",
    "case_studies/swiss_200m/wind_climatology/assess_wind_spinup_mechanism.py",
    "case_studies/swiss_200m/wind_climatology/assess_wind_spinup_mechanism_balfrin.sbatch",
    "case_studies/swiss_200m/wind_climatology/prepare_forcing_reproducibility_probe.py",
    "case_studies/swiss_200m/wind_climatology/compare_netcdf_arrays.py",
    "case_studies/swiss_200m/wind_climatology/compare_forcing_reproducibility_balfrin.sbatch",
    "case_studies/swiss_200m/wind_climatology/prepare_wind_pathway_experiment.py",
    "case_studies/swiss_200m/wind_climatology/finalize_wind_pathway_forcing.py",
    "case_studies/swiss_200m/wind_climatology/finalize_wind_pathway_forcing_balfrin.sbatch",
    "case_studies/swiss_200m/wind_climatology/run_wind_pathway_balfrin.sbatch",
    "case_studies/swiss_200m/wind_climatology/assess_wind_pathway_experiment.py",
    "case_studies/swiss_200m/wind_climatology/assess_wind_pathway_balfrin.sbatch",
    "case_studies/swiss_200m/wind_climatology/finalize_wind_mechanism_assessment.py",
    "case_studies/swiss_200m/wind_climatology/finalize_wind_mechanism_balfrin.sbatch",
    "case_studies/swiss_200m/wind_climatology/assess_wind_spinup_convergence_balfrin.sbatch",
    "case_studies/swiss_200m/wind_climatology/prepare_wind_spinup_assessment_release.py",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_value(root: Path, *arguments: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), *arguments],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def write_json(path: Path, payload: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")


def build_release(source_root: Path, release_root: Path) -> dict[str, Any]:
    source_root = source_root.resolve()
    release_root = release_root.resolve()
    if release_root.exists():
        raise ValueError(f"release root already exists: {release_root}")
    release_root.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{release_root.name}.", dir=release_root.parent)
    )
    try:
        inventory = []
        for relative in RUNTIME_PATHS:
            source = source_root / relative
            if not source.is_file():
                raise ValueError(f"assessment runtime source is missing: {source}")
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            inventory.append(
                {
                    "path": relative,
                    "size_bytes": target.stat().st_size,
                    "sha256": sha256(target),
                }
            )
        (
            temporary
            / "case_studies/swiss_200m/wind_climatology/experiments/"
            "bridge_spinup_plateau_gate_v1.json.ready"
        ).touch()
        status = git_value(source_root, "status", "--porcelain")
        payload = {
            "schema_version": 1,
            "status": "PASS",
            "purpose": "wind-spinup-convergence-assessment",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "release_root": str(release_root),
            "source_root": str(source_root),
            "source_commit": git_value(source_root, "rev-parse", "HEAD"),
            "source_dirty": bool(status) if status is not None else None,
            "files": inventory,
        }
        manifest = temporary / "runtime_release.json"
        write_json(manifest, payload)
        (temporary / "runtime_release.json.ready").touch()
        for path in sorted(temporary.rglob("*"), reverse=True):
            if path.is_file():
                path.chmod(path.stat().st_mode & ~0o222)
            elif path.is_dir():
                path.chmod(path.stat().st_mode & ~0o222)
        temporary.chmod(temporary.stat().st_mode & ~0o222)
        os.replace(temporary, release_root)
        return payload
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def validate_release(manifest_path: Path) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    if not manifest_path.is_file() or not Path(f"{manifest_path}.ready").is_file():
        raise ValueError(f"assessment runtime is not published: {manifest_path}")
    payload = json.loads(manifest_path.read_text())
    if payload.get("status") != "PASS":
        raise ValueError("assessment runtime status is not PASS")
    root = Path(payload["release_root"]).resolve()
    if manifest_path != root / "runtime_release.json":
        raise ValueError("assessment runtime release_root mismatch")
    expected = set(RUNTIME_PATHS)
    inventory = {item["path"]: item for item in payload["files"]}
    if set(inventory) != expected:
        raise ValueError("assessment runtime inventory mismatch")
    for relative in RUNTIME_PATHS:
        path = root / relative
        if not path.is_file() or sha256(path) != inventory[relative]["sha256"]:
            raise ValueError(f"assessment runtime file changed: {path}")
        if path.stat().st_mode & 0o222:
            raise ValueError(f"assessment runtime file is writable: {path}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--release-root", type=Path)
    parser.add_argument("--validate", type=Path)
    args = parser.parse_args()
    if args.validate is not None:
        payload = validate_release(args.validate)
        print(f"assessment runtime validated: {payload['release_root']}")
        return 0
    if args.source_root is None or args.release_root is None:
        parser.error("--source-root and --release-root are required for a build")
    payload = build_release(args.source_root, args.release_root)
    print(f"assessment runtime published: {payload['release_root']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
