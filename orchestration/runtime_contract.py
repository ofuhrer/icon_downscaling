"""Hash-bound runtime contract for pre-emptible campaign releases."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any


REQUIRED_RUNTIME_PATHS = (
    "orchestration/preemptible_campaign.py",
    "orchestration/preemption.py",
    "orchestration/prepare_preemptible_campaign.py",
    "orchestration/qualify_preemptible_recovery.py",
    "orchestration/retire_campaign_artifacts.py",
    "orchestration/run_cpu_task.py",
    "orchestration/runtime_contract.py",
    "requirements/balfrin-preemptible.txt",
    "case_studies/swiss_200m/scripts/bootstrap_preemptible_python_balfrin.sbatch",
    "case_studies/swiss_200m/scripts/compress_hicar_stream_output_balfrin.sbatch",
    "case_studies/swiss_200m/scripts/finalize_rea_l_stream_chunk_balfrin.sbatch",
    "case_studies/swiss_200m/scripts/gpu_rank_wrapper.sh",
    "case_studies/swiss_200m/scripts/produce_rea_l_stream_record_balfrin.sbatch",
    "case_studies/swiss_200m/scripts/render_hicar_namelist.py",
    "case_studies/swiss_200m/scripts/run_preemptible_recovery_probe_balfrin.sbatch",
    "case_studies/swiss_200m/scripts/run_preemptible_campaign_cpu_task_balfrin.sbatch",
    "case_studies/swiss_200m/scripts/run_rea_l_stream_chunk_balfrin.sbatch",
    "case_studies/swiss_200m/scripts/validate_solver_event_balfrin.sbatch",
    "case_studies/swiss_200m/scripts/watch_preemptible_campaign_balfrin.sbatch",
    "case_studies/swiss_200m/streaming/compress_output_file.py",
    "case_studies/swiss_200m/streaming/create_chunk_plan.py",
    "case_studies/swiss_200m/streaming/finalize_forcing_chunk.py",
    "case_studies/swiss_200m/streaming/validate_model_chunk.py",
    "case_studies/swiss_200m/validation/evaluate_hicar_solver_log.py",
    "case_studies/swiss_200m/validation/validate_forcing.py",
    "case_studies/swiss_200m/validation/validate_published_restart_input.py",
    "scripts/hicar_domain_to_fieldextra_grid.py",
    "scripts/prepare_icon_inputs.sh",
    "scripts/reduce_hicar_wind_climatology.py",
)


def sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def validate_runtime_release(
    manifest_path: Path,
    *,
    expected_root: Path | None = None,
    production: bool = False,
) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    if not manifest_path.is_file() or not Path(f"{manifest_path}.ready").is_file():
        raise ValueError(f"runtime release is not published: {manifest_path}")
    payload = json.loads(manifest_path.read_text())
    if payload.get("schema_version") != 1 or payload.get("status") != "PASS":
        raise ValueError(f"runtime release is invalid: {manifest_path}")
    if production and (
        payload.get("purpose") != "production" or payload.get("source_dirty")
    ):
        raise ValueError(
            "production campaign requires a clean production runtime release"
        )
    root = Path(payload["release_root"]).resolve()
    if expected_root is not None and root != expected_root.resolve():
        raise ValueError(
            f"runtime release root {root} does not match repository root "
            f"{expected_root.resolve()}"
        )
    files = payload.get("files")
    if not isinstance(files, list):
        raise ValueError("runtime release has no file inventory")
    indexed = {item.get("path"): item for item in files}
    if set(indexed) != set(REQUIRED_RUNTIME_PATHS):
        missing = sorted(set(REQUIRED_RUNTIME_PATHS) - set(indexed))
        extra = sorted(set(indexed) - set(REQUIRED_RUNTIME_PATHS))
        raise ValueError(
            f"runtime release inventory mismatch; missing={missing}, extra={extra}"
        )
    for relative in REQUIRED_RUNTIME_PATHS:
        item = indexed[relative]
        path = (root / relative).resolve()
        if root not in path.parents or not path.is_file():
            raise ValueError(f"runtime release file is missing: {path}")
        if sha256(path) != item.get("sha256"):
            raise ValueError(f"runtime release file checksum changed: {path}")
    return payload


def validate_python_environment(
    report_path: Path,
    runtime_manifest: Path,
    *,
    smoke: bool = False,
) -> dict[str, Any]:
    report_path = report_path.resolve()
    runtime_manifest = runtime_manifest.resolve()
    if not report_path.is_file() or not Path(f"{report_path}.ready").is_file():
        raise ValueError(f"Python environment is not published: {report_path}")
    payload = json.loads(report_path.read_text())
    if (
        payload.get("schema_version") != 1
        or payload.get("status") != "PASS"
        or payload.get("purpose") != "preemptible-runtime"
    ):
        raise ValueError(f"Python environment report is invalid: {report_path}")
    if (
        Path(payload["runtime_release"]).resolve() != runtime_manifest
        or payload.get("runtime_release_sha256") != sha256(runtime_manifest)
    ):
        raise ValueError("Python environment identifies another runtime release")
    runtime_root = Path(
        json.loads(runtime_manifest.read_text())["release_root"]
    ).resolve()
    requirements = runtime_root / "requirements/balfrin-preemptible.txt"
    if (
        Path(payload["requirements"]).resolve() != requirements
        or payload.get("requirements_sha256") != sha256(requirements)
    ):
        raise ValueError("Python environment identifies other requirements")
    executable = Path(payload["python"]).absolute()
    if not executable.is_file() or not executable.stat().st_mode & 0o111:
        raise ValueError(f"Python environment executable is missing: {executable}")
    if smoke:
        subprocess.run(
            [
                str(executable),
                "-c",
                "import netCDF4,numpy,pyproj,scipy,yaml",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    return payload
