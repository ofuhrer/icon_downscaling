from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
DEFINITION = ROOT / "case_studies/swiss_200m/config/hicarprep_land_response_6h_v1.json"
ASSESSOR = ROOT / "case_studies/swiss_200m/validation/assess_hicarprep_land_response.py"
ROBUSTNESS = ROOT / "case_studies/swiss_200m/validation/diagnose_hicarprep_land_response_robustness.py"
CASE_REPORT = ROOT / "case_studies/swiss_200m/validation/hicarprep_land_response_6h_20200702_v1.json"
SPEC = importlib.util.spec_from_file_location("assess_hicarprep_land_response", ASSESSOR)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_definition_freezes_symmetric_six_hour_decision() -> None:
    definition = json.loads(DEFINITION.read_text())
    assert definition["case"]["duration_seconds"] == 6 * 3600
    assert definition["case"]["expected_output_records"] == 13
    assert definition["case"]["methods"] == ["smi", "relative_saturation"]
    assert definition["comparison_contract"]["symmetric"] is True
    assert definition["decision_states"]["both_pass"] == (
        "RETAIN_METHOD_UNCERTAINTY_AFTER_6H_VIABILITY"
    )
    required = set(definition["required_diagnostics"])
    assert {"soil_water_content", "soil_water_content_liq", "hfss", "hfls"} <= required


def test_runner_uses_private_non_overwriting_matched_inputs() -> None:
    script = (
        ROOT / "case_studies/swiss_200m/scripts/run_hicarprep_land_response_balfrin.sbatch"
    ).read_text()
    assert "HICARPREP_LAND_RESPONSE_ROOT:?" in script
    assert 'test ! -e "$run"' in script
    assert "for hour in $(seq 0 7)" in script
    assert "--output-profile land_response_30min" in script
    assert "--expected-hours 7" in script
    assert '"$exe" -v soiltexture_var' in script
    assert '"$exe" -v soil_water_content_liq' not in script
    assert "hicarprep_hicar_soiltexture_v1/source" in script


def test_stats_and_source_gate_are_method_neutral(tmp_path: Path) -> None:
    values = np.asarray([[1.0, 2.0], [3.0, 4.0]])
    result = MODULE.stats(values, np.ones_like(values, dtype=bool))
    assert result["p50"] == 2.5
    report = {
        "status": "PASS",
        "metrics": {
            "active_soil_all": {
                "temperature_2m_height_adjusted_k": {
                    "bias": 0.25,
                    "root_mean_squared_error": 1.0,
                }
            }
        },
    }
    path = tmp_path / "source.json"
    path.write_text(json.dumps(report))
    contract = {
        "temperature_2m_height_adjusted_k": {
            "maximum_absolute_bias": 5.0,
            "maximum_rmse": 8.0,
        }
    }
    assert MODULE.source_gate(path, contract)["failures"] == []
    report["metrics"]["active_soil_all"]["temperature_2m_height_adjusted_k"][
        "bias"
    ] = -6.0
    path.write_text(json.dumps(report))
    assert "absolute bias" in MODULE.source_gate(path, contract)["failures"][0]


def test_posthoc_diagnostic_cannot_rewrite_frozen_decision() -> None:
    script = ROBUSTNESS.read_text()
    assert '"status": "POST_HOC_DIAGNOSTIC_ONLY"' in script
    assert '"frozen_decision_unchanged": assessment["decision"]' in script
    assert "decision_states" not in script


def test_case_report_closes_provenance_without_rewriting_result() -> None:
    report = json.loads(CASE_REPORT.read_text())
    decision = report["decision"]
    assert decision["frozen_result"] == "RELATIVE_SATURATION_NOT_VIABLE_IN_SUMMER_6H"
    assert decision["frozen_result_altered_by_posthoc_analysis"] is False
    assert report["forcing"]["common_to_both_arms"] is True
    assert len(report["forcing"]["records"]) == 8
    assert report["runs"]["smi"]["viability_status"] == "PASS_VIABILITY"
    assert report["runs"]["relative_saturation"]["viability_status"] == "FAIL_VIABILITY"
    stored = report["durable_publication"]["stored_files"]
    assert all("output" not in name and "model" not in name for name in stored)
    assert all(len(value) == 64 for value in stored.values())
