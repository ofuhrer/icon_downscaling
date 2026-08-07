from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import netCDF4


ROOT = Path(__file__).resolve().parents[1]
WIND_ROOT = ROOT / "case_studies/swiss_200m/wind_climatology"
if str(WIND_ROOT) not in sys.path:
    sys.path.insert(0, str(WIND_ROOT))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


load_module(
    "assess_wind_spinup_convergence",
    WIND_ROOT / "assess_wind_spinup_convergence.py",
)
ASSESS = load_module(
    "assess_wind_overlap_handoffs",
    WIND_ROOT / "assess_wind_overlap_handoffs.py",
)


def publish(path: Path) -> None:
    Path(f"{path}.ready").touch()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_history(path: Path, start_hour: int, offset: float) -> None:
    times = list(range(start_hour, 26))
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("time", len(times))
        dataset.createDimension("height_agl", 6)
        dataset.createDimension("lat_y", 2)
        dataset.createDimension("lon_x", 2)
        time = dataset.createVariable("time", "f8", ("time",))
        time.units = "hours since 2020-07-01 00:00:00"
        time[:] = times
        height = dataset.createVariable("height_agl", "f4", ("height_agl",))
        height[:] = [50, 75, 100, 125, 150, 200]
        u10 = dataset.createVariable("u10m", "f4", ("time", "lat_y", "lon_x"))
        v10 = dataset.createVariable("v10m", "f4", ("time", "lat_y", "lon_x"))
        u = dataset.createVariable(
            "u_agl", "f4", ("time", "height_agl", "lat_y", "lon_x")
        )
        v = dataset.createVariable(
            "v_agl", "f4", ("time", "height_agl", "lat_y", "lon_x")
        )
        rho = dataset.createVariable(
            "rho_agl", "f4", ("time", "height_agl", "lat_y", "lon_x")
        )
        ustar = dataset.createVariable("ustar", "f4", ("time", "lat_y", "lon_x"))
        roughness = dataset.createVariable(
            "surface_roughness", "f4", ("time", "lat_y", "lon_x")
        )
        ri = dataset.createVariable("sfc_Ri", "f4", ("time", "lat_y", "lon_x"))
        hpbl = dataset.createVariable("hpbl", "f4", ("time", "lat_y", "lon_x"))
        u10[:] = 8.0 + offset
        v10[:] = 1.0
        u[:] = 8.0 + offset
        v[:] = 1.0
        rho[:] = 1.1
        ustar[:] = 0.3
        roughness[:] = 0.1
        ri[:] = 0.2
        hpbl[:] = 800.0
    publish(path)


def bound_inputs(tmp_path: Path, offsets: dict[int, float]):
    thresholds = {
        "vector_rmse_m_s": 0.2,
        "relative_vector_rmse": 0.03,
        "absolute_speed_bias_m_s": 0.1,
        "direction_mae_degrees": 5.0,
        "vector_error_p99_m_s": 0.75,
        "direction_min_speed_m_s": 2.0,
    }
    runs = []
    result_runs = []
    for hours in (0, 12, 24):
        run_id = f"spinup-summer-{hours:02d}h"
        runs.append(
            {
                "run_id": run_id,
                "case_id": "summer",
                "spinup_hours": hours,
                "retained_start_exclusive": "2020-07-01T00:00:00",
                "output_interval_seconds": 3600,
            }
        )
        history = tmp_path / f"history_{hours}.nc"
        write_history(history, -hours, offsets[hours])
        result_runs.append({"run_id": run_id, "history_file": str(history)})
    experiment_path = tmp_path / "experiment.json"
    experiment_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "candidate_spinup_hours": [0, 12, 24],
                "thresholds": thresholds,
                "runs": runs,
            }
        )
    )
    publish(experiment_path)
    results_path = tmp_path / "results.json"
    results_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "experiment_sha256": file_sha256(experiment_path),
                "runs": result_runs,
            }
        )
    )
    publish(results_path)
    convergence_path = tmp_path / "convergence.json"
    convergence_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "HOLD",
                "decision": "MINIMUM_SPINUP_NOT_BRACKETED",
                "experiment_sha256": file_sha256(experiment_path),
                "results_sha256": file_sha256(results_path),
            }
        )
    )
    publish(convergence_path)
    return experiment_path, results_path, convergence_path


def test_hard_handoff_assessor_passes_identical_members(tmp_path: Path) -> None:
    experiment, results, convergence = bound_inputs(
        tmp_path,
        {0: 0.0, 12: 0.0, 24: 0.0},
    )
    report = tmp_path / "report.json"
    payload = ASSESS.assess(
        experiment_path=experiment,
        results_path=results,
        convergence_path=convergence,
        report_path=report,
    )
    assert payload["status"] == "PASS"
    assert payload["decision"] == "READY_FOR_OBSERVATIONAL_SKILL_GATE"
    assert payload["method"]["field_blending"] is False
    assert [item["owned_record_count"] for item in payload["cases"][0]["owned_cores"]] == [
        12,
        12,
        12,
    ]
    assert len(payload["cases"][0]["handoffs"]) == 2
    assert Path(f"{report}.ready").is_file()


def test_hard_handoff_assessor_holds_for_member_discontinuity(tmp_path: Path) -> None:
    experiment, results, convergence = bound_inputs(
        tmp_path,
        {0: 1.0, 12: 0.0, 24: 0.0},
    )
    payload = ASSESS.assess(
        experiment_path=experiment,
        results_path=results,
        convergence_path=convergence,
        report_path=tmp_path / "report.json",
    )
    assert payload["status"] == "HOLD"
    assert payload["decision"] == "SHORT_WINDOW_HANDOFF_DISCONTINUITY"
    assert payload["cases"][0]["handoffs"][0]["status"] == "PASS"
    assert payload["cases"][0]["handoffs"][1]["status"] == "FAIL"


def test_hard_handoff_assessor_rejects_unbound_convergence(tmp_path: Path) -> None:
    experiment, results, convergence = bound_inputs(
        tmp_path,
        {0: 0.0, 12: 0.0, 24: 0.0},
    )
    payload = json.loads(convergence.read_text())
    payload["results_sha256"] = "0" * 64
    convergence.write_text(json.dumps(payload))
    try:
        ASSESS.assess(
            experiment_path=experiment,
            results_path=results,
            convergence_path=convergence,
            report_path=tmp_path / "report.json",
        )
    except ValueError as error:
        assert "not bound to the results" in str(error)
    else:
        raise AssertionError("unbound convergence decision was accepted")


def test_balfrin_wrapper_uses_bounded_cpu_partition_and_runtime() -> None:
    wrapper = (
        WIND_ROOT / "assess_wind_overlap_handoffs_balfrin.sbatch"
    ).read_text()
    assert "#SBATCH --partition=pp-short" in wrapper
    assert "#SBATCH --cpus-per-task=8" in wrapper
    assert "#SBATCH --no-requeue" in wrapper
    assert '"$HICAR_OVERLAP_RUNTIME/assess_wind_overlap_handoffs.py"' in wrapper
    assert '"$HICAR_VALIDATION_PYTHON"' in wrapper
