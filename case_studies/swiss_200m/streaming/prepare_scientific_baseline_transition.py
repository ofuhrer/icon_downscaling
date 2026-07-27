#!/usr/bin/env python3
"""Publish a transition-only event plan for a scientifically new HICAR baseline.

This pathway is intentionally separate from ``month_source_contract.py``.
That contract requires an output-diagnostic-only child with exact legacy-field
and solver-gate equivalence.  A candidate that fails that contract may proceed
only by repeating the paired scientific-event and restart qualification under
the unchanged, predeclared event criteria.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
import os


SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
REQUIRED_CUMULATIVE_FIELDS = {
    "precipitation",
    "runoff_surface_cumulative",
    "runoff_subsurface_cumulative",
    "evaporation_net_cumulative",
}
REQUIRED_EVENT_REPORTS = (
    "model_chunk_completion.json",
    "scientific_validation/restart_checkpoint_diagnostics.json",
    "scientific_validation/solver_log_diagnostics.json",
    "scientific_validation/scientific_event_diagnostics.json",
    "scientific_validation/rea_l_source_comparison.json",
    "scientific_validation/swissmetnet_comparison.json",
    "scientific_validation/ogd_grid_comparison.json",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def require_publication(path: Path) -> None:
    if not path.is_file():
        raise ValueError(f"missing publication: {path}")
    ready = Path(f"{path}.ready")
    if not ready.is_file():
        raise ValueError(f"publication lacks ready marker: {ready}")


def validate_transition_inputs(
    base_plan: dict,
    source_report: dict,
    *,
    required_candidate_commit: str | None = None,
    required_parent_commit: str | None = None,
) -> tuple[str, str]:
    """Validate that a failed exact-child gate is safe to reroute to events."""

    if base_plan.get("schema_version") != 1:
        raise ValueError("base scientific plan schema_version is not 1")
    configuration = base_plan.get("configuration", {})
    if configuration.get("month_expected_hicar_commit") is not None:
        raise ValueError("base plan already authorizes a month source")

    if source_report.get("schema_version") != 1:
        raise ValueError("source qualification schema_version is not 1")
    if source_report.get("status") != "FAIL":
        raise ValueError(
            "baseline transition is only for a published failed exact-child gate"
        )
    if source_report.get("change_scope") != "OUTPUT_DIAGNOSTIC_ONLY":
        raise ValueError("source report does not describe the attempted diagnostic child")

    candidate = str(source_report.get("child_commit", ""))
    parent = str(source_report.get("parent_commit", ""))
    if not COMMIT_PATTERN.fullmatch(candidate):
        raise ValueError("candidate commit is not a full Git commit")
    if not COMMIT_PATTERN.fullmatch(parent):
        raise ValueError("candidate parent is not a full Git commit")
    if required_candidate_commit and candidate != required_candidate_commit:
        raise ValueError("source report candidate does not match required commit")
    if required_parent_commit and parent != required_parent_commit:
        raise ValueError("source report parent does not match required commit")
    ancestry = source_report.get("parent_ancestry", {})
    if not (
        ancestry.get("status") == "PASS"
        and ancestry.get("parent_is_ancestor") is True
        and ancestry.get("merge_base") == parent
    ):
        raise ValueError("candidate ancestry is not proven")

    evidence = source_report.get("evidence", {})
    build = evidence.get("clean_target_build", {})
    if not (
        build.get("status") == "PASS"
        and build.get("source_tree_clean") is True
        and build.get("source_commit") == candidate
        and SHA256_PATTERN.fullmatch(str(build.get("artifact_sha256", "")))
    ):
        raise ValueError("candidate clean build evidence is incomplete")

    bridge = evidence.get("representative_bridge_run", {})
    if not (
        bridge.get("status") == "PASS"
        and bridge.get("completion_status") == "PASS"
        and bridge.get("source_commit") == candidate
    ):
        raise ValueError("candidate representative bridge did not pass")

    national = evidence.get("national_short_run", {})
    if not (
        national.get("completion_status") == "PASS"
        and national.get("source_commit") == candidate
        and SHA256_PATTERN.fullmatch(str(national.get("artifact_sha256", "")))
    ):
        raise ValueError("candidate national run did not complete")

    restart = evidence.get("restart_continuity", {})
    compared = restart.get("compared_fields", [])
    if not (
        restart.get("status") == "PASS"
        and restart.get("source_commit") == candidate
        and restart.get("nonzero_runoff_observed") is True
        and isinstance(compared, list)
        and REQUIRED_CUMULATIVE_FIELDS <= set(compared)
    ):
        raise ValueError("candidate restart continuity is incomplete")

    equivalence = evidence.get("preexisting_field_equivalence", {})
    solver_equivalence = evidence.get("solver_gate_equivalence", {})
    if not (
        equivalence.get("status") == "FAIL"
        and isinstance(equivalence.get("compared_field_count"), int)
        and equivalence.get("compared_field_count", 0) > 0
        and isinstance(equivalence.get("mismatch_count"), int)
        and equivalence.get("mismatch_count", 0) > 0
    ):
        raise ValueError("transition lacks a demonstrated legacy-field mismatch")
    if not (
        solver_equivalence.get("status") == "FAIL"
        and isinstance(solver_equivalence.get("compared_gate_count"), int)
        and solver_equivalence.get("compared_gate_count", 0) > 0
        and isinstance(solver_equivalence.get("mismatch_count"), int)
        and solver_equivalence.get("mismatch_count", 0) > 0
    ):
        raise ValueError("transition lacks a demonstrated solver-gate mismatch")
    return candidate, parent


def build_candidate_plan(base_plan: dict, candidate: str, parent: str) -> dict:
    plan = deepcopy(base_plan)
    plan["name"] = f"{base_plan['name']}-baseline-transition"
    plan["purpose"] = (
        "Transition-only paired-event requalification of a scientifically new "
        "HICAR baseline. This plan cannot authorize month, annual, 20-year, or "
        "100 m production by itself."
    )
    configuration = plan["configuration"]
    configuration["event_expected_hicar_commit"] = candidate
    configuration["month_expected_hicar_commit"] = None
    configuration["month_required_parent_hicar_commit"] = None
    configuration["baseline_transition"] = {
        "mode": "SCIENTIFIC_BASELINE_REQUALIFICATION_ONLY",
        "candidate_commit": candidate,
        "candidate_parent_commit": parent,
        "preserved_event_commit": base_plan["configuration"][
            "event_expected_hicar_commit"
        ],
        "exact_parent_trajectory_equivalence": "FAILED_AND_NOT_WAIVED",
    }
    for stage in plan.get("stages", []):
        if stage.get("id") == "event-pilots":
            stage["status"] = "in_progress"
        elif stage.get("id") in {"month-pilot", "seasonal-cycle"}:
            stage["status"] = "blocked"
    plan["decision_rules"]["launch_200m_production"] = (
        "Never from this transition plan. A separate PASS transition assessor "
        "may nominate the candidate for the canonical month-source contract."
    )
    return plan


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-plan", type=Path, required=True)
    parser.add_argument("--source-qualification", type=Path, required=True)
    parser.add_argument("--source-bundle", type=Path, required=True)
    parser.add_argument(
        "--source-bundle-published-path",
        help=(
            "Durable reader-facing path recorded in the manifest. The local "
            "--source-bundle remains the payload that is actually hashed."
        ),
    )
    parser.add_argument("--required-candidate-commit")
    parser.add_argument("--required-parent-commit")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    require_publication(args.source_qualification)
    if not args.base_plan.is_file():
        raise SystemExit(f"missing base scientific plan: {args.base_plan}")
    if not args.source_bundle.is_file():
        raise SystemExit(f"missing candidate source bundle: {args.source_bundle}")

    base_plan = load_json(args.base_plan)
    source_report = load_json(args.source_qualification)
    try:
        candidate, parent = validate_transition_inputs(
            base_plan,
            source_report,
            required_candidate_commit=args.required_candidate_commit,
            required_parent_commit=args.required_parent_commit,
        )
    except ValueError as error:
        raise SystemExit(str(error)) from error

    candidate_plan = build_candidate_plan(base_plan, candidate, parent)
    candidate_plan_path = args.output_dir / "scientific_pilot_plan_candidate.json"
    atomic_json(candidate_plan_path, candidate_plan)
    candidate_plan_sha = sha256(candidate_plan_path)

    evidence = source_report["evidence"]
    manifest = {
        "schema_version": 1,
        "status": "PLANNED",
        "classification": "SCIENTIFIC_BASELINE_CANDIDATE",
        "candidate_commit": candidate,
        "candidate_parent_commit": parent,
        "preserved_event_commit": base_plan["configuration"][
            "event_expected_hicar_commit"
        ],
        "reason": (
            "The cumulative-water implementation passes clean build, compact, "
            "representative bridge, and national restart-policy gates, but it "
            "is not an exact trajectory-preserving diagnostic child. It must "
            "therefore repeat the full paired scientific-event qualification."
        ),
        "acknowledged_exact_child_failures": {
            "preexisting_field_equivalence": {
                "compared_field_count": evidence[
                    "preexisting_field_equivalence"
                ]["compared_field_count"],
                "mismatch_count": evidence["preexisting_field_equivalence"][
                    "mismatch_count"
                ],
            },
            "solver_gate_equivalence": {
                "compared_gate_count": evidence["solver_gate_equivalence"][
                    "compared_gate_count"
                ],
                "mismatch_count": evidence["solver_gate_equivalence"][
                    "mismatch_count"
                ],
            },
        },
        "published_inputs": {
            "base_scientific_plan": {
                "path": str(args.base_plan),
                "sha256": sha256(args.base_plan),
            },
            "failed_exact_child_qualification": {
                "path": str(args.source_qualification),
                "sha256": sha256(args.source_qualification),
            },
            "candidate_source_bundle": {
                "path": (
                    args.source_bundle_published_path
                    or str(args.source_bundle)
                ),
                "sha256": sha256(args.source_bundle),
            },
            "candidate_event_plan": {
                "path": str(candidate_plan_path),
                "sha256": candidate_plan_sha,
            },
        },
        "required_events": {
            "summer": base_plan["reference_periods"]["summer_event"],
            "winter": base_plan["reference_periods"]["winter_event"],
        },
        "required_reports_per_event": list(REQUIRED_EVENT_REPORTS),
        "required_restart_trajectory": base_plan["promotion_criteria"][
            "event_to_month"
        ]["restart_trajectory_gate"],
        "water_budget_requirements": {
            "mode": "production_cumulative",
            "required_fields": sorted(REQUIRED_CUMULATIVE_FIELDS),
            "nonzero_runoff_required": True,
            "restart_persistent": True,
            "exact_interval_semantics": "(previous_time, time]",
            "groundwater_stores_required": [
                "water_aquifer",
                "wetland_h20_store",
            ],
            "storage_gw_role": "diagnostic_only_not_summed",
        },
        "promotion_contract": {
            "candidate_event_assessment_decision": (
                "GO_MONTH_AND_100M_CAPACITY_GATE"
            ),
            "effect_of_pass": (
                "Nominate this commit for a separately published baseline-"
                "transition assessor and canonical month-source update."
            ),
            "effect_of_failure": "HOLD_AND_DIAGNOSE or STOP_AND_REDESIGN",
        },
        "authorization": {
            "month_compute": False,
            "annual_cycle": False,
            "twenty_year_200m_production": False,
            "hundred_meter_scientific_production": False,
        },
    }
    manifest_path = args.output_dir / "baseline_transition_plan.json"
    atomic_json(manifest_path, manifest)
    Path(f"{candidate_plan_path}.ready").touch()
    Path(f"{manifest_path}.ready").touch()
    print(f"published baseline transition plan: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
