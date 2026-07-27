"""Frozen source-qualification contract for month-scale HICAR execution."""

from __future__ import annotations

from pathlib import Path
import re


REQUIRED_EVIDENCE = (
    "clean_target_build",
    "restart_continuity",
    "representative_bridge_run",
    "national_short_run",
    "preexisting_field_equivalence",
    "solver_gate_equivalence",
)
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
OUTPUT_DIAGNOSTIC_ONLY = "OUTPUT_DIAGNOSTIC_ONLY"
SCIENTIFIC_BASELINE_TRANSITION = "SCIENTIFIC_BASELINE_TRANSITION"
SUPPORTED_QUALIFICATION_MODES = {
    OUTPUT_DIAGNOSTIC_ONLY,
    SCIENTIFIC_BASELINE_TRANSITION,
}


def _valid_sha256(value: object) -> bool:
    return SHA256_PATTERN.fullmatch(str(value or "")) is not None


def validate_baseline_transition_source_qualification(
    report: dict,
    *,
    expected_child_commit: str | None,
    required_parent_commit: str | None,
) -> list[str]:
    """Validate a full scientific-baseline transition nomination."""

    failures: list[str] = []
    if report.get("schema_version") != 1:
        failures.append("source qualification schema_version is not 1")
    if report.get("status") != "PASS":
        failures.append("source qualification status is not PASS")
    if report.get("change_scope") != SCIENTIFIC_BASELINE_TRANSITION:
        failures.append(
            "source change_scope is not SCIENTIFIC_BASELINE_TRANSITION"
        )
    if report.get("qualification_mode") != SCIENTIFIC_BASELINE_TRANSITION:
        failures.append(
            "source qualification_mode is not SCIENTIFIC_BASELINE_TRANSITION"
        )
    if not expected_child_commit:
        failures.append("month expected child commit is not frozen")
    elif report.get("child_commit") != expected_child_commit:
        failures.append("source qualification child commit does not match month plan")
    if not required_parent_commit:
        failures.append("required parent commit is not frozen")
    elif report.get("parent_commit") != required_parent_commit:
        failures.append("source qualification parent commit does not match month plan")
    if (
        expected_child_commit
        and required_parent_commit
        and expected_child_commit == required_parent_commit
    ):
        failures.append("month baseline commit must differ from its source parent")
    previous_baseline = report.get("previous_scientific_baseline_commit")
    if not COMMIT_PATTERN.fullmatch(str(previous_baseline or "")):
        failures.append("previous scientific baseline commit is not frozen")
    elif previous_baseline == expected_child_commit:
        failures.append("new month source reuses the previous scientific baseline")

    evidence = report.get("evidence", {})
    transition = evidence.get("baseline_transition", {})
    if transition.get("status") != "PASS":
        failures.append("baseline transition evidence is not PASS")
    if not _valid_sha256(transition.get("artifact_sha256")):
        failures.append("baseline transition evidence lacks an artifact SHA-256")
    if transition.get("report_status") != "PASS":
        failures.append("baseline transition report status is not PASS")
    if transition.get("decision") != "NOMINATE_V29_FOR_CANONICAL_MONTH_SOURCE":
        failures.append("baseline transition did not nominate the month source")
    if transition.get("candidate_commit") != expected_child_commit:
        failures.append("baseline transition candidate does not match month plan")
    if set(transition.get("event_names", [])) != {"summer", "winter"}:
        failures.append("baseline transition does not cover summer and winter")
    event_statuses = transition.get("event_statuses", {})
    if any(event_statuses.get(name) != "PASS" for name in ("summer", "winter")):
        failures.append("baseline transition event status is not PASS")
    if transition.get("restart_trajectory_status") != "PASS":
        failures.append("baseline transition restart trajectory is not PASS")
    required_water_fields = {
        "precipitation",
        "runoff_surface_cumulative",
        "runoff_subsurface_cumulative",
        "evaporation_net_cumulative",
    }
    compared_fields = transition.get("restart_trajectory_fields", [])
    if not isinstance(compared_fields, list) or not required_water_fields <= set(
        compared_fields
    ):
        failures.append(
            "baseline transition restart trajectory omits cumulative water fields"
        )
    pair_runoff = transition.get("paired_total_runoff_kg_m2")
    if not isinstance(pair_runoff, (int, float)) or pair_runoff <= 0:
        failures.append("baseline transition does not exercise nonzero runoff")

    contract = evidence.get("assessment_contract", {})
    if contract.get("status") != "PASS":
        failures.append("baseline transition assessment contract is not PASS")
    if not _valid_sha256(contract.get("artifact_sha256")):
        failures.append(
            "baseline transition assessment contract lacks an artifact SHA-256"
        )
    if contract.get("contract_status") != "FROZEN":
        failures.append("baseline transition assessment contract is not FROZEN")
    if contract.get("candidate_commit") != expected_child_commit:
        failures.append("assessment contract candidate does not match month plan")
    if contract.get("candidate_parent_commit") != required_parent_commit:
        failures.append("assessment contract parent does not match month plan")

    transition_plan = evidence.get("transition_plan", {})
    if transition_plan.get("status") != "PASS":
        failures.append("baseline transition plan evidence is not PASS")
    if not _valid_sha256(transition_plan.get("artifact_sha256")):
        failures.append("baseline transition plan lacks an artifact SHA-256")
    if transition_plan.get("plan_status") != "PLANNED":
        failures.append("baseline transition plan status is not PLANNED")
    if transition_plan.get("candidate_commit") != expected_child_commit:
        failures.append("transition plan candidate does not match month plan")
    if transition_plan.get("candidate_parent_commit") != required_parent_commit:
        failures.append("transition plan parent does not match month plan")
    if transition_plan.get("preserved_event_commit") != previous_baseline:
        failures.append("transition plan previous baseline identity is inconsistent")

    bundle = evidence.get("candidate_source_bundle", {})
    if bundle.get("status") != "PASS":
        failures.append("candidate source bundle evidence is not PASS")
    if not _valid_sha256(bundle.get("artifact_sha256")):
        failures.append("candidate source bundle lacks an artifact SHA-256")
    if bundle.get("source_commit") != expected_child_commit:
        failures.append("candidate source bundle does not identify the month source")

    authorization = report.get("authorization", {})
    if authorization.get("month_source") is not True:
        failures.append("baseline transition does not authorize month-source use")
    for name in (
        "month_compute",
        "annual_cycle",
        "twenty_year_200m_production",
        "hundred_meter_scientific_production",
    ):
        if authorization.get(name) is not False:
            failures.append(
                f"baseline transition source qualification over-authorizes {name}"
            )
    return failures


