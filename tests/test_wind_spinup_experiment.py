from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import datetime
from pathlib import Path
import sys

import netCDF4
ROOT = Path(__file__).resolve().parents[1]
WIND_ROOT = ROOT / "case_studies/swiss_200m/wind_climatology"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


PREPARE = load_module(
    "prepare_wind_spinup_experiment",
    WIND_ROOT / "prepare_wind_spinup_experiment.py",
)
ASSESS = load_module(
    "assess_wind_spinup_convergence",
    WIND_ROOT / "assess_wind_spinup_convergence.py",
)


def publish(path: Path) -> None:
    Path(f"{path}.ready").touch()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def small_contract(tmp_path: Path) -> Path:
    source = json.loads(
        (WIND_ROOT / "wind_production_candidate.json").read_text()
    )
    source["spinup"].update(
        {
            "candidate_hours": [0, 1, 2],
            "reference_spinup_hours": 2,
            "retained_hours": 2,
            "overlap_hours": 1,
            "screen_output_interval_seconds": 3600,
        }
    )
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(source))
    return path


def write_history(
    path: Path, offset: float, times: tuple[int, ...] = (1, 2, 3)
) -> None:
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("time", len(times))
        dataset.createDimension("height_agl", 6)
        dataset.createDimension("lat_y", 2)
        dataset.createDimension("lon_x", 2)
        time = dataset.createVariable("time", "f8", ("time",))
        time.units = "hours since 2020-07-01 00:00:00"
        time[:] = times
        height = dataset.createVariable(
            "height_agl", "f4", ("height_agl",)
        )
        height[:] = [50, 75, 100, 125, 150, 200]
        u10 = dataset.createVariable(
            "u10m", "f4", ("time", "lat_y", "lon_x")
        )
        v10 = dataset.createVariable(
            "v10m", "f4", ("time", "lat_y", "lon_x")
        )
        u = dataset.createVariable(
            "u_agl", "f4", ("time", "height_agl", "lat_y", "lon_x")
        )
        v = dataset.createVariable(
            "v_agl", "f4", ("time", "height_agl", "lat_y", "lon_x")
        )
        rho = dataset.createVariable(
            "rho_agl", "f4", ("time", "height_agl", "lat_y", "lon_x")
        )
        ustar = dataset.createVariable(
            "ustar", "f4", ("time", "lat_y", "lon_x")
        )
        roughness = dataset.createVariable(
            "surface_roughness", "f4", ("time", "lat_y", "lon_x")
        )
        ri = dataset.createVariable(
            "sfc_Ri", "f4", ("time", "lat_y", "lon_x")
        )
        hpbl = dataset.createVariable(
            "hpbl", "f4", ("time", "lat_y", "lon_x")
        )
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


def test_prepare_builds_discard_and_overlap_chains(tmp_path: Path) -> None:
    output = tmp_path / "experiment.json"
    payload = PREPARE.build_experiment(
        contract_path=small_contract(tmp_path),
        cases=[("summer", datetime(2020, 7, 1))],
        output=output,
    )
    assert Path(f"{output}.ready").is_file()
    assert [run["spinup_hours"] for run in payload["runs"]] == [0, 1, 2]
    one_hour = payload["runs"][1]
    assert one_hour["integration_start"] == "2020-06-30T23:00:00"
    assert one_hour["discard_before"] == "2020-07-01T00:00:00"
    assert one_hour["retained_end_inclusive"] == "2020-07-01T02:00:00"
    assert one_hour["overlap_end_inclusive"] == "2020-07-01T03:00:00"
    assert payload["preemptible_campaign_fragment"]["policy"]["segment_hours"] == 24


def test_prepare_binds_time_matched_static_files(tmp_path: Path) -> None:
    output = tmp_path / "experiment.json"
    static_root = tmp_path / "statics"
    payload = PREPARE.build_experiment(
        contract_path=small_contract(tmp_path),
        cases=[("summer", datetime(2020, 7, 1))],
        output=output,
        static_root=static_root,
    )
    runs = {run["spinup_hours"]: run for run in payload["runs"]}
    assert runs[0]["static_file"].endswith(
        "domain_static_alpine_bridge_200m_rea_l_20200701_0000.nc"
    )
    assert runs[1]["static_file"].endswith(
        "domain_static_alpine_bridge_200m_rea_l_20200630_2300.nc"
    )
    chains = payload["preemptible_campaign_fragment"]["chains"]
    assert {chain["static_file"] for chain in chains} == {
        run["static_file"] for run in payload["runs"]
    }


