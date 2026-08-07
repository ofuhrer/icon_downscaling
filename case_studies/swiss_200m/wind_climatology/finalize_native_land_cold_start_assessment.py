#!/usr/bin/env python3
"""Compare the native-SMI cold starts with the matching legacy reset origins."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


ORIGINS = ("20200702", "20200703")
RESET_METRICS = {
    "tsfe_rmse_k": ("tsfe", "rmse", "surface_reset_materiality", "tsfe_rmse_k", False),
    "hfss_mean_bias_abs_w_m2": (
        "hfss", "mean_bias", "surface_reset_materiality",
        "hfss_absolute_mean_bias_w_m2", True,
    ),
    "hfls_mean_bias_abs_w_m2": (
        "hfls", "mean_bias", "surface_reset_materiality",
        "hfls_absolute_mean_bias_w_m2", True,
    ),
    "soil_temperature_rmse_k": (
        "soil_temperature", "rmse", "slow_state_reset_materiality",
        "soil_temperature_rmse_k", False,
    ),
    "soil_water_rmse_m3_m3": (
        "soil_water_content", "rmse", "slow_state_reset_materiality",
        "soil_water_rmse_m3_m3", False,
    ),
    "soil_column_water_mean_bias_abs_kg_m2": (
        "soil_column_total_water", "mean_bias", "slow_state_reset_materiality",
        "soil_column_water_absolute_mean_bias_kg_m2", True,
    ),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_report(path: Path) -> dict[str, Any]:
    ready = Path(f"{path}.ready")
    if not path.is_file() or not ready.is_file():
        raise ValueError(f"assessment is not published: {path}")
    payload = json.loads(path.read_text())
    if payload.get("status") != "PASS":
        raise ValueError(f"assessment is not passing: {path}")
    return payload


def threshold_value(thresholds: dict[str, Any], group: str, name: str) -> float:
    return float(thresholds[group][name])


def reset_metric_rows(
    legacy: dict[str, Any], candidate: dict[str, Any], thresholds: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    rows: dict[str, Any] = {}
    passes = True
    for origin in ORIGINS:
        old = legacy["window_vs_reference"][f"origin-{origin}"]["scalars"]
        new = candidate["window_vs_reference"][f"native-origin-{origin}"]["scalars"]
        origin_rows = {}
        for label, (variable, statistic, group, threshold_name, absolute) in RESET_METRICS.items():
            old_value = float(old[variable][statistic])
            new_value = float(new[variable][statistic])
            if absolute:
                old_value = abs(old_value)
                new_value = abs(new_value)
            limit = threshold_value(thresholds, group, threshold_name)
            ratio = new_value / old_value if old_value else (0.0 if new_value == 0 else None)
            passed = new_value <= limit
            passes = passes and passed
            origin_rows[label] = {
                "legacy": old_value,
                "candidate": new_value,
                "candidate_minus_legacy": new_value - old_value,
                "candidate_over_legacy": ratio,
                "candidate_improved": new_value < old_value,
                "threshold": limit,
                "candidate_pass": passed,
            }
        rows[origin] = origin_rows
    return rows, passes


def wind_passes(window: dict[str, Any], thresholds: dict[str, Any]) -> bool:
    limits = thresholds["wind"]
    return all(
        item["vector_rmse_m_s"] <= limits["vector_rmse_m_s"]
        and item["relative_vector_rmse"] <= limits["relative_vector_rmse"]
        and abs(item["speed_bias_m_s"]) <= limits["absolute_speed_bias_m_s"]
        and item["direction_mae_degrees"] <= limits["direction_mae_degrees"]
        and item["max_time_vector_error_p99_m_s"] <= limits["vector_error_p99_m_s"]
        for item in window["wind"].values()
    )


def pbl_passes(window: dict[str, Any], thresholds: dict[str, Any]) -> bool:
    item = window["scalars"]["hpbl"]
    limits = thresholds["pbl"]
    return (
        item["relative_rmse"] <= limits["relative_hpbl_rmse"]
        and abs(item["mean_bias"]) <= limits["absolute_hpbl_mean_bias_m"]
    )


def atmospheric_rows(
    legacy: dict[str, Any], candidate: dict[str, Any], thresholds: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    rows = {}
    passes = True
    for origin in ORIGINS:
        old = legacy["window_vs_reference"][f"origin-{origin}"]
        new = candidate["window_vs_reference"][f"native-origin-{origin}"]
        heights = {}
        for height, metrics in new["wind"].items():
            old_metrics = old["wind"][height]
            heights[height] = {
                "vector_rmse_m_s": {
                    "legacy": old_metrics["vector_rmse_m_s"],
                    "candidate": metrics["vector_rmse_m_s"],
                    "candidate_minus_legacy": (
                        metrics["vector_rmse_m_s"] - old_metrics["vector_rmse_m_s"]
                    ),
                },
                "relative_vector_rmse": {
                    "legacy": old_metrics["relative_vector_rmse"],
                    "candidate": metrics["relative_vector_rmse"],
                    "candidate_minus_legacy": (
                        metrics["relative_vector_rmse"]
                        - old_metrics["relative_vector_rmse"]
                    ),
                },
            }
        wind_ok = wind_passes(new, thresholds)
        pbl_ok = pbl_passes(new, thresholds)
        passes = passes and wind_ok and pbl_ok
        rows[origin] = {
            "candidate_wind_pass": wind_ok,
            "candidate_pbl_pass": pbl_ok,
            "hpbl": {
                "legacy_rmse_m": old["scalars"]["hpbl"]["rmse"],
                "candidate_rmse_m": new["scalars"]["hpbl"]["rmse"],
                "legacy_mean_bias_m": old["scalars"]["hpbl"]["mean_bias"],
                "candidate_mean_bias_m": new["scalars"]["hpbl"]["mean_bias"],
            },
            "wind": heights,
        }
    return rows, passes


def final_decision(*, atmospheric: bool, reset: bool, seam: bool, evolution: bool) -> str:
    if atmospheric and reset and seam and evolution:
        return "PROMOTE_NATIVE_SMI_TO_CONTRASTING_REGIME"
    if atmospheric and evolution:
        return "RETAIN_NATIVE_REMAP_ISOLATE_SMI_OR_RESIDUAL_STATE"
    return "REJECT_CURRENT_COLD_START_INTERVENTION"


def build_report(
    legacy: dict[str, Any],
    candidate: dict[str, Any],
    *,
    legacy_path: Path,
    candidate_path: Path,
) -> dict[str, Any]:
    if legacy.get("thresholds") != candidate.get("thresholds"):
        raise ValueError("legacy and candidate assessments do not share frozen thresholds")
    thresholds = candidate["thresholds"]
    reset_rows, reset_ok = reset_metric_rows(legacy, candidate, thresholds)
    atmospheric, atmospheric_ok = atmospheric_rows(legacy, candidate, thresholds)
    seam_ok = bool(candidate["checks"]["seam_excess_nonmaterial"])
    evolution_ok = bool(
        candidate["checks"]["native_restart_wind_evolves"]
        and candidate["checks"]["fixed_height_wind_evolves"]
    )
    decision = final_decision(
        atmospheric=atmospheric_ok,
        reset=reset_ok,
        seam=seam_ok,
        evolution=evolution_ok,
    )
    return {
        "schema_version": 1,
        "status": "PASS",
        "assessor": {
            "path": str(Path(__file__).resolve()),
            "sha256": sha256(Path(__file__).resolve()),
        },
        "decision": decision,
        "method_pass": decision == "PROMOTE_NATIVE_SMI_TO_CONTRASTING_REGIME",
        "checks": {
            "matched_origins_present": True,
            "frozen_thresholds_identical": True,
            "candidate_retained_day_wind_pbl": atmospheric_ok,
            "candidate_retained_day_slow_state": reset_ok,
            "candidate_selected_core_seams": seam_ok,
            "candidate_wind_evolves": evolution_ok,
            "legacy_same_initial_state_control": bool(
                legacy["checks"].get("same_initial_state_control", False)
            ),
        },
        "interpretation": {
            "causal_contrast": (
                "Only the 2 and 3 July cold-start initialization changes; the "
                "reference, forcing, executable, retained model ages, and thresholds are unchanged."
            ),
            "control_policy": (
                "The legacy same-initial-state control remains diagnostic but is not a gate "
                "for the matched cold-start intervention because it already failed before "
                "the intervention and would make success logically impossible."
            ),
            "decision_meaning": {
                "PROMOTE_NATIVE_SMI_TO_CONTRASTING_REGIME": (
                    "The candidate passes the frozen July retained-day state, wind/PBL, "
                    "seam, and wind-evolution checks; test the same initializer in winter "
                    "or strong wind before any production claim."
                ),
                "RETAIN_NATIVE_REMAP_ISOLATE_SMI_OR_RESIDUAL_STATE": (
                    "Atmospheric behavior remains acceptable, but the reset-state or seam "
                    "problem persists; retain direct native remapping and isolate SMI versus "
                    "uninitialized residual NoahMP stores."
                ),
                "REJECT_CURRENT_COLD_START_INTERVENTION": (
                    "The candidate fails retained-day wind/PBL or wind-evolution evidence."
                ),
            }[decision],
        },
        "sources": {
            "legacy_assessment": str(legacy_path.resolve()),
            "legacy_assessment_sha256": sha256(legacy_path),
            "candidate_assessment": str(candidate_path.resolve()),
            "candidate_assessment_sha256": sha256(candidate_path),
        },
        "reset_state_candidate_vs_legacy": reset_rows,
        "atmospheric_candidate_vs_legacy": atmospheric,
        "thresholds": thresholds,
    }


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
    parser.add_argument("--legacy-assessment", required=True, type=Path)
    parser.add_argument("--candidate-assessment", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    if args.report.exists() or Path(f"{args.report}.ready").exists():
        raise SystemExit(f"refusing to overwrite publication: {args.report}")
    payload = build_report(
        require_report(args.legacy_assessment),
        require_report(args.candidate_assessment),
        legacy_path=args.legacy_assessment,
        candidate_path=args.candidate_assessment,
    )
    publish(args.report, payload)
    print(json.dumps({"decision": payload["decision"], "checks": payload["checks"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
