from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = (
    ROOT
    / "case_studies"
    / "swiss_100m"
    / "config"
    / "scientific_scaling_gate.json"
)
MODULE_PATH = (
    ROOT
    / "case_studies"
    / "swiss_100m"
    / "validation"
    / "validate_scientific_scaling_gate.py"
)
SPEC = importlib.util.spec_from_file_location(
    "validate_100m_scientific_scaling_gate", MODULE_PATH
)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def contract() -> dict:
    return json.loads(CONTRACT.read_text())


def test_frozen_100m_scientific_scaling_contract_is_complete() -> None:
    assert MODULE.validate(contract()) == []


def test_capacity_pass_alone_cannot_enter_100m_science() -> None:
    payload = contract()
    payload["prerequisites"]["required_200m_month_decision"] = None

    failures = MODULE.validate(payload)

    assert "100 m science must require a passing 200 m month" in failures


def test_100m_restart_gate_requires_all_cumulative_water_fields() -> None:
    payload = contract()
    payload["paired_event_design"]["restart_overlap"]["required_fields"].remove(
        "evaporation_net_cumulative"
    )

    failures = MODULE.validate(payload)

    assert "restart overlap omits cumulative water fields" in failures


def test_100m_gate_requires_demonstrated_added_value() -> None:
    payload = contract()
    skill = payload["matched_scientific_comparison"][
        "terrain_sensitive_skill_families"
    ]
    skill["minimum_families_with_positive_median_skill"] = 0
    skill[
        "minimum_families_with_positive_95_percent_lower_confidence_bound"
    ] = 0

    failures = MODULE.validate(payload)

    assert "too few terrain-sensitive families must improve" in failures
    assert "no statistically robust added-value family is required" in failures


def test_100m_event_pass_cannot_authorize_annual_or_production() -> None:
    payload = contract()
    payload["authorization"]["on_pass"][
        "hundred_meter_twenty_year_production"
    ] = True

    failures = MODULE.validate(payload)

    assert any("over-authorizes" in failure for failure in failures)


def test_validator_detects_weak_memory_margin() -> None:
    payload = copy.deepcopy(contract())
    payload["mandatory_numerical_and_physical_gates"][
        "minimum_memory_headroom_fraction_every_gpu"
    ] = 0.1

    failures = MODULE.validate(payload)

    assert "GPU memory headroom is below 15 percent" in failures
