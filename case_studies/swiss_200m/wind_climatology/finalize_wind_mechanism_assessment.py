#!/usr/bin/env python3
"""Publish the evidence-bound HICAR wind-memory mechanism conclusion."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require(path: Path) -> dict[str, Any]:
    if not path.is_file() or not Path(f"{path}.ready").is_file():
        raise ValueError(f"required publication is missing: {path}")
    payload = json.loads(path.read_text())
    if payload.get("status") != "PASS":
        raise ValueError(f"required publication is not PASS: {path}")
    return payload


def artifact(path: Path) -> dict[str, Any]:
    return {"path": str(path), "sha256": sha256(path)}


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


def classify_sx(ratios: list[float]) -> str:
    if all(value < 0.5 for value in ratios):
        return "SX_DOMINANT"
    if any(value < 0.8 for value in ratios):
        return "SX_MATERIAL_REGIME_DEPENDENT"
    return "SX_NOT_DOMINANT"


def finalize(
    mechanism_dir: Path,
    forcing_path: Path,
    pathway_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    forcing = require(forcing_path)
    pathway = require(pathway_path)
    if not forcing.get("arrays_identical"):
        raise ValueError("forcing-array identity did not pass")

    case_summaries = []
    sources = [artifact(forcing_path), artifact(pathway_path)]
    for report_path in sorted(mechanism_dir.glob("*.json")):
        if report_path.name in {
            forcing_path.name,
            pathway_path.name,
            output_path.name,
        }:
            continue
        if not Path(f"{report_path}.ready").is_file():
            continue
        report = json.loads(report_path.read_text())
        if report.get("decision") != "MECHANISM_EVIDENCE_READY":
            continue
        comparison = next(
            item for item in report["comparisons"]
            if item["spinup_hours"] == 24
        )
        metrics = comparison["metrics"]
        case_summaries.append(
            {
                "case_id": report["case_id"],
                "valid_time": report["final_valid_time"],
                "wind_full_level_rmse_m_s": metrics[
                    "wind_full_levels"
                ]["vector_rmse_m_s"],
                "wind_10m_rmse_m_s": metrics["wind_10m"][
                    "vector_rmse_m_s"
                ],
                "pressure_rmse_Pa": metrics["atmospheric_state"][
                    "pressure"
                ]["rmse"],
                "density_rmse_kg_m3": metrics["atmospheric_state"][
                    "density"
                ]["rmse"],
                "potential_temperature_rmse_K": metrics[
                    "atmospheric_state"
                ]["potential_temperature"]["rmse"],
                "qv_rmse_kg_kg": metrics["atmospheric_state"]["qv"][
                    "rmse"
                ],
                "ustar_rmse_m_s": metrics["surface_state"]["ustar"][
                    "rmse"
                ],
                "hpbl_rmse_m": metrics["surface_state"]["hpbl"]["rmse"],
            }
        )
        sources.append(artifact(report_path))
    if not case_summaries:
        raise ValueError("no mechanism case reports found")
    if not all(item["pressure_rmse_Pa"] == 0.0 for item in case_summaries):
        raise ValueError("pressure identity invariant did not hold")

    sx_cases = []
    ratios = []
    for case in pathway["cases"]:
        screen = case["screens"]["sx_removed"]
        if screen.get("status") != "PASS":
            raise ValueError(f"Sx screen is unavailable: {case['case_id']}")
        full_ratio = screen["full_level_difference_ratio_to_baseline"]
        wind10_ratio = screen["wind_10m_difference_ratio_to_baseline"]
        ratios.extend((full_ratio, wind10_ratio))
        sx_cases.append(
            {
                "case_id": case["case_id"],
                "full_level_difference_ratio": full_ratio,
                "wind_10m_difference_ratio": wind10_ratio,
                "full_level_interpretation": screen[
                    "full_level_interpretation"
                ],
                "wind_10m_interpretation": screen[
                    "wind_10m_interpretation"
                ],
            }
        )
    sx_class = classify_sx(ratios)
    if sx_class == "SX_DOMINANT":
        conclusion = (
            "State-dependent Sx sheltering is the dominant amplifier of "
            "cold-start-age wind differences. Its stability input comes from "
            "the evolving HICAR thermodynamic trajectory."
        )
    elif sx_class == "SX_MATERIAL_REGIME_DEPENDENT":
        conclusion = (
            "Sx materially amplifies cold-start-age differences in at least "
            "one regime, but a substantial residual remains in the coupled "
            "thermodynamic, PBL, advection, and density-weighted projection path."
        )
    else:
        conclusion = (
            "Removing Sx does not materially remove the cold-start-age "
            "difference. The established memory source is the coupled "
            "thermodynamic/PBL/land/advection trajectory. The density-weighted "
            "projection consumes the differing density field and is therefore "
            "a plausible propagator, but it was not independently isolated."
        )

    payload = {
        "schema_version": 1,
        "status": "PASS",
        "decision": "COUPLED_HICAR_SEGMENT_STRATEGY_REJECTED",
        "forcing_identity": {
            "arrays_identical": forcing["arrays_identical"],
            "container_byte_identical": forcing[
                "container_byte_identical"
            ],
            "differing_global_attributes": [
                name
                for name, identical in forcing[
                    "global_attributes_identical"
                ].items()
                if not identical
            ],
        },
        "same_valid_time_24h_vs_48h": case_summaries,
        "pathway_screen": {
            "sx_classification": sx_class,
            "sx_cases": sx_cases,
            "density_screen": {
                "status": "UNSUPPORTED_BY_QUALIFIED_SOLVER",
                "reason": (
                    "advect_density=False was accepted as a deliberate restart "
                    "override but the adjoint solver rejected the result at its "
                    "independent conservation gate. The gate was not disabled."
                ),
            },
        },
        "mechanism": {
            "conclusion": conclusion,
            "code_path": [
                "Each hourly wind solve replaces its target U/V with forcing.",
                "The target is then modified by stability-dependent Sx.",
                "The variational solve projects it using the current density.",
                "Between solves, PBL, surface, land, thermodynamic, and "
                "advection state evolve prognostically.",
            ],
            "invariants": [
                "Independent forcing conversions have bit-identical arrays.",
                "Same-valid-time pressure is exactly identical across ages.",
                "Theta, qv, density, PBL height, friction velocity, and surface "
                "temperature differ materially across ages.",
                "The divergence is domainwide rather than confined to boundaries.",
            ],
        },
        "production_decision": {
            "coupled_26h_segments": "HOLD",
            "reason": (
                "One-hour overlap and discarded spinup do not make independent "
                "coupled trajectories interchangeable; segment age changes the "
                "wind climatology at the same valid time."
            ),
            "recommended_next_gate": "INSTANTANEOUS_WIND_ONLY_DOWNSCALING",
            "gate_design": [
                "Run HICAR wind_only independently at each forcing valid time, "
                "so source theta/qv/density—not a free HICAR trajectory—conditions "
                "Sx and the variational projection.",
                "Prove same-valid-time invariance across launch order and process "
                "restart, then validate speed, direction, vertical shear, and "
                "terrain response against stations and source-scale aggregates.",
                "Treat 10 m wind, gusts, and turbine-height shear as separately "
                "qualified diagnostics because wind_only omits an evolved local "
                "PBL and land-surface history.",
            ],
            "fallback": (
                "If wind_only cannot provide physically defensible surface and "
                "hub-height diagnostics, use an observation-calibrated diagnostic "
                "terrain/roughness downscaling anchored directly to ICON winds "
                "rather than a free-running coupled HICAR climatology."
            ),
        },
        "sources": sources,
    }
    publish(output_path, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mechanism-dir", type=Path, required=True)
    parser.add_argument("--forcing-comparison", type=Path, required=True)
    parser.add_argument("--pathway-assessment", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = finalize(
        args.mechanism_dir.resolve(),
        args.forcing_comparison.resolve(),
        args.pathway_assessment.resolve(),
        args.output.resolve(),
    )
    print(
        f"wind mechanism conclusion: "
        f"{payload['pathway_screen']['sx_classification']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
