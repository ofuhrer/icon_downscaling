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


def validate_month_source_qualification(
    report: dict,
    *,
    expected_child_commit: str | None,
    required_parent_commit: str | None,
) -> list[str]:
    """Return all contract violations; an empty list means qualification PASS."""

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
    )
