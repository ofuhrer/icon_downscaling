from __future__ import annotations

from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
ASSESSOR = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "validation"
    / "assess_scientific_annual.py"
)
SCIENTIFIC = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "config"
    / "scientific_pilot_plan.json"
)
OBSERVATIONAL = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "config"
    / "observational_validation_contract.json"
)


def publish(path: Path, payload: dict, *, ready: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    if ready:
        Path(f"{path}.ready").touch()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def artifact(path: Path, content: bytes) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    Path(f"{path}.ready").touch()
    return {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "sha256": file_sha256(path),
    }


def season(value: datetime | date) -> str:
    if value.month in (12, 1, 2):
        return "DJF"
    if value.month in (3, 4, 5):
        return "MAM"
    if value.month in (6, 7, 8):
        return "JJA"
    return "SON"


def make_fixture(tmp_path: Path) -> tuple[Path, Path, dict[str, Path]]:
    scientific = tmp_path / "scientific.json"
    scientific.write_text(SCIENTIFIC.read_text())
    criteria = json.loads(scientific.read_text())["promotion_criteria"][
        "annual_cycle_to_20_year_campaign"
    ]
    start = datetime.fromisoformat("2019-10-01T00:00:00")
    end = datetime.fromisoformat("2020-10-01T00:00:00")
    times = []
    valid = start
    while valid <= end:
        times.append(valid.isoformat())
        valid += timedelta(hours=3)
    assert len(times) == criteria["expected_unique_output_records"]

    month = tmp_path / "month.json"
    publish(
        month,
        {
            "assessment_status": "COMPLETE",
            "decision": "GO_ANNUAL_CYCLE",
            "authorization": {"annual_cycle": True},
        },
    )

    target = tmp_path / "annual.nc"
    target.write_bytes(b"compressed")
    Path(f"{target}.ready").touch()
    model = tmp_path / "model.json"
    solver = tmp_path / "solver.json"
    compression = tmp_path / "compression.json"
    publish(
        model,
        {
            "status": "PASS",
            "provenance": {
                "status": "PASS",
                "source_commit": "a" * 40,
                "executable_sha256": "b" * 64,
                "static_sha256": "c" * 64,
                "forcing_publication_sha256": "d" * 64,
            },
            "output": {"times": times},
        },
    )
    publish(solver, {"status": "PASS"})
    publish(
        compression,
        {
            "status": "PASS",
            "target": str(target),
            "target_bytes": target.stat().st_size,
        },
        ready=False,
    )

    trajectories = []
    for index in range(4):
        path = tmp_path / f"trajectory_{index}.json"
        publish(path, {"status": "PASS"})
        trajectories.append({"report": str(path)})
    overlaps = []
    for label in ("DJF", "JJA"):
        path = tmp_path / f"initialization_{label}.json"
        publish(
            path,
            {
                "status": "PASS",
                "season": label,
                "retained_days": 21,
                "trajectory_equivalence_after_spinup": "PASS",
            },
        )
        overlaps.append({"report": str(path)})

    physical = tmp_path / "physical.json"
    source = tmp_path / "source.json"
    station = tmp_path / "station.json"
    ogd = tmp_path / "ogd.json"
    drift = tmp_path / "drift.json"
    attribution = tmp_path / "attribution.json"
    application = tmp_path / "application.json"
    recovery = tmp_path / "recovery.json"
    archive_transfer = tmp_path / "archive_transfer.json"
    release = tmp_path / "release.json"
    annual_assessment = tmp_path / "annual_assessment.json"

    publish(
        physical,
        {
            "status": "PASS",
            "classes": {
                "active_soil_interior": {
                    "surface_energy_diagnostic": {
                        "mean_absolute_residual_w_m2": 1.0
                    }
                }
            },
        },
    )
    publish(source, {"status": "PASS"})

    station_metrics = {}
    for label in ("DJF", "MAM", "JJA", "SON"):
        station_metrics[label] = {}
        for model_name in ("hicar", "rea_l"):
            all_sites = {}
            for metric in (
                "temperature_2m_height_adjusted_k",
                "relative_humidity_2m_percent",
                "surface_pressure_height_adjusted_pa",
                "precipitation_interval_kg_m2",
            ):
                all_sites[metric] = {
                    "count": 100,
                    "root_mean_squared_error": 0.5,
                }
            all_sites["wind_vector"] = {
                "count": 100,
                "vector_root_mean_squared_error_m_s": 0.5,
            }
            station_metrics[label][model_name] = {"all_sites": all_sites}
    publish(
        station,
        {
            "status": "PASS",
            "matched_model_times": times,
            "seasonal_metrics": station_metrics,
        },
    )

    days = []
    day = start.date()
    while day < end.date():
        days.append(day)
        day += timedelta(days=1)
    ogd_metrics = {}
    for label in ("DJF", "MAM", "JJA", "SON"):
        ogd_metrics[label] = {}
        for product in ("rhiresd", "tabsd"):
            ogd_metrics[label][product] = {
                model_name: {
                    "interior_ge_10km": {
                        "count": 100,
                        "root_mean_squared_error": 0.5,
                    }
                }
                for model_name in ("hicar", "rea_l")
            }
        ogd_metrics[label]["sis"] = {
            product: {"interior_ge_10km": {"count": 100}}
            for product in ("sis", "sis_no_horizon")
        }
    publish(
        ogd,
        {
            "status": "PASS",
            "matched_temperature_days": [value.isoformat() for value in days],
            "matched_daily_windows": [
                {"rhires_day": value.isoformat()} for value in days[:-1]
            ],
            "matched_radiation_times": times[1:-1],
            "seasonal_metrics": ogd_metrics,
        },
    )
    publish(drift, {"status": "PASS", "flags": []})
    publish(attribution, {"signed": True, "attributions": []})
    publish(
        recovery,
        {
            "status": "PASS",
            "drills_completed": 1,
            "restart_hash_match": True,
            "output_hash_match": True,
        },
    )
    archive_destination = tmp_path / "durable_archive"
    archive_manifest = artifact(
        archive_destination / "archive_manifest.json",
        b"manifest",
    )
    publish(
        archive_transfer,
        {
            "status": "PASS",
            "restore_verified": True,
            "sha256_match": True,
            "bytes_transferred": 100,
            "destination": str(archive_destination),
            "manifest": archive_manifest,
        },
    )

    restore = tmp_path / "restore.json"
    publish(restore, {"status": "PASS"})
    archive = tmp_path / "archive.json"
    publish(
        archive,
        {
            "status": "APPROVED",
            "approval": {
                "destination": str(archive_destination),
                "owner": "owner",
                "quota_bytes": 1000,
                "measured_transfer_bytes_per_second": 100,
                "restore_drill_report": str(restore),
                "approved_by": "approver",
            },
        },
        ready=False,
    )

    quality = json.loads(OBSERVATIONAL.read_text())
    required_families = quality["annual_acceptance_thresholds"][
        "required_metrics"
    ]
    quality["annual_acceptance_thresholds"]["status"] = "APPROVED"
    quality["annual_acceptance_thresholds"]["approval"] = {
        "application": "synthetic qualification",
        "metric_weights": {name: 1.0 for name in required_families},
        "absolute_limits": {
            name: {"synthetic_limit": 1.0} for name in required_families
        },
        "approved_by": "approver",
        "frozen_at": "2019-01-01T00:00:00",
    }
    quality_path = tmp_path / "quality.json"
    publish(quality_path, quality, ready=False)
    publish(
        application,
        {
            "status": "PASS",
            "contract_sha256": file_sha256(quality_path),
            "evaluated_metric_families": sorted(required_families),
            "evaluated_strata": quality["annual_acceptance_thresholds"][
                "required_strata"
            ],
            "failed_metrics": [],
        },
    )

    annual = tmp_path / "annual_plan.json"
    payload = {
        "status": "PLANNED",
        "scientific_plan": str(scientific),
        "month_assessment": str(month),
        "expected_hicar_commit": "1" * 40,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "output_interval_seconds": 10800,
        "segments": [
            {
                "sequence": 1,
                "model_completion_report": str(model),
                "solver_report": str(solver),
                "compression_report": str(compression),
                "compressed_output_file": str(target),
            }
        ],
        "restart_trajectory_reports": trajectories,
        "initialization_equivalence_reports": overlaps,
        "validation_reports": {
            "physical": str(physical),
            "rea_l_source": str(source),
            "swissmetnet": str(station),
            "ogd_grid": str(ogd),
            "drift_screen": str(drift),
            "drift_attribution": str(attribution),
            "application_quality": str(application),
            "failure_recovery": str(recovery),
            "archive_transfer_restore": str(archive_transfer),
            "production_release": str(release),
            "annual_assessment": str(annual_assessment),
        },
        "archive_contract": str(archive),
        "observational_validation_contract": str(quality_path),
    }
    publish(annual, payload)
    source_archive = artifact(archive_destination / "source.tar", b"source")
    executable_archive = artifact(
        archive_destination / "HICAR_gpu",
        b"executable",
    )
    static_archive = artifact(
        archive_destination / "domain_static_swiss_200m.nc",
        b"static-domain",
    )
    configuration_archive = artifact(
        archive_destination / "configuration.tar",
        b"config",
    )
    annual_plan_archive = artifact(
        archive_destination / "annual_plan.release.json",
        annual.read_bytes(),
    )
    model_payload = json.loads(model.read_text())
    model_payload["provenance"].update(
        {
            "source_commit": "1" * 40,
            "executable_sha256": executable_archive["sha256"],
            "static_sha256": static_archive["sha256"],
        }
    )
    model.write_text(json.dumps(model_payload))
    publish(
        release,
        {
            "status": "PASS",
            "immutable": True,
            "source_commit": "1" * 40,
            "executable_sha256": executable_archive["sha256"],
            "static_sha256": static_archive["sha256"],
            "configuration_sha256": configuration_archive["sha256"],
            "annual_plan_sha256": file_sha256(annual),
            "compute_allocation": "approved allocation",
            "archive_destination": str(archive_destination),
            "artifacts": {
                "source_archive": source_archive,
                "executable": executable_archive,
                "static_domain": static_archive,
                "configuration_archive": configuration_archive,
                "annual_plan": annual_plan_archive,
            },
        },
    )
    return annual, annual_assessment, {
        "model": model,
        "station": station,
        "physical": physical,
    }


def run_assessor(plan: Path, report: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(ASSESSOR),
            "--annual-plan",
            str(plan),
            "--report",
            str(report),
        ],
        text=True,
        capture_output=True,
    )