def test_production_candidate_preserves_assessed_wind_contract() -> None:
    candidate = json.loads(
        (WIND_ROOT / "wind_production_candidate.json").read_text()
    )
    assert candidate["production_output"]["variables"] == [
        "u10m",
        "v10m",
        "u_agl",
        "v_agl",
        "rho_agl",
        "ustar",
        "surface_roughness",
        "sfc_Ri",
        "hpbl",
    ]
    assert candidate["production_output"]["raw_float_field_equivalents"] == 24
    assert candidate["numerics"]["terrain_smoothing_window"] == 5
    assert candidate["numerics"]["terrain_smoothing_cycles"] == 10
    assert candidate["gust_policy"]["icon_vmax"] == "EXCLUDED"
    assert candidate["gust_policy"]["wind_speed_of_gust"] == (
        "GATED_NOT_PRODUCTION"
    )
    template = (
        ROOT / "case_studies/swiss_200m/config/hicar_swiss_200m.nml.in"
    ).read_text()
    assert "mp = 'morrison'" in template
    assert "model_top_height = 12000.0" in template
    assert "terrain_smooth_windowsize = 5" in template
    assert "terrain_smooth_cycles = 10" in template
    assert "smooth_wind_distance = 500.0" in template


def test_assessor_selects_shortest_stable_passing_tail(tmp_path: Path) -> None:
    experiment_path = tmp_path / "experiment.json"
    PREPARE.build_experiment(
        contract_path=small_contract(tmp_path),
        cases=[("summer", datetime(2020, 7, 1))],
        output=experiment_path,
    )
    histories = {}
    for hours, offset in ((0, 1.0), (1, 0.05), (2, 0.0)):
        path = tmp_path / f"history_{hours}.nc"
        write_history(path, offset)
        histories[f"spinup-summer-{hours:02d}h"] = path
    results_path = tmp_path / "results.json"
    results_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "experiment_sha256": file_sha256(experiment_path),
                "runs": [
                    {"run_id": run_id, "history_file": str(path)}
                    for run_id, path in histories.items()
                ],
            }
        )
    )
    publish(results_path)
    report_path = tmp_path / "assessment.json"
    payload = ASSESS.assess(
        experiment_path=experiment_path,
        results_path=results_path,
        report_path=report_path,
    )
    assert payload["status"] == "PASS"
    assert payload["selected_spinup_hours"] == 1
    assert payload["pass_by_spinup_hours"] == {0: False, 1: True, 2: True}
    assert Path(f"{report_path}.ready").is_file()


def test_assessor_does_not_treat_reference_self_match_as_convergence(
    tmp_path: Path,
) -> None:
    experiment_path = tmp_path / "experiment.json"
    PREPARE.build_experiment(
        contract_path=small_contract(tmp_path),
        cases=[("summer", datetime(2020, 7, 1))],
        output=experiment_path,
    )
    histories = {}
    for hours, offset in ((0, 1.0), (1, 1.0), (2, 0.0)):
        path = tmp_path / f"history_{hours}.nc"
        write_history(path, offset)
        histories[f"spinup-summer-{hours:02d}h"] = path
    results_path = tmp_path / "results.json"
    results_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "experiment_sha256": file_sha256(experiment_path),
                "runs": [
                    {"run_id": run_id, "history_file": str(path)}
                    for run_id, path in histories.items()
                ],
            }
        )
    )
    publish(results_path)
    report_path = tmp_path / "assessment.json"
    payload = ASSESS.assess(
        experiment_path=experiment_path,
        results_path=results_path,
        report_path=report_path,
    )
    assert payload["status"] == "HOLD"
    assert payload["decision"] == "MINIMUM_SPINUP_NOT_BRACKETED"
    assert payload["selected_spinup_hours"] is None
    assert payload["lower_bound_spinup_hours"] == 2
    assert payload["pass_by_spinup_hours"] == {0: False, 1: False, 2: True}


def test_assessor_reads_segmented_history_files(tmp_path: Path) -> None:
    experiment_path = tmp_path / "experiment.json"
    PREPARE.build_experiment(
        contract_path=small_contract(tmp_path),
        cases=[("summer", datetime(2020, 7, 1))],
        output=experiment_path,
    )
    result_runs = []
    for hours, offset in ((0, 1.0), (1, 0.05), (2, 0.0)):
        first = tmp_path / f"history_{hours}_first.nc"
        second = tmp_path / f"history_{hours}_second.nc"
        write_history(first, offset, (1,))
        write_history(second, offset, (2, 3))
        result_runs.append(
            {
                "run_id": f"spinup-summer-{hours:02d}h",
                "history_files": [str(first), str(second)],
            }
        )
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
    report_path = tmp_path / "assessment.json"
    payload = ASSESS.assess(
        experiment_path=experiment_path,
        results_path=results_path,
        report_path=report_path,
    )
    assert payload["status"] == "PASS"
    assert payload["selected_spinup_hours"] == 1
