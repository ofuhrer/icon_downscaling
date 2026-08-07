#!/usr/bin/env python3
"""Publish the adaptive, land-initialization-aware static A/B/C process plan."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import tempfile


BASELINE_SHA = "bf03fa8ce45270bb1bfb4e7f987b2af59e0a18db7a16b1d83fd2ed33c64b7372"
CANDIDATE_SHA = "7b470441eecbcbc45818a8479d87d2ab33603a24d7bd1baf6db76cfad8f89144"


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        Path(f"{path}.ready").write_text(f"sha256 {digest(path)}  {path.name}\n", encoding="utf-8")
    finally:
        Path(temporary).unlink(missing_ok=True)


def iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S")


def create(release_path: Path, output: Path) -> dict:
    if output.exists() or Path(f"{output}.ready").exists():
        raise ValueError(f"refusing to overwrite published plan: {output}")
    release = json.loads(release_path.read_text(encoding="utf-8"))
    if release.get("decision") != "STATIC_AUDIT_PASS_PAIRED_COUPLED_CASES_REQUIRED":
        raise ValueError("static release has not passed the paired-case entry gate")
    if release.get("candidate_sha256") != CANDIDATE_SHA:
        raise ValueError("unexpected candidate static checksum")

    arms = {
        "A": {
            "static_sha256": BASELINE_SHA,
            "land_cover": "nearest-pixel WorldCover baseline",
            "soil": "dominant 0-5 cm class",
            "nmp_opt_soil": 1,
            "depth_varying_soil": False,
        },
        "B": {
            "static_sha256": CANDIDATE_SHA,
            "land_cover": "modal 10 m WorldCover aggregation",
            "soil": "candidate dominant top-layer class",
            "nmp_opt_soil": 1,
            "depth_varying_soil": False,
        },
        "C": {
            "static_sha256": CANDIDATE_SHA,
            "land_cover": "modal 10 m WorldCover aggregation",
            "soil": "four depth-mapped SoilGrids classes",
            "nmp_opt_soil": 2,
            "depth_varying_soil": True,
        },
    }
    tiles = {
        "alpine_valley": {
            "center_latitude": 46.75,
            "center_longitude": 9.87,
            "grid": {"dx_m": 200, "nx": 101, "ny": 101},
            "purpose": "valley/katabatic and weak-wind transition response",
        },
        "glaciated_alps": {
            "center_latitude": 46.60,
            "center_longitude": 8.38,
            "grid": {"dx_m": 200, "nx": 101, "ny": 101},
            "purpose": "snow/ice and high-Alpine surface response",
        },
    }
    events = {
        "summer_valley": {"tile": "alpine_valley", "score_start": "2020-07-01T00:00:00"},
        "winter_snow": {"tile": "glaciated_alps", "score_start": "2020-01-15T00:00:00"},
        "strong_synoptic": {"tile": "alpine_valley", "score_start": "2020-02-10T00:00:00"},
        "weak_transition": {"tile": "alpine_valley", "score_start": "2014-11-21T00:00:00"},
    }
    lead_hours = [24, 48, 72, 120, 168]
    records = []
    for event_name, event in events.items():
        score_start = datetime.fromisoformat(event["score_start"])
        score_end = score_start + timedelta(hours=24)
        for arm_name in arms:
            for lead in lead_hours:
                spin_start = score_start - timedelta(hours=lead)
                prefix = f"{event_name}-{arm_name}-lead{lead:03d}h"
                records.append(
                    {
                        "id": f"{prefix}-continuous",
                        "stage": "continuous_precondition_and_score",
                        "event": event_name,
                        "tile": event["tile"],
                        "arm": arm_name,
                        "lead_hours": lead,
                        "start": iso(spin_start),
                        "end": iso(score_end),
                        "score_start": iso(score_start),
                        "score_end": iso(score_end),
                        "restart_required": False,
                        "output_interval_seconds": 900,
                        "output_profile": "static_process_case",
                    }
                )

    plan = {
        "schema": "hicar-static-process-case-plan/v3",
        "status": "EXECUTABLE_DESIGN_NO_RUNS_AUTHORIZED",
        "generator": {
            "path": str(Path(__file__).resolve()),
            "sha256": digest(Path(__file__).resolve()),
        },
        "source_release": {"path": str(release_path), "sha256": digest(release_path)},
        "arms": arms,
        "tiles": tiles,
        "events": events,
        "initialization": {
            "method": "arm-specific coupled preconditioning from published REA-L land state at each lead start",
            "lead_hours": lead_hours,
            "adaptive_order": [24, 48, 72, 120, 168],
            "minimum_selected_lead_hours": 48,
            "selection_rule": (
                "Select the shortest lead >=48 h for which its score trajectory and the next longer lead pass every "
                "state/flux/wind threshold. Confirm the next increment too when available. If 120 vs 168 h fails, "
                "classify the event/arm as initialization-sensitive and do not interpret A/B/C differences."
            ),
            "why_not_same_numeric_state": (
                "Equal volumetric water content is not an equal hydraulic state under different soil textures; "
                "each arm therefore evolves its own land state before the common scoring window."
            ),
            "scope_limit": (
                "At most seven days does not equilibrate deep soil climatology. This design estimates event response "
                "conditional on REA-L initialization; climatological production still requires continuous carry-over "
                "or a separately qualified offline Noah-MP spin-up."
            ),
            "restart_policy": (
                "Run each lead and its 24 h score window in one uninterrupted HICAR process. The synthetic restart "
                "qualification exposed a first-step land/surface transient, so restart stitching is not permitted "
                "for these attribution cases until a separate restart-continuity gate passes."
            ),
        },
        "convergence_thresholds": {
            "soil_temperature": {"median_abs_K": 0.2, "p95_abs_K": 0.5, "each_layer": True},
            "soil_water_content": {"median_abs_m3_m3": 0.005, "p95_abs_m3_m3": 0.015, "each_layer": True},
            "surface_temperature": {"hourly_rmse_K": 0.5},
            "surface_fluxes": {"daily_mean_abs_W_m2": 10.0, "variables": ["hfss", "hfls", "hfgs"]},
            "wind": {"hourly_rmse_m_s": 0.3, "direction_mae_degrees_if_speed_gt_2": 10.0},
            "invalid_values": 0,
        },
        "execution": {
            "records": records,
            "forcing_cache": "one checksum-bound valid-time cache shared by all arms/leads for each tile/event",
            "ordering": (
                "Run summer_valley and winter_snow adaptively first. Add longer leads only when required. "
                "Run strong_synoptic and weak_transition A/B/C only after seasonal initialization gates pass."
            ),
            "score_extraction": (
                "Retain the uninterrupted trajectory for provenance, but compute A/B/C metrics only over each "
                "record's score_start through score_end interval."
            ),
            "fixed_controls": [
                "forcing checksums", "tile/grid", "executable checksum", "atmospheric physics",
                "terrain", "time-step controls", "score timestamps", "output cadence",
            ],
        },
        "attribution": {
            "B_minus_A": "modal land-cover aggregation plus its arm-specific surface adjustment",
            "C_minus_B": "depth-varying soil classes and their arm-specific hydraulic adjustment",
            "primary_metrics": [
                "surface-energy closure", "skin and 2 m temperature", "soil temperature/moisture by layer",
                "PBL height", "friction velocity", "10 m and fixed-height wind speed/direction/shear",
                "valley-flow onset and reversal",
            ],
        },
        "promotion_gate": (
            "No factor is promoted from one event. Require passed initialization sensitivity, consistent benefit "
            "in at least two contrasting events, observation-facing improvement, and no energy/wind physicality regression."
        ),
        "authorization": "Plan publication does not authorize national or process-case model submission.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(output, plan)
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        plan = create(args.release.resolve(), args.output.resolve())
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps({"status": plan["status"], "records": len(plan["execution"]["records"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