def test_passing_annual_authorizes_only_200m_twenty_year_campaign(tmp_path):
    plan, report, _ = make_fixture(tmp_path)

    result = run_assessor(plan, report)

    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(report.read_text())
    assert payload["decision"] == "GO_20_YEAR_200M_PRODUCTION"
    assert payload["authorization"]["twenty_year_200m_production"] is True
    assert payload["authorization"]["100m_scientific_production"] is False
    assert Path(f"{report}.ready").is_file()


def test_annual_without_frozen_model_provenance_cannot_authorize_campaign(
    tmp_path,
):
    plan, _, paths = make_fixture(tmp_path)
    model = json.loads(paths["model"].read_text())
    model["provenance"]["status"] = "NOT_REQUESTED"
    paths["model"].write_text(json.dumps(model))
    report = tmp_path / "missing_provenance.json"

    result = run_assessor(plan, report)

    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(report.read_text())
    assert payload["decision"] == "HOLD_AND_DIAGNOSE"
    assert "segment_01_production_provenance" in payload["failed_screens"]
    assert payload["authorization"]["twenty_year_200m_production"] is False


def test_annual_source_commit_must_match_frozen_plan(tmp_path):
    plan, _, paths = make_fixture(tmp_path)
    model = json.loads(paths["model"].read_text())
    model["provenance"]["source_commit"] = "b" * 40
    paths["model"].write_text(json.dumps(model))

    annual = json.loads(plan.read_text())
    release_path = Path(annual["validation_reports"]["production_release"])
    release = json.loads(release_path.read_text())
    release["source_commit"] = "b" * 40
    release_path.write_text(json.dumps(release))
    report = tmp_path / "wrong_source_commit.json"

    result = run_assessor(plan, report)

    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(report.read_text())
    assert payload["decision"] == "HOLD_AND_DIAGNOSE"
    assert "frozen_hicar_source_commit" in payload["failed_screens"]
    assert payload["authorization"]["twenty_year_200m_production"] is False


