#!/usr/bin/env python3
"""Validate the frozen Swiss 100 m scientific-scaling contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


WATER_FIELDS = {
    "precipitation",
    "runoff_surface_cumulative",
    "runoff_subsurface_cumulative",
    "evaporation_net_cumulative",
}
REQUIRED_SKILL_FAMILIES = {
    "temperature",
    "precipitation",
    "wind",
    "snow",
}
REQUIRED_DECISIONS = {
    "pass": "GO_100M_MONTH_PILOT",
    "single_event_or_metric_failure": "HOLD_100M_AND_DIAGNOSE",
    "systematic_numerical_or_physical_failure": "STOP_100M_AND_REDESIGN",
}


def validate(contract: dict) -> list[str]:
    failures: list[str] = []
    if contract.get("schema_version") != 1:
        failures.append("schema_version must be 1")
    if contract.get("status") != "FROZEN_BEFORE_100M_SCIENTIFIC_EXECUTION":
        failures.append("scientific scaling gate is not frozen before execution")

    prerequisites = contract.get("prerequisites", {})
    if prerequisites.get("required_200m_month_decision") != "GO_ANNUAL_CYCLE":
        failures.append("100 m science must require a passing 200 m month")
    if (
        prerequisites.get("required_100m_capacity_decision")
        != "QUALIFIED_100M_ENGINEERING_CAPACITY_ONLY"
    ):
        failures.append("100 m science must require a passing capacity gate")

    design = contract.get("paired_event_design", {})
    events = {
        event.get("name"): event
        for event in design.get("events", [])
        if isinstance(event, dict)
    }
    if set(events) != {"summer", "winter"}:
        failures.append("paired design must include summer and winter")
    for name in ("summer", "winter"):
        if events.get(name, {}).get("duration_hours") != 72:
            failures.append(f"{name} event must span 72 hours")
    if set(design.get("resolutions_m", [])) != {100, 200}:
        failures.append("paired design must compare 100 m and 200 m")
    overlap = design.get("restart_overlap", {})
    if (
        overlap.get("restart_at_hour") != 48
        or overlap.get("comparison_end_hour") != 72
        or overlap.get("expected_records") != 8
    ):
        failures.append("restart overlap must cover hour 48 through hour 72")
    if not WATER_FIELDS <= set(overlap.get("required_fields", [])):
        failures.append("restart overlap omits cumulative water fields")

    numerical = contract.get("mandatory_numerical_and_physical_gates", {})
    if numerical.get("required_water_budget_mode") != "production_cumulative":
        failures.append("water budget must use production cumulative observables")
    if numerical.get("required_restart_trajectory_status") != "PASS":
        failures.append("restart trajectory must be a mandatory PASS")
    if numerical.get("maximum_duplicate_or_missing_output_times") != 0:
        failures.append("output timeline must be exact and gap-free")
    if numerical.get("minimum_memory_headroom_fraction_every_gpu", 0) < 0.15:
        failures.append("GPU memory headroom is below 15 percent")
    if numerical.get("minimum_memory_headroom_fraction_every_node", 0) < 0.15:
        failures.append("host memory headroom is below 15 percent")

    comparison = contract.get("matched_scientific_comparison", {})
    uncertainty = comparison.get("uncertainty", {})
    if (
        uncertainty.get("method") != "paired block bootstrap"
        or uncertainty.get("minimum_block_hours", 0) < 24
        or uncertainty.get("confidence_level") != 0.95
        or uncertainty.get("minimum_resamples", 0) < 1000
    ):
        failures.append("paired uncertainty contract is incomplete")
    skill = comparison.get("terrain_sensitive_skill_families", {})
    if set(skill.get("families", [])) != REQUIRED_SKILL_FAMILIES:
        failures.append("terrain-sensitive skill families are incomplete")
    if skill.get("minimum_families_with_positive_median_skill", 0) < 2:
        failures.append("too few terrain-sensitive families must improve")
    if (
        skill.get(
            "minimum_families_with_positive_95_percent_lower_confidence_bound",
            0,
        )
        < 1
    ):
        failures.append("no statistically robust added-value family is required")
    degradation = skill.get("maximum_allowed_family_median_skill_degradation")
    if not isinstance(degradation, (int, float)) or degradation < -0.05:
        failures.append("allowed family-level degradation exceeds five percent")

    decisions = contract.get("decisions", {})
    for name, expected in REQUIRED_DECISIONS.items():
        if decisions.get(name) != expected:
            failures.append(f"{name} decision is not frozen")
    authorization = contract.get("authorization", {}).get("on_pass", {})
    if authorization.get("hundred_meter_month_pilot") is not True:
        failures.append("passing gate does not authorize the 100 m month pilot")
    for name in (
        "hundred_meter_annual_cycle",
        "hundred_meter_twenty_year_production",
    ):
        if authorization.get(name) is not False:
            failures.append(f"scientific event gate over-authorizes {name}")
    month = contract.get("follow_on_month_gate", {})
    if (
        month.get("duration_days") != 31
        or month.get("declared_spinup_days") != 7
        or month.get("minimum_retained_days", 0) < 21
        or month.get("minimum_post_restart_overlap_hours", 0) < 24
        or month.get("pass_decision") != "GO_100M_ANNUAL_CYCLE"
    ):
        failures.append("100 m follow-on month gate is incomplete")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("contract", type=Path)
    args = parser.parse_args()
    with args.contract.open(encoding="utf-8") as stream:
        contract = json.load(stream)
    failures = validate(contract)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("PASS: Swiss 100 m scientific-scaling contract is internally complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
