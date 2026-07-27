from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "validation"
    / "assess_scientific_baseline_transition.py"
)
SPEC = importlib.util.spec_from_file_location(
    "baseline_transition_assessment", MODULE_PATH
)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


CANDIDATE = "a" * 40
EXE = "b" * 64


def publish(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    Path(f"{path}.ready").touch()


def contract(tmp_path: Path) -> tuple[dict, dict[str, Path]]:
    inputs = {
        name: tmp_path / f"{name}.json"
        for name in ("transition", "candidate", "runtime")
    }
    for name, path in inputs.items():
        path.write_text(json.dumps({"name": name}))
    bundle = tmp_path / "candidate.bundle"
    bundle.write_bytes(b"bundle")
    payload = {
        "candidate_commit": CANDIDATE,
        "candidate_executable_sha256": EXE,
        "frozen_inputs": {
            "transition_plan_sha256": MODULE.sha256(inputs["transition"]),
            "candidate_scientific_plan_sha256": MODULE.sha256(inputs["candidate"]),
            "runtime_manifest_sha256": MODULE.sha256(inputs["runtime"]),
            "summer_static_sha256": "1" * 64,
            "winter_static_sha256": "2" * 64,
        },
        "source_bundle": {
            "path": str(bundle),
            "sha256": MODULE.sha256(bundle),
        },
        "required_event_reports": [
            "model_chunk_completion.json",
            "scientific_validation/restart_checkpoint_diagnostics.json",
            "scientific_validation/solver_log_diagnostics.json",
            "scientific_validation/scientific_event_diagnostics.json",
            "scientific_validation/rea_l_source_comparison.json",
            "scientific_validation/swissmetnet_comparison.json",
            "scientific_validation/ogd_grid_comparison.json",
        ],
        "water_budget_gate": {
            "mode": "production_cumulative",
            "production_eligible": True,
            "required_zero_decrease_counts": [
                "precipitation_decrease_cells",
                "surface_runoff_decrease_cells",
                "subsurface_runoff_decrease_cells",
            ],
            "required_nonzero_pair_total_runoff": True,
            "gate_class": "active_soil_interior",
            "maximum_absolute_residual_kg_m2_per_event": 5.0,
            "required_closure_stores": [
                "layer-derived soil water",
                "swet",
                "canopy_water",
                "water_aquifer",
                "wetland_h20_store",
            ],
            "diagnostic_not_summed": "storage_gw",
        },
        "required_event_assessment_decision": "GO_MONTH_AND_100M_CAPACITY_GATE",
        "required_events": ["summer", "winter"],
        "restart_trajectory_gate": {
            "required_status": "PASS",
            "expected_records": 8,
            "required_fields": [
                "precipitation",
                "runoff_surface_cumulative",
                "runoff_subsurface_cumulative",
                "evaporation_net_cumulative",
            ],
        },
        "decision": {
            "pass": "NOMINATE_V29_FOR_CANONICAL_MONTH_SOURCE",
            "single_event_or_restart_failure": "HOLD_AND_DIAGNOSE",
        },
    }
    inputs["bundle"] = bundle
    return payload, inputs


def make_event(run: Path, name: str, runoff: float = 0.2, residual: float = 0.5) -> None:
    static_sha = "1" * 64 if name == "summer" else "2" * 64
    publish(
        run / "model_chunk_completion.json",
        {
            "status": "PASS",
            "provenance": {
                "status": "PASS",
                "source_commit": CANDIDATE,
                "executable_sha256": EXE,
                "static_sha256": static_sha,
            },
        },
    )
    validation = run / "scientific_validation"
    for name_ in (
        "restart_checkpoint_diagnostics.json",
        "solver_log_diagnostics.json",
        "rea_l_source_comparison.json",
        "swissmetnet_comparison.json",
        "ogd_grid_comparison.json",
    ):
        publish(validation / name_, {"status": "PASS"})
    publish(
        validation / "scientific_event_diagnostics.json",
        {
            "status": "PASS",
            "precipitation_decrease_cells": 0,
            "surface_runoff_decrease_cells": 0,
            "subsurface_runoff_decrease_cells": 0,
            "water_budget_contract": {
                "mode": "production_cumulative",
                "production_eligible": True,
                "storage": {
                    "summed_for_closure": [
                        "layer-derived soil water",
                        "swet",
                        "canopy_water",
                        "water_aquifer",
                        "wetland_h20_store",
                    ],
                    "diagnostic_not_summed": [{"field": "storage_gw"}],
                },
            },
            "classes": {
                "active_soil_interior": {
                    "water_diagnostic_kg_m2": {
                        "residual": residual,
                        "runoff": runoff,
                    }
                }
            },
        },
    )


def passing_fixture(tmp_path: Path):
    contract_payload, inputs = contract(tmp_path)
    summer = tmp_path / "summer"
    winter = tmp_path / "winter"
    make_event(summer, "summer")
    make_event(winter, "winter")
    paired = {
        "decision": "GO_MONTH_AND_100M_CAPACITY_GATE",
        "events": [{"event": "summer"}, {"event": "winter"}],
    }
    fields = contract_payload["restart_trajectory_gate"]["required_fields"]
    trajectory = {
        "status": "PASS",
        "failures": [],
        "expected_times": [str(index) for index in range(8)],
        "metrics": {name: {} for name in fields},
    }
    overlap = {
        "status": "PASS",
        "provenance": {
            "status": "PASS",
            "source_commit": CANDIDATE,
            "executable_sha256": EXE,
            "static_sha256": "1" * 64,
        },
    }
    return contract_payload, inputs, summer, winter, paired, overlap, trajectory


def assess_fixture(tmp_path: Path, **overrides):
    values = passing_fixture(tmp_path)
    contract_payload, inputs, summer, winter, paired, overlap, trajectory = values
    return MODULE.assess(
        contract=contract_payload,
        transition_plan_path=inputs["transition"],
        candidate_plan_path=inputs["candidate"],
        runtime_manifest_path=inputs["runtime"],
        paired_assessment=overrides.get("paired", paired),
        summer_run=summer,
        winter_run=winter,
        restart_overlap_completion=overrides.get("overlap", overlap),
        restart_trajectory=overrides.get("trajectory", trajectory),
        source_bundle_path=inputs["bundle"],
    )


def test_complete_transition_nominates_but_does_not_authorize_compute(
    tmp_path: Path,
) -> None:
    report = assess_fixture(tmp_path)

    assert report["status"] == "PASS"
    assert report["decision"] == "NOMINATE_V29_FOR_CANONICAL_MONTH_SOURCE"
    assert report["authorization"]["canonical_month_source_nomination"] is True
    assert report["authorization"]["month_compute"] is False


def test_approximate_water_budget_cannot_nominate(tmp_path: Path) -> None:
    values = passing_fixture(tmp_path)
    contract_payload, inputs, summer, winter, paired, overlap, trajectory = values
    physical = (
        summer / "scientific_validation" / "scientific_event_diagnostics.json"
    )
    payload = json.loads(physical.read_text())
    payload["water_budget_contract"]["mode"] = "legacy_snapshot_reconstruction"
    physical.write_text(json.dumps(payload))

    report = MODULE.assess(
        contract=contract_payload,
        transition_plan_path=inputs["transition"],
        candidate_plan_path=inputs["candidate"],
        runtime_manifest_path=inputs["runtime"],
        paired_assessment=paired,
        summer_run=summer,
        winter_run=winter,
        restart_overlap_completion=overlap,
        restart_trajectory=trajectory,
        source_bundle_path=inputs["bundle"],
    )

    assert report["status"] == "FAIL"
    assert any("not production cumulative" in item for item in report["failures"])


def test_large_water_residual_blocks_nomination(tmp_path: Path) -> None:
    values = passing_fixture(tmp_path)
    contract_payload, inputs, summer, winter, paired, overlap, trajectory = values
    physical = (
        winter / "scientific_validation" / "scientific_event_diagnostics.json"
    )
    payload = json.loads(physical.read_text())
    payload["classes"]["active_soil_interior"]["water_diagnostic_kg_m2"][
        "residual"
    ] = 5.01
    physical.write_text(json.dumps(payload))

    report = MODULE.assess(
        contract=contract_payload,
        transition_plan_path=inputs["transition"],
        candidate_plan_path=inputs["candidate"],
        runtime_manifest_path=inputs["runtime"],
        paired_assessment=paired,
        summer_run=summer,
        winter_run=winter,
        restart_overlap_completion=overlap,
        restart_trajectory=trajectory,
        source_bundle_path=inputs["bundle"],
    )

    assert report["status"] == "FAIL"
    assert any("water residual exceeds" in item for item in report["failures"])


def test_restart_comparison_must_cover_cumulative_fields(tmp_path: Path) -> None:
    values = passing_fixture(tmp_path)
    _, _, _, _, _, _, trajectory = values
    del trajectory["metrics"]["evaporation_net_cumulative"]

    report = assess_fixture(tmp_path, trajectory=trajectory)

    assert report["status"] == "FAIL"
    assert any("omits required cumulative fields" in item for item in report["failures"])


def test_restart_overlap_must_use_the_same_executable(tmp_path: Path) -> None:
    values = passing_fixture(tmp_path)
    _, _, _, _, _, overlap, _ = values
    overlap["provenance"]["executable_sha256"] = "f" * 64

    report = assess_fixture(tmp_path, overlap=overlap)

    assert report["status"] == "FAIL"
    assert any(
        "overlap executable checksum" in item for item in report["failures"]
    )