def test_release_hash_strings_without_published_artifacts_do_not_authorize(
    tmp_path,
):
    plan, _, _ = make_fixture(tmp_path)
    annual = json.loads(plan.read_text())
    release_path = Path(annual["validation_reports"]["production_release"])
    release = json.loads(release_path.read_text())
    release.pop("artifacts")
    release_path.write_text(json.dumps(release))
    report = tmp_path / "unverifiable_release.json"

    result = run_assessor(plan, report)

    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(report.read_text())
    assert payload["decision"] == "HOLD_PRODUCTION_CONTRACTS"
    assert "immutable_production_release" in payload["failed_screens"]
    assert payload["authorization"]["twenty_year_200m_production"] is False


def test_release_artifact_outside_approved_destination_does_not_authorize(
    tmp_path,
):
    plan, _, _ = make_fixture(tmp_path)
    annual = json.loads(plan.read_text())
    release_path = Path(annual["validation_reports"]["production_release"])
    release = json.loads(release_path.read_text())
    release["artifacts"]["source_archive"] = artifact(
        tmp_path / "outside_source.tar",
        b"source",
    )
    release_path.write_text(json.dumps(release))
    report = tmp_path / "outside_release.json"

    result = run_assessor(plan, report)

    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(report.read_text())
    assert payload["decision"] == "HOLD_PRODUCTION_CONTRACTS"
    assert "immutable_production_release" in payload["failed_screens"]


