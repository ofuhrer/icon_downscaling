#!/usr/bin/env python3
"""Assess Sx and density-coupling screens from bounded HICAR replays."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any


def load_mechanism(path: Path):
    spec = importlib.util.spec_from_file_location(
        "wind_spinup_mechanism_metrics", path
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def publish(path: Path, payload: dict[str, Any]) -> None:
    if path.exists() or Path(f"{path}.ready").exists():
        raise ValueError(f"refusing to replace assessment: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)
    Path(f"{path}.ready").touch()


def require_completion(run: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    path = Path(run["run_dir"]) / "model_chunk_completion.json"
    if not path.is_file() or not Path(f"{path}.ready").is_file():
        raise ValueError(f"run completion is not published: {path}")
    payload = json.loads(path.read_text())
    if payload.get("status") != "PASS":
        raise ValueError(f"run completion is not PASS: {path}")
    namelist = (Path(run["run_dir"]) / "input.nml").read_text()
    sx_expected = ".True." if run["sx"] == "on" else ".False."
    density_expected = (
        ".True." if run["advect_density"] == "on" else ".False."
    )
    if not re.search(
        rf"(?im)^\s*Sx\s*=\s*{re.escape(sx_expected)}", namelist
    ):
        raise ValueError(f"Sx configuration mismatch: {path}")
    if not re.search(
        rf"(?im)^\s*advect_density\s*=\s*"
        rf"{re.escape(density_expected)}",
        namelist,
    ):
        raise ValueError(f"density configuration mismatch: {path}")
    return path, payload


def ratio(value: float, baseline: float) -> float | None:
    return value / baseline if baseline > 0.0 else None


def pathway_label(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "UNRESOLVED"
    if value < 0.5:
        return "MAJOR_REDUCTION"
    if value < 0.8:
        return "MATERIAL_REDUCTION"
    return "NOT_DOMINANT"


def assess(
    experiment_path: Path,
    mechanism_script: Path,
    output_path: Path,
    boundary_cells: int,
    sample_stride: int,
) -> dict[str, Any]:
    if not experiment_path.is_file() or not Path(
        f"{experiment_path}.ready"
    ).is_file():
        raise ValueError(f"experiment plan is not published: {experiment_path}")
    experiment = json.loads(experiment_path.read_text())
    metrics_module = load_mechanism(mechanism_script)
    completed: dict[tuple[str, int, str, str], tuple[Path, dict[str, Any]]] = {}
    unsupported_density_runs = []
    for run in experiment["runs"]:
        key = (
            run["case_id"],
            run["spinup_hours"],
            run["sx"],
            run["advect_density"],
        )
        completion = Path(run["run_dir"]) / "model_chunk_completion.json"
        if completion.is_file() and Path(f"{completion}.ready").is_file():
            completed[key] = require_completion(run)
        elif run["advect_density"] == "off":
            model_log = Path(run["run_dir"]) / "model.out"
            text = model_log.read_text(errors="replace") if model_log.is_file() else ""
            unsupported_density_runs.append(
                {
                    "run_id": run["run_id"],
                    "model_log": str(model_log),
                    "adjoint_conservation_gate_rejected": (
                        "adjoint projection rejected by conservation gate"
                        in text
                    ),
                }
            )
        else:
            raise ValueError(
                f"required density-on replay is not published: {run['run_id']}"
            )

    cases = []
    any_intervention_active = False
    for case in experiment["cases"]:
        case_id = case["case_id"]
        factors = []
        for sx in ("on", "off"):
            for density in ("on", "off"):
                if (
                    (case_id, 24, sx, density) not in completed
                    or (case_id, 48, sx, density) not in completed
                ):
                    continue
                candidate_path, candidate = completed[
                    (case_id, 24, sx, density)
                ]
                reference_path, reference = completed[
                    (case_id, 48, sx, density)
                ]
                if candidate["end"] != reference["end"]:
                    raise ValueError(f"factor final times differ for {case_id}")
                comparison = metrics_module.compare_restarts(
                    Path(candidate["restart"]["path"]),
                    Path(reference["restart"]["path"]),
                    boundary_cells,
                    sample_stride,
                )
                factors.append(
                    {
                        "sx": sx,
                        "advect_density": density,
                        "candidate_completion": str(candidate_path),
                        "reference_completion": str(reference_path),
                        "metrics": comparison,
                    }
                )
        keyed = {
            (item["sx"], item["advect_density"]): item
            for item in factors
        }
        baseline = keyed[("on", "on")]["metrics"]
        intervention_effect = {}
        for age in (24, 48):
            baseline_path, baseline_completion = completed[
                (case_id, age, "on", "on")
            ]
            sx_path, sx_completion = completed[
                (case_id, age, "off", "on")
            ]
            effect = metrics_module.compare_restarts(
                Path(sx_completion["restart"]["path"]),
                Path(baseline_completion["restart"]["path"]),
                boundary_cells,
                sample_stride,
            )
            intervention_effect[str(age)] = {
                "baseline_completion": str(baseline_path),
                "sx_off_completion": str(sx_path),
                "full_level_vector_rmse_m_s": effect[
                    "wind_full_levels"
                ]["vector_rmse_m_s"],
                "wind_10m_vector_rmse_m_s": effect["wind_10m"][
                    "vector_rmse_m_s"
                ],
            }
        intervention_active = not all(
            item["full_level_vector_rmse_m_s"] <= 1.0e-6
            and item["wind_10m_vector_rmse_m_s"] <= 1.0e-6
            for item in intervention_effect.values()
        )
        any_intervention_active = (
            any_intervention_active or intervention_active
        )
        screens = {}
        for name, key in (
            ("sx_removed", ("off", "on")),
            ("density_coupling_removed", ("on", "off")),
            ("both_removed", ("off", "off")),
        ):
            if key not in keyed:
                screens[name] = {
                    "status": "UNAVAILABLE",
                    "reason": (
                        "The qualified adjoint projection rejected the "
                        "advect_density=off branch at its conservation gate."
                    ),
                }
                continue
            selected = keyed[key]["metrics"]
            full_ratio = ratio(
                selected["wind_full_levels"]["vector_rmse_m_s"],
                baseline["wind_full_levels"]["vector_rmse_m_s"],
            )
            wind10_ratio = ratio(
                selected["wind_10m"]["vector_rmse_m_s"],
                baseline["wind_10m"]["vector_rmse_m_s"],
            )
            screens[name] = {
                "status": "PASS",
                "full_level_difference_ratio_to_baseline": full_ratio,
                "wind_10m_difference_ratio_to_baseline": wind10_ratio,
                "full_level_interpretation": pathway_label(full_ratio),
                "wind_10m_interpretation": pathway_label(wind10_ratio),
            }
        cases.append(
            {
                "case_id": case_id,
                "baseline": {
                    "full_level_vector_rmse_m_s": baseline[
                        "wind_full_levels"
                    ]["vector_rmse_m_s"],
                    "wind_10m_vector_rmse_m_s": baseline["wind_10m"][
                        "vector_rmse_m_s"
                    ],
                    "density_rmse_kg_m3": baseline["atmospheric_state"][
                        "density"
                    ]["rmse"],
                    "potential_temperature_rmse_K": baseline[
                        "atmospheric_state"
                    ]["potential_temperature"]["rmse"],
                    "ustar_rmse_m_s": baseline["surface_state"]["ustar"][
                        "rmse"
                    ],
                },
                "screens": screens,
                "intervention_active": intervention_active,
                "intervention_effect": intervention_effect,
                "factors": factors,
            }
        )
    result = {
        "schema_version": 1,
        "status": "PASS",
        "decision": "SX_SCREEN_COMPLETE_DENSITY_SCREEN_UNSUPPORTED",
        "experiment_plan": str(experiment_path),
        "cases": cases,
        "unsupported_density_runs": unsupported_density_runs,
        "any_sx_effect_above_1e_6_m_s": any_intervention_active,
        "interpretation_contract": {
            "Sx": (
                "Sx off changes only the explicit stability-dependent terrain "
                "sheltering branch in the configured wind update."
            ),
            "advect_density": (
                "advect_density off changes both density-weighted wind "
                "projection and density-aware advection. A positive screen "
                "requires a projection-only follow-up; a negative screen "
                "rules out this combined pathway as dominant."
            ),
            "ratio": (
                "Ratio of the 24h-versus-48h same-valid-time difference under "
                "the intervention to the production on/on difference."
            ),
        },
    }
    publish(output_path, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-plan", type=Path, required=True)
    parser.add_argument("--mechanism-script", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--boundary-cells", type=int, default=20)
    parser.add_argument("--sample-stride", type=int, default=4)
    args = parser.parse_args()
    result = assess(
        args.experiment_plan.resolve(),
        args.mechanism_script.resolve(),
        args.output.resolve(),
        args.boundary_cells,
        args.sample_stride,
    )
    print(f"wind pathway assessment: {len(result['cases'])} cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
