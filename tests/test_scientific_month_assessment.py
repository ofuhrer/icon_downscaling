from __future__ import annotations

import copy
import hashlib
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
    / "assess_scientific_month.py"
)
SPEC = importlib.util.spec_from_file_location("month_assessor", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
SCIENTIFIC_PLAN = (
    ROOT / "case_studies" / "swiss_200m" / "config" / "scientific_pilot_plan.json"
)


def publish(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    Path(f"{path}.ready").touch()


def fixture(tmp_path: Path) -> tuple[dict, dict]:
    scientific = copy.deepcopy(json.loads(SCIENTIFIC_PLAN.read_text()))
    criteria = scientific["promotion_criteria"]["month_to_annual_cycle"]
    criteria["expected_unique_output_records"] = 2
    criteria["expected_complete_tabsd_days"] = 1
    criteria["expected_complete_rhiresd_windows"] = 1
    criteria["expected_matched_sis_times"] = 1
    criteria["minimum_station_pairs_per_core_metric"] = 2
    scientific_path = tmp_path / "scientific.json"
    scientific_path.write_text(json.dumps(scientific))

    compressed = tmp_path / "compressed.nc"
    compressed.write_bytes(b"data")
    Path(f"{compressed}.ready").touch()
    compression = tmp_path / "compression.json"
    publish(
        compression,
        {"status": "PASS", "target": str(compressed), "target_bytes": 4},
    )
    Path(f"{compression}.ready").unlink()
    overlap_compressed = tmp_path / "overlap_compressed.nc"
    overlap_compressed.write_bytes(b"data")
    Path(f"{overlap_compressed}.ready").touch()
    overlap_compression = tmp_path / "overlap_compression.json"
    publish(
        overlap_compression,
        {
            "status": "PASS",
            "target": str(overlap_compressed),
            "target_bytes": 4,
        },
    )
    Path(f"{overlap_compression}.ready").unlink()
    retirement = tmp_path / "forcing_retirement.json"
    publish(
        retirement,
        {
            "status": "PASS",
            "action": "RETIRED",
            "execute": True,
            "forcing_publication_ready_withdrawn": True,
            "payload_count": 2,
            "payload_bytes": 20,
        },
    )
    overlap_retirement = tmp_path / "overlap_forcing_retirement.json"
    publish(
        overlap_retirement,
        {
            "status": "PASS",
            "action": "RETIRED",
            "execute": True,
            "forcing_publication_ready_withdrawn": True,
            "payload_count": 1,
            "payload_bytes": 10,
        },
    )

    segment_model = tmp_path / "segment_model.json"
    overlap_model = tmp_path / "overlap_model.json"
    solver = tmp_path / "solver.json"
    overlap_solver = tmp_path / "overlap_solver.json"
    publish(
        segment_model,
        {
            "status": "PASS",
            "provenance": {
                "status": "PASS",
                "source_commit": "a" * 40,
                "executable_sha256": "b" * 64,
                "static_sha256": "c" * 64,
                "forcing_publication_sha256": "d" * 64,
            },
            "output": {
                "times": ["2020-07-01T00:00:00", "2020-07-01T03:00:00"]
            },
        },
    )
    publish(
        overlap_model,
        {
            "status": "PASS",
            "provenance": {
                "status": "PASS",
                "source_commit": "a" * 40,
                "executable_sha256": "b" * 64,
                "static_sha256": "c" * 64,
                "forcing_publication_sha256": "d" * 64,
            },
            "output": {"times": ["x"]},
        },
    )
    publish(solver, {"status": "PASS"})
    publish(overlap_solver, {"status": "PASS"})

    validation = tmp_path / "validation"
    trajectory = validation / "trajectory.json"
    physical = validation / "physical.json"
    source = validation / "source.json"
    station = validation / "station.json"
    ogd = validation / "ogd.json"
    drift = validation / "drift.json"
    assessment = validation / "assessment.json"
    publish(
        trajectory,
        {
            "status": "PASS",
            "metrics": {
                name: {"outside_tolerance_count": 0}
                for name in (
                    "precipitation",
                    "runoff_surface_cumulative",
                    "runoff_subsurface_cumulative",
                    "evaporation_net_cumulative",
                )
            },
        },
    )
    publish(
        physical,
        {
            "status": "PASS",
            "classes": {
                "active_soil_interior": {
                    "surface_energy_diagnostic": {
                        "mean_absolute_residual_w_m2": 2.0
                    }
                }
            },
            "water_budget_contract": {
                "mode": "production_cumulative",
                "production_eligible": True,
                "representativeness_limited": False,
            },
        },
    )
    publish(source, {"status": "PASS"})
    metrics = {}
    for source_name, delta in (("hicar", 0.5), ("rea_l", 0.0)):
        metrics[source_name] = {"all_sites": {}}
        for metric in MODULE.CORE_STATION_METRICS:
            value = {"count": 2, "root_mean_squared_error": 1.0 + delta}
            if metric == "wind_vector":
                value["vector_root_mean_squared_error_m_s"] = 1.0 + delta
            metrics[source_name]["all_sites"][metric] = value
    publish(
        station,
        {
            "status": "PASS",
            "matched_model_times": [
                "2020-07-01T00:00:00",
                "2020-07-01T03:00:00",
            ],
            "metrics": metrics,
        },
    )
    publish(
        ogd,
        {
            "status": "PASS",
            "matched_temperature_days": ["2020-07-01"],
            "matched_daily_windows": [
                {
                    "rhires_day": "2020-07-01",
                    "window_start": "2020-07-01T06:00:00",
                    "window_end": "2020-07-02T06:00:00",
                }
            ],
            "matched_radiation_times": ["2020-07-01T03:00:00"],
            "metrics": {
                product: {
                    source_name: {
                        "interior_ge_10km": {
                            "root_mean_squared_error": (
                                1.5 if source_name == "hicar" else 1.0
                            )
                        }
                    }
                    for source_name in ("hicar", "rea_l")
                }
                for product in ("tabsd", "rhiresd")
            },
        },
    )
    publish(
        drift,
        {"status": "PASS", "decision": "NO_DRIFT_FLAGS", "flags": []},
    )

    restore = tmp_path / "restore.json"
    publish(restore, {"status": "PASS"})
    archive = tmp_path / "archive.json"
    archive.write_text(
        json.dumps(
            {
                "status": "APPROVED",
                "approval": {
                    "destination": "/archive",
                    "owner": "owner",
                    "quota_bytes": 100,
                    "measured_transfer_bytes_per_second": 10,
                    "restore_drill_report": str(restore),
                    "approved_by": "approver",
                },
            }
        )
    )
    quality = tmp_path / "quality.json"
    metric_families = {
        "temperature",
        "precipitation",
        "wind",
        "radiation",
        "snow",
    }
    quality.write_text(
        json.dumps(
            {
                "annual_acceptance_thresholds": {
                    "status": "APPROVED",
                    "required_metrics": {
                        family: ["metric"] for family in metric_families
                    },
                    "approval": {
                        "application": "test",
                        "metric_weights": {
                            family: 1.0 for family in metric_families
                        },
                        "absolute_limits": {
                            family: {"metric": 2.0}
                            for family in metric_families
                        },
                        "approved_by": "approver",
                        "frozen_at": "2026-07-25T12:00:00+02:00",
                    },
                }
            }
        )
    )

    source_qualification = tmp_path / "month_source_qualification.json"
    publish(
        source_qualification,
        {
            "schema_version": 1,
            "status": "PASS",
            "change_scope": "OUTPUT_DIAGNOSTIC_ONLY",
            "child_commit": "a" * 40,
            "parent_commit": scientific["configuration"][
                "month_required_parent_hicar_commit"
            ],
            "parent_ancestry": {
                "status": "PASS",
                "parent_is_ancestor": True,
                "merge_base": scientific["configuration"][
                    "month_required_parent_hicar_commit"
                ],
            },
            "evidence": {
                "clean_target_build": {
                    "status": "PASS",
                    "artifact_sha256": "1" * 64,
                    "source_tree_clean": True,
                    "source_commit": "a" * 40,
                    "target": "HICAR",
                },
                "restart_continuity": {
                    "status": "PASS",
                    "artifact_sha256": "2" * 64,
                    "source_commit": "a" * 40,
                    "nonzero_runoff_observed": True,
                    "compared_fields": [
                        "precipitation",
                        "runoff_surface_cumulative",
                        "runoff_subsurface_cumulative",
                        "evaporation_net_cumulative",
                    ],
                },
                "representative_bridge_run": {
                    "status": "PASS",
                    "artifact_sha256": "3" * 64,
                    "source_commit": "a" * 40,
                    "completion_status": "PASS",
                },
                "national_short_run": {
                    "status": "PASS",
                    "artifact_sha256": "4" * 64,
                    "source_commit": "a" * 40,
                    "completion_status": "PASS",
                },
                "preexisting_field_equivalence": {
                    "status": "PASS",
                    "artifact_sha256": "5" * 64,
                    "compared_field_count": 20,
                    "mismatch_count": 0,
                },
                "solver_gate_equivalence": {
                    "status": "PASS",
                    "artifact_sha256": "6" * 64,
                    "compared_gate_count": 8,
                    "mismatch_count": 0,
                },
            },
        },
    )

    month = {
        "_plan_path": str(tmp_path / "month.json"),
        "status": "PLANNED",
        "expected_hicar_commit": "a" * 40,
        "required_parent_hicar_commit": scientific["configuration"][
            "month_required_parent_hicar_commit"
        ],
        "source_qualification_report": str(source_qualification),
        "source_qualification_sha256": hashlib.sha256(
            source_qualification.read_bytes()
        ).hexdigest(),
        "start": "2020-07-01T00:00:00",
        "end": "2020-07-01T03:00:00",
        "output_interval_seconds": 10800,
        "scientific_plan": str(scientific_path),
        "segments": [
            {
                "sequence": 1,
                "expected_output_records": 2,
                "forcing_record_count": 2,
                "model_completion_report": str(segment_model),
                "solver_report": str(solver),
                "compression_report": str(compression),
                "compressed_output_file": str(compressed),
                "forcing_retirement_report": str(retirement),
            }
        ],
        "uninterrupted_restart_overlap": {
            "expected_output_records": 1,
            "forcing_record_count": 1,
            "model_completion_report": str(overlap_model),
            "solver_report": str(overlap_solver),
            "compression_report": str(overlap_compression),
            "compressed_output_file": str(overlap_compressed),
            "forcing_retirement_report": str(overlap_retirement),
        },
        "restart_trajectory_report": str(trajectory),
        "validation_reports": {
            "physical": str(physical),
            "rea_l_source": str(source),
            "swissmetnet": str(station),
            "ogd_grid": str(ogd),
            "drift_screen": str(drift),
            "drift_attribution": str(validation / "attribution.json"),
            "month_assessment": str(assessment),
        },
        "archive_contract": str(archive),
        "observational_validation_contract": str(quality),
    }
    return month, scientific


def test_passing_month_with_approved_archive_authorizes_annual_cycle(tmp_path):
    month, scientific = fixture(tmp_path)

    report, complete = MODULE.assess(month, scientific)

    assert complete is True
    assert report["decision"] == "GO_ANNUAL_CYCLE"
    assert report["failed_screens"] == []
    assert report["authorization"]["annual_cycle"] is True
    assert report["authorization"]["twenty_year_200m_production"] is False


def test_month_without_frozen_model_provenance_cannot_authorize_annual(tmp_path):
    month, scientific = fixture(tmp_path)
    model_path = Path(month["segments"][0]["model_completion_report"])
    model = json.loads(model_path.read_text())
    model.pop("provenance")
    model_path.write_text(json.dumps(model))

    report, complete = MODULE.assess(month, scientific)

    assert complete is True
    assert report["decision"] == "HOLD_AND_DIAGNOSE"
    assert "segment_01_production_provenance" in report["failed_screens"]
    assert report["authorization"]["annual_cycle"] is False


def test_month_without_executed_forcing_retirement_cannot_authorize_annual(
    tmp_path,
):
    month, scientific = fixture(tmp_path)
    retirement_path = Path(
        month["segments"][0]["forcing_retirement_report"]
    )
    retirement = json.loads(retirement_path.read_text())
    retirement["action"] = "READY_TO_RETIRE"
    retirement["execute"] = False
    retirement_path.write_text(json.dumps(retirement))

    report, complete = MODULE.assess(month, scientific)

    assert complete is True
    assert report["decision"] == "HOLD_AND_DIAGNOSE"
    assert "segment_01_forcing_retired" in report["failed_screens"]


def test_month_with_multiple_segments_requires_restart_retirement_publication(
    tmp_path,
):
    month, scientific = fixture(tmp_path)
    second = copy.deepcopy(month["segments"][0])
    second["sequence"] = 2
    month["segments"][0]["restart_retirement_report"] = str(
        tmp_path / "missing_restart_retirement.json"
    )
    month["segments"].append(second)

    report, complete = MODULE.assess(month, scientific)

    assert complete is False
    assert report["assessment_status"] == "INCOMPLETE"
    assert any(
        "segment_01_restart_retirement is not published" in reason
        for reason in report["incomplete_reasons"]
    )


def test_month_with_mixed_model_executables_cannot_authorize_annual(tmp_path):
    month, scientific = fixture(tmp_path)
    overlap_path = Path(
        month["uninterrupted_restart_overlap"]["model_completion_report"]
    )
    overlap = json.loads(overlap_path.read_text())
    overlap["provenance"]["executable_sha256"] = "e" * 64
    overlap_path.write_text(json.dumps(overlap))

    report, complete = MODULE.assess(month, scientific)

    assert complete is True
    assert report["decision"] == "HOLD_AND_DIAGNOSE"
    assert "consistent_model_identity" in report["failed_screens"]


def test_month_with_source_outside_frozen_gate_cannot_authorize_annual(tmp_path):
    month, scientific = fixture(tmp_path)
    overlap_path = Path(
        month["uninterrupted_restart_overlap"]["model_completion_report"]
    )
    overlap = json.loads(overlap_path.read_text())
    overlap["provenance"]["source_commit"] = "b" * 40
    overlap_path.write_text(json.dumps(overlap))

    report, complete = MODULE.assess(month, scientific)

    assert complete is True
    assert report["decision"] == "HOLD_AND_DIAGNOSE"
    assert "frozen_hicar_source_commit" in report["failed_screens"]


def test_month_with_approximate_water_budget_cannot_authorize_annual(tmp_path):
    month, scientific = fixture(tmp_path)
    physical_path = Path(month["validation_reports"]["physical"])
    physical = json.loads(physical_path.read_text())
    physical["water_budget_contract"] = {
        "mode": "legacy_snapshot_reconstruction",
        "production_eligible": False,
        "representativeness_limited": True,
    }
    physical_path.write_text(json.dumps(physical))

    report, complete = MODULE.assess(month, scientific)

    assert complete is True
    assert report["decision"] == "HOLD_AND_DIAGNOSE"
    assert "production_water_budget_observables" in report["failed_screens"]


def test_month_without_cumulative_restart_comparison_cannot_authorize_annual(
    tmp_path,
):
    month, scientific = fixture(tmp_path)
    trajectory_path = Path(month["restart_trajectory_report"])
    trajectory = json.loads(trajectory_path.read_text())
    trajectory["metrics"].pop("evaporation_net_cumulative")
    trajectory_path.write_text(json.dumps(trajectory))

    report, complete = MODULE.assess(month, scientific)

    assert complete is True
    assert report["decision"] == "HOLD_AND_DIAGNOSE"
    assert "cumulative_water_restart_continuity" in report["failed_screens"]


def test_month_duplicate_ogd_dates_cannot_satisfy_complete_day_counts(tmp_path):
    month, scientific = fixture(tmp_path)
    criteria = scientific["promotion_criteria"]["month_to_annual_cycle"]
    criteria["expected_complete_tabsd_days"] = 2
    ogd_path = Path(month["validation_reports"]["ogd_grid"])
    ogd = json.loads(ogd_path.read_text())
    ogd["matched_temperature_days"] = [
        "2020-07-01",
        "2020-07-01",
    ]
    ogd_path.write_text(json.dumps(ogd))

    report, complete = MODULE.assess(month, scientific)

    assert complete is True
    assert report["decision"] == "HOLD_AND_DIAGNOSE"
    assert "tabsd_complete_days" in report["failed_screens"]


def test_month_requires_frozen_sis_time_coverage(tmp_path):
    month, scientific = fixture(tmp_path)
    ogd_path = Path(month["validation_reports"]["ogd_grid"])
    ogd = json.loads(ogd_path.read_text())
    ogd["matched_radiation_times"] = []
    ogd_path.write_text(json.dumps(ogd))

    report, complete = MODULE.assess(month, scientific)

    assert complete is True
    assert report["decision"] == "HOLD_AND_DIAGNOSE"
    assert "sis_matched_time_axis" in report["failed_screens"]


def test_unresolved_archive_is_a_specific_hold(tmp_path):
    month, scientific = fixture(tmp_path)
    archive = Path(month["archive_contract"])
    archive.write_text(json.dumps({"status": "UNRESOLVED", "approval": {}}))

    report, complete = MODULE.assess(month, scientific)

    assert complete is True
    assert report["decision"] == "HOLD_ARCHIVE_CONTRACT"
    assert report["failed_screens"] == ["production_archive_contract"]
    assert report["authorization"]["annual_cycle"] is False


def test_unresolved_archive_and_quality_contracts_are_a_combined_hold(tmp_path):
    month, scientific = fixture(tmp_path)
    Path(month["archive_contract"]).write_text(
        json.dumps({"status": "UNRESOLVED", "approval": {}})
    )
    Path(month["observational_validation_contract"]).write_text(
        json.dumps(
            {
                "annual_acceptance_thresholds": {
                    "status": "UNRESOLVED",
                    "approval": {},
                }
            }
        )
    )

    report, complete = MODULE.assess(month, scientific)

    assert complete is True
    assert report["decision"] == "HOLD_QUALIFICATION_CONTRACTS"
    assert set(report["failed_screens"]) == {
        "production_archive_contract",
        "application_quality_contract",
    }


def test_approved_label_without_all_metric_families_remains_a_quality_hold(
    tmp_path,
):
    month, scientific = fixture(tmp_path)
    Path(month["observational_validation_contract"]).write_text(
        json.dumps(
            {
                "annual_acceptance_thresholds": {
                    "status": "APPROVED",
                    "required_metrics": {
                        "temperature": ["rmse"],
                        "precipitation": ["rmse"],
                    },
                    "approval": {
                        "application": "test",
                        "metric_weights": {"temperature": 1.0},
                        "absolute_limits": {
                            "temperature": {"rmse": 2.0},
                        },
                        "approved_by": "approver",
                        "frozen_at": "2026-07-25T12:00:00+02:00",
                    },
                }
            }
        )
    )

    report, complete = MODULE.assess(month, scientific)

    assert complete is True
    assert report["decision"] == "HOLD_APPLICATION_QUALITY_CONTRACT"
    screen = next(
        item
        for item in report["screens"]
        if item["id"] == "application_quality_contract"
    )
    assert screen["missing_weight_families"] == ["precipitation"]
    assert screen["missing_limit_families"] == ["precipitation"]


def test_signed_unexplained_drift_stops_escalation(tmp_path):
    month, scientific = fixture(tmp_path)
    drift = Path(month["validation_reports"]["drift_screen"])
    publish(
        drift,
        {
            "status": "PASS",
            "decision": "ATTRIBUTION_REQUIRED",
            "flags": [{"id": "active_soil_interior:soil_water_kg_m2"}],
        },
    )
    attribution = Path(month["validation_reports"]["drift_attribution"])
    publish(
        attribution,
        {
            "status": "PASS",
            "reviewer": "reviewer",
            "reviewed_at": "2026-07-25T12:00:00+02:00",
            "attributions": [
                {
                    "flag_id": "active_soil_interior:soil_water_kg_m2",
                    "classification": "unexplained",
                    "rationale": "No forcing or physical explanation was found.",
                }
            ],
        },
    )

    report, complete = MODULE.assess(month, scientific)

    assert complete is True
    assert report["decision"] == "STOP_AND_REDESIGN"
    assert report["postspinup_drift"]["unexplained_flag_ids"] == [
        "active_soil_interior:soil_water_kg_m2"
    ]