def validate_month_source_qualification(
    report: dict,
    *,
    expected_child_commit: str | None,
    required_parent_commit: str | None,
    qualification_mode: str = OUTPUT_DIAGNOSTIC_ONLY,
) -> list[str]:
    """Return all contract violations; an empty list means qualification PASS."""

    if qualification_mode == SCIENTIFIC_BASELINE_TRANSITION:
        return validate_baseline_transition_source_qualification(
            report,
            expected_child_commit=expected_child_commit,
            required_parent_commit=required_parent_commit,
        )
    if qualification_mode != OUTPUT_DIAGNOSTIC_ONLY:
        return [f"unsupported month source qualification mode: {qualification_mode}"]

    failures: list[str] = []
    if report.get("schema_version") != 1:
        failures.append("source qualification schema_version is not 1")
    if report.get("status") != "PASS":
        failures.append("source qualification status is not PASS")
    if report.get("change_scope") != "OUTPUT_DIAGNOSTIC_ONLY":
        failures.append("source change_scope is not OUTPUT_DIAGNOSTIC_ONLY")
    if not expected_child_commit:
        failures.append("month expected child commit is not frozen")
    elif report.get("child_commit") != expected_child_commit:
        failures.append("source qualification child commit does not match month plan")
    if not required_parent_commit:
        failures.append("required parent commit is not frozen")
    elif report.get("parent_commit") != required_parent_commit:
        failures.append("source qualification parent commit does not match event source")
    if (
        expected_child_commit
        and required_parent_commit
        and expected_child_commit == required_parent_commit
    ):
        failures.append("month child commit must differ from preserved event commit")

    ancestry = report.get("parent_ancestry", {})
    if not (
        ancestry.get("status") == "PASS"
        and ancestry.get("parent_is_ancestor") is True
        and ancestry.get("merge_base") == required_parent_commit
    ):
        failures.append("parent ancestry is not proven")

    evidence = report.get("evidence", {})
    for name in REQUIRED_EVIDENCE:
        item = evidence.get(name, {})
        if item.get("status") != "PASS":
            failures.append(f"{name} evidence is not PASS")
        if not SHA256_PATTERN.fullmatch(str(item.get("artifact_sha256", ""))):
            failures.append(f"{name} evidence lacks an artifact SHA-256")

    build = evidence.get("clean_target_build", {})
    if (
        build.get("source_tree_clean") is not True
        or build.get("source_commit") != expected_child_commit
        or not build.get("target")
    ):
        failures.append("clean target build identity is incomplete")

    equivalence = evidence.get("preexisting_field_equivalence", {})
    compared_field_count = equivalence.get("compared_field_count")
    mismatch_count = equivalence.get("mismatch_count")
    if (
        not isinstance(compared_field_count, int)
        or compared_field_count <= 0
        or not isinstance(mismatch_count, int)
        or mismatch_count != 0
    ):
        failures.append("pre-existing field equivalence is not exact")

    restart = evidence.get("restart_continuity", {})
    required_restart_fields = {
        "precipitation",
        "runoff_surface_cumulative",
        "runoff_subsurface_cumulative",
        "evaporation_net_cumulative",
    }
    compared_fields_value = restart.get("compared_fields", [])
    compared_restart_fields = (
        set(compared_fields_value)
        if isinstance(compared_fields_value, list)
        and all(isinstance(value, str) for value in compared_fields_value)
        else set()
    )
    if not required_restart_fields <= compared_restart_fields:
        failures.append(
            "restart continuity does not cover every cumulative water observable"
        )
    if (
        restart.get("source_commit") != expected_child_commit
        or restart.get("nonzero_runoff_observed") is not True
    ):
        failures.append(
            "restart continuity does not exercise nonzero runoff on the child source"
        )

    for name in ("representative_bridge_run", "national_short_run"):
        run = evidence.get(name, {})
        if (
            run.get("source_commit") != expected_child_commit
            or run.get("completion_status") != "PASS"
        ):
            failures.append(f"{name} identity or completion status is incomplete")

    solver_equivalence = evidence.get("solver_gate_equivalence", {})
    if (
        not isinstance(solver_equivalence.get("compared_gate_count"), int)
        or solver_equivalence.get("compared_gate_count", 0) <= 0
        or solver_equivalence.get("mismatch_count") != 0
    ):
        failures.append("solver-gate equivalence is not exact")
    return failures


def require_published_source_qualification(
    path: Path,
    *,
    expected_child_commit: str | None,
    required_parent_commit: str | None,
    qualification_mode: str = OUTPUT_DIAGNOSTIC_ONLY,
) -> tuple[dict | None, list[str]]:
    if not path.is_file():
        return None, [f"source qualification is missing: {path}"]
    ready = Path(f"{path}.ready")
    if not ready.is_file():
        return None, [f"source qualification lacks ready marker: {ready}"]

    import json

    with path.open(encoding="utf-8") as stream:
        report = json.load(stream)
    return report, validate_month_source_qualification(
        report,
        expected_child_commit=expected_child_commit,
        required_parent_commit=required_parent_commit,
        qualification_mode=qualification_mode,
    )