def test_seasonal_hicar_degradation_blocks_production(tmp_path):
    plan, _, paths = make_fixture(tmp_path)
    station = json.loads(paths["station"].read_text())
    station["seasonal_metrics"]["DJF"]["hicar"]["all_sites"][
        "temperature_2m_height_adjusted_k"
    ]["root_mean_squared_error"] = 10.0
    paths["station"].write_text(json.dumps(station))
    report = tmp_path / "degraded.json"

    result = run_assessor(plan, report)

    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(report.read_text())
    assert payload["decision"] == "HOLD_AND_DIAGNOSE"
    assert "seasonal_non_degradation_against_rea_l" in payload["failed_screens"]
    assert payload["authorization"]["twenty_year_200m_production"] is False


def test_duplicate_or_out_of_window_observation_times_cannot_inflate_completeness(
    tmp_path,
):
    plan, _, paths = make_fixture(tmp_path)
    station = json.loads(paths["station"].read_text())
    station["matched_model_times"][-1] = station["matched_model_times"][-2]
    station["matched_model_times"][0] = "2018-01-01T00:00:00"
    paths["station"].write_text(json.dumps(station))
    report = tmp_path / "invalid_observation_axis.json"

    result = run_assessor(plan, report)

    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(report.read_text())
    assert payload["decision"] == "HOLD_AND_DIAGNOSE"
    assert "seasonal_observation_completeness" in payload["failed_screens"]
    screen = next(
        item
        for item in payload["screens"]
        if item["id"] == "seasonal_observation_completeness"
    )
    assert screen["axes"]["station"]["duplicates"] == 1
    assert screen["axes"]["station"]["outside_expected_axis"] == [
        "2018-01-01T00:00:00"
    ]
    assert payload["authorization"]["twenty_year_200m_production"] is False


def test_declared_annual_reference_counts_must_match_frozen_axes(tmp_path):
    plan, _, _ = make_fixture(tmp_path)
    annual = json.loads(plan.read_text())
    scientific_path = Path(annual["scientific_plan"])
    scientific = json.loads(scientific_path.read_text())
    criteria = scientific["promotion_criteria"][
        "annual_cycle_to_20_year_campaign"
    ]
    criteria["expected_station_model_times"] -= 1
    scientific_path.write_text(json.dumps(scientific))
    report = tmp_path / "inconsistent_reference_counts.json"

    result = run_assessor(plan, report)

    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(report.read_text())
    assert payload["decision"] == "HOLD_AND_DIAGNOSE"
    assert "seasonal_observation_completeness" in payload["failed_screens"]
    assert payload["authorization"]["twenty_year_200m_production"] is False


def test_missing_publication_keeps_annual_assessment_incomplete(tmp_path):
    plan, _, paths = make_fixture(tmp_path)
    Path(f"{paths['physical']}.ready").unlink()
    report = tmp_path / "incomplete.json"

    result = run_assessor(plan, report)

    assert result.returncode == 1
    payload = json.loads(report.read_text())
    assert payload["assessment_status"] == "INCOMPLETE"
    assert payload["decision"] == "INCOMPLETE"
    assert not Path(f"{report}.ready").exists()


def test_balfrin_wrapper_uses_cpu_partition_and_published_plan():
    wrapper = (
        ROOT
        / "case_studies"
        / "swiss_200m"
        / "scripts"
        / "assess_scientific_annual_balfrin.sbatch"
    ).read_text()

    assert "#SBATCH --partition=pp-short" in wrapper
    assert "ANNUAL_PILOT_PLAN" in wrapper
    assert '"$annual_plan.ready"' in wrapper
