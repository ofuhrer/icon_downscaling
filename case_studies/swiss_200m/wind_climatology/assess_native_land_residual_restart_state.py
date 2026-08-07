#!/usr/bin/env python3
"""Compare cold-start-only NoahMP stores with the continuous reference."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import netCDF4
import numpy as np


ORIGINS = ("20200702", "20200703")
VARIABLES = (
    "swe_0", "snow_height", "snow_nlayers", "Sice", "Sliq", "snow_temperature",
    "canopy_water", "canopy_liquid", "canopy_ice", "canopy_fwet",
    "canopy_temperature", "water_aquifer", "water_table_depth", "recharge",
    "soil_water_content_liq", "lai", "sai", "soil_carbon_fast", "soil_carbon_stable",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or not Path(f"{path}.ready").is_file():
        raise ValueError(f"input is not published: {path}")
    payload = json.loads(path.read_text())
    if payload.get("status") not in {"PASS", "PLANNED"}:
        raise ValueError(f"input is not passing: {path}")
    return payload


def as_datetime(value: Any) -> datetime:
    return datetime.fromisoformat(str(value)[:19])


def restart_index(chain: dict[str, Any]) -> dict[datetime, Path]:
    result = {}
    for segment in chain["segments"]:
        completion = require_json(Path(segment["completion"]))
        path = Path(completion["restart"]["path"])
        if not path.is_file():
            raise ValueError(f"restart is missing: {path}")
        valid = as_datetime(segment["end"])
        if valid in result:
            raise ValueError(f"duplicate restart time: {valid}")
        result[valid] = path
    return result


def variable_values(dataset: netCDF4.Dataset, name: str) -> np.ndarray:
    variable = dataset.variables[name]
    values = np.ma.asarray(variable[:]).filled(np.nan)
    if variable.dimensions and variable.dimensions[0] == "time":
        values = values[-1]
    return np.asarray(values)


def interior(values: np.ndarray, trim: int, landmask: np.ndarray) -> np.ndarray:
    if values.ndim < 2:
        return values
    values = values[..., trim:-trim, trim:-trim]
    return values[..., landmask[trim:-trim, trim:-trim]]


def comparison(candidate: np.ndarray, reference: np.ndarray) -> dict[str, Any]:
    finite = np.isfinite(candidate) & np.isfinite(reference)
    if not np.any(finite):
        return {"count": 0, "mean_bias": None, "rmse": None, "max_abs": None}
    error = np.asarray(candidate[finite] - reference[finite], dtype=np.float64)
    return {
        "count": int(error.size),
        "mean_bias": float(np.mean(error)),
        "rmse": float(math.sqrt(np.mean(error * error))),
        "max_abs": float(np.max(np.abs(error))),
    }


def compare_restart_pair(
    candidate_path: Path,
    reference_path: Path,
    *,
    trim: int,
    landmask: np.ndarray,
) -> dict[str, Any]:
    result = {}
    with netCDF4.Dataset(candidate_path) as candidate, netCDF4.Dataset(reference_path) as reference:
        for name in VARIABLES:
            if name not in candidate.variables or name not in reference.variables:
                result[name] = {
                    "present": False,
                    "candidate_present": name in candidate.variables,
                    "reference_present": name in reference.variables,
                }
                continue
            left = interior(variable_values(candidate, name), trim, landmask)
            right = interior(variable_values(reference, name), trim, landmask)
            result[name] = {"present": True, **comparison(left, right)}
    return result


def metric_delta(candidate: dict[str, Any], legacy: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for name in VARIABLES:
        new, old = candidate[name], legacy[name]
        if (
            not new.get("present") or not old.get("present")
            or new.get("rmse") is None or old.get("rmse") is None
        ):
            result[name] = {"comparable": False, "reason": "missing_or_no_joint_finite_support"}
            continue
        new_rmse, old_rmse = float(new["rmse"]), float(old["rmse"])
        result[name] = {
            "comparable": True,
            "candidate_rmse": new_rmse,
            "legacy_rmse": old_rmse,
            "candidate_minus_legacy_rmse": new_rmse - old_rmse,
            "candidate_over_legacy_rmse": (
                new_rmse / old_rmse if old_rmse else (0.0 if new_rmse == 0 else None)
            ),
            "candidate_improved": new_rmse < old_rmse,
        }
    return result


def publish(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent,
        prefix=f".{path.name}.", delete=False,
    ) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)
    Path(f"{path}.ready").write_text(sha256(path) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-contract", required=True, type=Path)
    parser.add_argument("--baseline-completion", required=True, type=Path)
    parser.add_argument("--candidate-completion", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    if args.report.exists() or Path(f"{args.report}.ready").exists():
        raise SystemExit(f"refusing to overwrite publication: {args.report}")
    contract = require_json(args.baseline_contract)
    baseline = require_json(args.baseline_completion)
    candidate = require_json(args.candidate_completion)
    baseline_chains = {item["chain_id"]: item for item in baseline["chains"]}
    candidate_chains = {item["chain_id"]: item for item in candidate["chains"]}
    reference_index = restart_index(baseline_chains[contract["reference"]["chain_id"]])
    trim = int(contract["diagnostics"]["interior_boundary_trim_cells"])
    with netCDF4.Dataset(contract["reference"]["static_file"]) as static:
        landmask = np.asarray(static.variables["landmask"][:]) > 0
    origins = {}
    for origin_name in ORIGINS:
        origin = datetime.strptime(origin_name, "%Y%m%d")
        legacy_index = restart_index(baseline_chains[f"origin-{origin_name}"])
        native_index = restart_index(candidate_chains[f"native-origin-{origin_name}"])
        rows = []
        for age in range(6, 73, 6):
            valid = origin + timedelta(hours=age)
            if valid not in reference_index or valid not in legacy_index or valid not in native_index:
                raise ValueError(f"missing matched restart at {valid}")
            legacy_metrics = compare_restart_pair(
                legacy_index[valid], reference_index[valid], trim=trim, landmask=landmask
            )
            candidate_metrics = compare_restart_pair(
                native_index[valid], reference_index[valid], trim=trim, landmask=landmask
            )
            rows.append(
                {
                    "model_age_hours": age,
                    "valid_time": valid.isoformat(),
                    "legacy_vs_reference": legacy_metrics,
                    "candidate_vs_reference": candidate_metrics,
                    "candidate_vs_legacy_error": metric_delta(candidate_metrics, legacy_metrics),
                }
            )
        origins[origin_name] = rows
    payload = {
        "schema_version": 1,
        "status": "PASS",
        "scope": "post-hoc residual NoahMP restart-state characterization",
        "decision_limit": (
            "No frozen thresholds exist for these internal stores. This report diagnoses "
            "which reset states converge or persist; it does not independently promote a method."
        ),
        "variables": list(VARIABLES),
        "origins": origins,
        "sources": {
            "baseline_contract": str(args.baseline_contract.resolve()),
            "baseline_contract_sha256": sha256(args.baseline_contract),
            "baseline_completion": str(args.baseline_completion.resolve()),
            "baseline_completion_sha256": sha256(args.baseline_completion),
            "candidate_completion": str(args.candidate_completion.resolve()),
            "candidate_completion_sha256": sha256(args.candidate_completion),
            "assessor": str(Path(__file__).resolve()),
            "assessor_sha256": sha256(Path(__file__).resolve()),
        },
    }
    publish(args.report, payload)
    print(json.dumps({"status": "PASS", "origins": list(origins)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
