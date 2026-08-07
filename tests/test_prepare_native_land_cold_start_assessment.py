from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "wind_climatology"
    / "prepare_native_land_cold_start_assessment.py"
)
SPEC = importlib.util.spec_from_file_location("prepare_cold_start_assessment", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_merge_keeps_controls_and_replaces_only_reset_origins(tmp_path):
    contract = {
        "execution": {},
        "decision_rule": {},
        "windows": [
            {"chain_id": "origin-20200701", "static_file": "old1"},
            {"chain_id": "origin-20200702", "static_file": "old2"},
            {"chain_id": "origin-20200703", "static_file": "old3"},
        ],
    }
    baseline = {
        "chains": [
            {"chain_id": "reference"},
            {"chain_id": "origin-20200701"},
        ]
    }
    candidate = {
        "chains": [
            {"chain_id": "native-origin-20200702"},
            {"chain_id": "native-origin-20200703"},
        ]
    }
    left = tmp_path / "two.nc"
    right = tmp_path / "three.nc"
    merged_contract, completion = MODULE.merge_payloads(contract, baseline, candidate, left, right)
    assert [item["chain_id"] for item in completion["chains"]] == [
        "reference",
        "origin-20200701",
        "native-origin-20200702",
        "native-origin-20200703",
    ]
    assert [item["chain_id"] for item in merged_contract["windows"]] == [
        "origin-20200701",
        "native-origin-20200702",
        "native-origin-20200703",
    ]
