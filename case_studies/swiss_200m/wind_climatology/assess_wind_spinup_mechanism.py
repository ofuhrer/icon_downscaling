#!/usr/bin/env python3
"""Attribute wind-spinup divergence using preserved full-state restarts."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import netCDF4
import numpy as np


RUN_PATTERN = re.compile(r"spinup-(?P<case>.+)-(?P<hours>\d+)h$")
THREE_D_FIELDS = (
    "density",
    "potential_temperature",
    "temperature",
    "qv",
    "pressure",
)
SURFACE_FIELDS = (
    "hpbl",
    "ustar",
    "surface_rad_temperature",
    "ground_surf_temperature",
    "taix",
    "psfc",
    "hfss",
    "hfls",
)
PHASE_FIELDS = (
    "wind_update_elapsed",
    "lsm_update_phase_offset",
    "radiation_update_phase_offset",
    "lsm_next_update_offset",
    "radiation_next_update_offset",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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


def require_published(path: Path) -> None:
    if not path.is_file() or not Path(f"{path}.ready").is_file():
        raise ValueError(f"required publication is missing: {path}")


def parse_run(run_id: str) -> tuple[str, int]:
    match = RUN_PATTERN.fullmatch(run_id)
    if match is None:
        raise ValueError(f"invalid wind-spinup run id: {run_id}")
    return match.group("case"), int(match.group("hours"))


def latest_completion(chain_root: Path) -> tuple[Path, dict[str, Any]]:
    candidates: list[tuple[datetime, Path, dict[str, Any]]] = []
    for path in chain_root.glob(
        "segments/*/attempts/*/run/model_chunk_completion.json"
    ):
        if not Path(f"{path}.ready").is_file():
            continue
        payload = json.loads(path.read_text())
        if payload.get("status") != "PASS":
            continue
        candidates.append(
            (datetime.fromisoformat(payload["end"]), path, payload)
        )
    if not candidates:
        raise ValueError(f"no published completion found below {chain_root}")
    _, path, payload = max(candidates, key=lambda item: item[0])
    restart = Path(payload["restart"]["path"])
    if not restart.is_file():
        raise ValueError(f"published restart is missing: {restart}")
    return path, payload


def read_float(dataset: netCDF4.Dataset, name: str) -> np.ndarray:
    if name not in dataset.variables:
        raise ValueError(f"{dataset.filepath()} does not contain {name}")
    return np.asarray(dataset.variables[name][...], dtype=np.float64).squeeze(
        axis=0
    )


def scalar_metrics(
    candidate: np.ndarray, reference: np.ndarray
) -> dict[str, Any]:
    if candidate.shape != reference.shape:
        raise ValueError(
            f"shape mismatch: candidate {candidate.shape}, reference "
            f"{reference.shape}"
        )
    difference = candidate - reference
    finite = np.isfinite(difference) & np.isfinite(reference)
    if not np.all(finite):
        raise ValueError("non-finite values encountered in restart comparison")
    axes = tuple(range(1, difference.ndim))
    per_leading_rmse = (
        np.sqrt(np.mean(np.square(difference), axis=axes))
        if axes
        else np.abs(difference)
    )
    per_leading_bias = (
        np.mean(difference, axis=axes) if axes else difference
    )
    reference_rms = math.sqrt(float(np.mean(np.square(reference))))
    rmse = math.sqrt(float(np.mean(np.square(difference))))
    return {
        "rmse": rmse,
        "mean_bias": float(np.mean(difference)),
        "reference_rms": reference_rms,
        "relative_rmse": (
            rmse / reference_rms if reference_rms > 0.0 else math.inf
        ),
        "maximum_absolute_difference": float(
            np.max(np.abs(difference))
        ),
        "per_leading_index_rmse": np.atleast_1d(
            per_leading_rmse
        ).tolist(),
        "per_leading_index_mean_bias": np.atleast_1d(
            per_leading_bias
        ).tolist(),
    }


def mass_center_wind(
    dataset: netCDF4.Dataset,
) -> tuple[np.ndarray, np.ndarray]:
    u = read_float(dataset, "u")
    v = read_float(dataset, "v")
    mass_u = 0.5 * (u[..., :-1] + u[..., 1:])
    mass_v = 0.5 * (v[..., :-1, :] + v[..., 1:, :])
    return mass_u, mass_v


def vector_metrics(
    candidate_u: np.ndarray,
    candidate_v: np.ndarray,
    reference_u: np.ndarray,
    reference_v: np.ndarray,
    boundary_cells: int,
) -> tuple[dict[str, Any], np.ndarray]:
    if not (
        candidate_u.shape
        == candidate_v.shape
        == reference_u.shape
        == reference_v.shape
    ):
        raise ValueError("wind component shapes do not match")
    du = candidate_u - reference_u
    dv = candidate_v - reference_v
    squared_error = np.square(du) + np.square(dv)
    error = np.sqrt(squared_error)
    axes = tuple(range(1, error.ndim))
    per_level_rmse = np.sqrt(np.mean(squared_error, axis=axes))
    reference_speed = np.hypot(reference_u, reference_v)
    candidate_speed = np.hypot(candidate_u, candidate_v)
    overall_rmse = math.sqrt(float(np.mean(squared_error)))
    reference_rms = math.sqrt(float(np.mean(np.square(reference_speed))))

    if 2 * boundary_cells >= error.shape[-1] or 2 * boundary_cells >= error.shape[-2]:
        raise ValueError("boundary width leaves no interior cells")
    interior_squared = squared_error[
        ..., boundary_cells:-boundary_cells, boundary_cells:-boundary_cells
    ]
    total_sum = float(np.sum(squared_error))
    interior_sum = float(np.sum(interior_squared))
    boundary_count = squared_error.size - interior_squared.size
    return (
        {
            "vector_rmse_m_s": overall_rmse,
            "relative_vector_rmse": (
                overall_rmse / reference_rms
                if reference_rms > 0.0
                else math.inf
            ),
            "mean_speed_bias_m_s": float(
                np.mean(candidate_speed - reference_speed)
            ),
            "maximum_vector_error_m_s": float(np.max(error)),
            "per_level_vector_rmse_m_s": per_level_rmse.tolist(),
            "interior_vector_rmse_m_s": math.sqrt(
                float(np.mean(interior_squared))
            ),
            "boundary_vector_rmse_m_s": math.sqrt(
                (total_sum - interior_sum) / boundary_count
            ),
            "boundary_cells": boundary_cells,
        },
        error,
    )


def pearson(first: np.ndarray, second: np.ndarray) -> float | None:
    first = np.asarray(first, dtype=np.float64).ravel()
    second = np.asarray(second, dtype=np.float64).ravel()
    finite = np.isfinite(first) & np.isfinite(second)
    first = first[finite]
    second = second[finite]
    if first.size < 3 or np.std(first) == 0.0 or np.std(second) == 0.0:
        return None
    return float(np.corrcoef(first, second)[0, 1])


def normalized_validation_signature(path: Path) -> str:
    payload = json.loads(path.read_text())
    payload.pop("forcing_file", None)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def forcing_identity_audit(
    campaign_root: Path, run_ids: list[str]
) -> dict[str, Any]:
    records: dict[str, dict[int, list[dict[str, Any]]]] = {}
    for run_id in run_ids:
        _, hours = parse_run(run_id)
        for path in (campaign_root / "execution/chains" / run_id).glob(
            "segments/*/forcing_chunk/forcing/*.manifest.json"
        ):
            payload = json.loads(path.read_text())
            validation = Path(payload["validation_report"])
            if not validation.is_file():
                raise ValueError(f"forcing validation is missing: {validation}")
            records.setdefault(payload["valid_time"], {}).setdefault(
                hours, []
            ).append(
                {
                    "container_sha256": payload["forcing_sha256"],
                    "container_size_bytes": payload["forcing_size_bytes"],
                    "dynamic_source_sha256": payload["source_dynamic"][
                        "sha256"
                    ],
                    "static_source_sha256": payload["source_static"]["sha256"],
                    "validation_signature": normalized_validation_signature(
                        validation
                    ),
                }
            )
    required_hours = {parse_run(run_id)[1] for run_id in run_ids}
    common = {
        valid_time: by_hours
        for valid_time, by_hours in records.items()
        if set(by_hours) == required_hours
    }
    if not common:
        raise ValueError("no forcing valid times are common to all spinups")
    details = []
    for valid_time, by_hours in sorted(common.items()):
        entries = [entry for values in by_hours.values() for entry in values]
        details.append(
            {
                "valid_time": valid_time,
                "dynamic_source_sha256_count": len(
                    {item["dynamic_source_sha256"] for item in entries}
                ),
                "static_source_sha256_count": len(
                    {item["static_source_sha256"] for item in entries}
                ),
                "validation_signature_count": len(
                    {item["validation_signature"] for item in entries}
                ),
                "container_sha256_count": len(
                    {item["container_sha256"] for item in entries}
                ),
                "container_size_count": len(
                    {item["container_size_bytes"] for item in entries}
                ),
                "records": len(entries),
            }
        )
    return {
        "common_valid_time_count": len(details),
        "source_identity_pass": all(
            item["dynamic_source_sha256_count"] == 1
            and item["static_source_sha256_count"] == 1
            for item in details
        ),
        "validation_signature_pass": all(
            item["validation_signature_count"] == 1 for item in details
        ),
        "container_byte_identity_pass": all(
            item["container_sha256_count"] == 1 for item in details
        ),
        "container_size_identity_pass": all(
            item["container_size_count"] == 1 for item in details
        ),
        "scope": (
            "Source GRIB identity and bounded validation signatures do not "
            "prove NetCDF array identity when container hashes differ."
        ),
        "valid_times": details,
    }


def compare_restarts(
    candidate_path: Path,
    reference_path: Path,
    boundary_cells: int,
    sample_stride: int,
) -> dict[str, Any]:
    with netCDF4.Dataset(candidate_path) as candidate, netCDF4.Dataset(
        reference_path
    ) as reference:
        candidate_u, candidate_v = mass_center_wind(candidate)
        reference_u, reference_v = mass_center_wind(reference)
        wind, full_wind_error = vector_metrics(
            candidate_u,
            candidate_v,
            reference_u,
            reference_v,
            boundary_cells,
        )
        lowest_wind_error = full_wind_error[0, ::sample_stride, ::sample_stride]
        del (
            candidate_u,
            candidate_v,
            reference_u,
            reference_v,
            full_wind_error,
        )

        candidate_u10 = read_float(candidate, "u10m")
        candidate_v10 = read_float(candidate, "v10m")
        reference_u10 = read_float(reference, "u10m")
        reference_v10 = read_float(reference, "v10m")
        wind_10m, error_10m = vector_metrics(
            candidate_u10[np.newaxis, ...],
            candidate_v10[np.newaxis, ...],
            reference_u10[np.newaxis, ...],
            reference_v10[np.newaxis, ...],
            boundary_cells,
        )
        error_10m = error_10m[0, ::sample_stride, ::sample_stride]

        state: dict[str, Any] = {}
        correlations: dict[str, float | None] = {}
        for name in THREE_D_FIELDS:
            candidate_field = read_float(candidate, name)
            reference_field = read_float(reference, name)
            state[name] = scalar_metrics(candidate_field, reference_field)
            correlations[f"lowest_wind_error_vs_abs_{name}_difference"] = (
                pearson(
                    lowest_wind_error,
                    np.abs(candidate_field[0] - reference_field[0])[
                        ::sample_stride, ::sample_stride
                    ],
                )
            )

        surface: dict[str, Any] = {}
        for name in SURFACE_FIELDS:
            candidate_field = read_float(candidate, name)
            reference_field = read_float(reference, name)
            surface[name] = scalar_metrics(candidate_field, reference_field)
            correlations[f"10m_wind_error_vs_abs_{name}_difference"] = (
                pearson(
                    error_10m,
                    np.abs(candidate_field - reference_field)[
                        ::sample_stride, ::sample_stride
                    ],
                )
            )

        soil: dict[str, Any] = {}
        for name in ("soil_temperature", "soil_water_content"):
            candidate_field = read_float(candidate, name)
            reference_field = read_float(reference, name)
            soil[name] = scalar_metrics(candidate_field, reference_field)
            correlations[f"10m_wind_error_vs_abs_top_{name}_difference"] = (
                pearson(
                    error_10m,
                    np.abs(candidate_field[0] - reference_field[0])[
                        ::sample_stride, ::sample_stride
                    ],
                )
            )

        phase = {}
        for name in PHASE_FIELDS:
            candidate_field = read_float(candidate, name)
            reference_field = read_float(reference, name)
            phase[name] = scalar_metrics(candidate_field, reference_field)

        return {
            "wind_full_levels": wind,
            "wind_10m": wind_10m,
            "atmospheric_state": state,
            "surface_state": surface,
            "soil_state": soil,
            "update_phase_state": phase,
            "spatial_error_correlations": correlations,
            "correlation_sample_stride": sample_stride,
        }


def assess(
    campaign_root: Path,
    results_path: Path,
    case_id: str,
    output_path: Path,
    boundary_cells: int = 20,
    sample_stride: int = 4,
) -> dict[str, Any]:
    require_published(results_path)
    results = json.loads(results_path.read_text())
    selected_runs = []
    for run in results["runs"]:
        parsed_case, hours = parse_run(run["run_id"])
        if parsed_case == case_id:
            selected_runs.append((hours, run["run_id"]))
    selected_runs.sort()
    if not selected_runs:
        raise ValueError(f"case is not present in results: {case_id}")
    reference_hours = max(hours for hours, _ in selected_runs)

    completions: dict[int, tuple[Path, dict[str, Any]]] = {}
    for hours, run_id in selected_runs:
        completions[hours] = latest_completion(
            campaign_root / "execution/chains" / run_id
        )
    final_times = {
        completion["end"] for _, completion in completions.values()
    }
    if len(final_times) != 1:
        raise ValueError(f"final restart times differ: {sorted(final_times)}")
    reference_completion_path, reference_completion = completions[
        reference_hours
    ]
    reference_restart = Path(reference_completion["restart"]["path"])

    comparisons = []
    for hours, run_id in selected_runs:
        if hours == reference_hours:
            continue
        completion_path, completion = completions[hours]
        restart = Path(completion["restart"]["path"])
        comparisons.append(
            {
                "run_id": run_id,
                "spinup_hours": hours,
                "restart": str(restart),
                "restart_recorded_sha256": completion["restart"]["sha256"],
                "completion": str(completion_path),
                "completion_sha256": sha256(completion_path),
                "metrics": compare_restarts(
                    restart,
                    reference_restart,
                    boundary_cells,
                    sample_stride,
                ),
            }
        )

    payload = {
        "schema_version": 1,
        "status": "PASS",
        "decision": "MECHANISM_EVIDENCE_READY",
        "case_id": case_id,
        "final_valid_time": next(iter(final_times)),
        "reference_spinup_hours": reference_hours,
        "reference_run_id": next(
            run_id
            for hours, run_id in selected_runs
            if hours == reference_hours
        ),
        "reference_restart": str(reference_restart),
        "reference_restart_recorded_sha256": reference_completion["restart"][
            "sha256"
        ],
        "reference_completion": str(reference_completion_path),
        "reference_completion_sha256": sha256(reference_completion_path),
        "campaign_root": str(campaign_root),
        "results": str(results_path),
        "results_sha256": sha256(results_path),
        "forcing_identity": forcing_identity_audit(
            campaign_root, [run_id for _, run_id in selected_runs]
        ),
        "comparisons": comparisons,
        "scope": (
            "Same-valid-time final-restart attribution. Stage-resolved wind "
            "solver fields are not present and require a separate bounded replay "
            "only if state and forcing evidence cannot discriminate."
        ),
    }
    if output_path.exists() or Path(f"{output_path}.ready").exists():
        raise ValueError(f"refusing to replace mechanism report: {output_path}")
    write_json_atomic(output_path, payload)
    Path(f"{output_path}.ready").touch()
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--boundary-cells", type=int, default=20)
    parser.add_argument("--sample-stride", type=int, default=4)
    args = parser.parse_args()
    payload = assess(
        campaign_root=args.campaign_root.resolve(),
        results_path=args.results.resolve(),
        case_id=args.case_id,
        output_path=args.output.resolve(),
        boundary_cells=args.boundary_cells,
        sample_stride=args.sample_stride,
    )
    print(
        f"wind-spinup mechanism case: {payload['case_id']} "
        f"status={payload['status']} comparisons={len(payload['comparisons'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
