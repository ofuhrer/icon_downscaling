from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path
import sys

import netCDF4
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "case_studies/swiss_200m/wind_climatology/"
    "assess_wind_spinup_mechanism.py"
)
COMPARE_SCRIPT = (
    ROOT
    / "case_studies/swiss_200m/wind_climatology/"
    "compare_netcdf_arrays.py"
)
PATHWAY_PREPARE_SCRIPT = (
    ROOT
    / "case_studies/swiss_200m/wind_climatology/"
    "prepare_wind_pathway_experiment.py"
)
PATHWAY_FINALIZE_SCRIPT = (
    ROOT
    / "case_studies/swiss_200m/wind_climatology/"
    "finalize_wind_pathway_forcing.py"
)
MECHANISM_FINALIZE_SCRIPT = (
    ROOT
    / "case_studies/swiss_200m/wind_climatology/"
    "finalize_wind_mechanism_assessment.py"
)


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MECHANISM = load_module("wind_mechanism", SCRIPT)
COMPARE = load_module("compare_netcdf_arrays", COMPARE_SCRIPT)
PATHWAY_PREPARE = load_module(
    "prepare_wind_pathway_experiment", PATHWAY_PREPARE_SCRIPT
)
PATHWAY_FINALIZE = load_module(
    "finalize_wind_pathway_forcing", PATHWAY_FINALIZE_SCRIPT
)
MECHANISM_FINALIZE = load_module(
    "finalize_wind_mechanism_assessment", MECHANISM_FINALIZE_SCRIPT
)


def publish(path: Path) -> None:
    Path(f"{path}.ready").touch()


def write_restart(path: Path, offset: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("time", 1)
        dataset.createDimension("level", 2)
        dataset.createDimension("nsoil", 4)
        dataset.createDimension("lat_y", 6)
        dataset.createDimension("lon_x", 6)
        dataset.createDimension("lon_u", 7)
        dataset.createDimension("lat_v", 7)
        u = dataset.createVariable(
            "u", "f4", ("time", "level", "lat_y", "lon_u")
        )
        v = dataset.createVariable(
            "v", "f4", ("time", "level", "lat_v", "lon_x")
        )
        u[:] = 5.0 + offset
        v[:] = 1.0
        for name, base in (
            ("density", 1.1),
            ("potential_temperature", 290.0),
            ("temperature", 285.0),
            ("qv", 0.005),
            ("pressure", 90000.0),
        ):
            variable = dataset.createVariable(
                name, "f4", ("time", "level", "lat_y", "lon_x")
            )
            variable[:] = base + offset
        for name, base in (
            ("u10m", 4.0),
            ("v10m", 1.0),
            ("hpbl", 500.0),
            ("ustar", 0.3),
            ("surface_rad_temperature", 285.0),
            ("ground_surf_temperature", 284.0),
            ("taix", 283.0),
            ("psfc", 90000.0),
            ("hfss", 50.0),
            ("hfls", 25.0),
            ("wind_update_elapsed", 0.0),
            ("lsm_update_phase_offset", 0.0),
            ("radiation_update_phase_offset", 0.0),
            ("lsm_next_update_offset", 300.0),
            ("radiation_next_update_offset", 600.0),
        ):
            variable = dataset.createVariable(
                name, "f4", ("time", "lat_y", "lon_x")
            )
            variable[:] = base + (
                0.0 if "offset" in name or "elapsed" in name else offset
            )
        for name, base in (
            ("soil_temperature", 280.0),
            ("soil_water_content", 0.2),
        ):
            variable = dataset.createVariable(
                name, "f4", ("time", "nsoil", "lat_y", "lon_x")
            )
            variable[:] = base + offset


def build_run(
    campaign: Path, run_id: str, offset: float, container_hash: str
) -> dict[str, str]:
    run_root = (
        campaign
        / "execution/chains"
        / run_id
        / "segments/00001"
        / "attempts/a001/run"
    )
    restart = run_root.parent / "restart/state.nc"
    write_restart(restart, offset)
    completion = run_root / "model_chunk_completion.json"
    completion.parent.mkdir(parents=True, exist_ok=True)
    completion.write_text(
        json.dumps(
            {
                "status": "PASS",
                "end": "2020-01-02T01:00:00",
                "restart": {
                    "path": str(restart),
                    "sha256": f"restart-{run_id}",
                },
            }
        )
    )
    publish(completion)

    forcing = (
        campaign
        / "execution/chains"
        / run_id
        / "segments/00001/forcing_chunk/forcing"
    )
    forcing.mkdir(parents=True, exist_ok=True)
    validation = forcing / "forcing.validation.json"
    validation.write_text(
        json.dumps(
            {
                "status": "PASS",
                "forcing_file": str(forcing / "forcing.nc"),
                "ranges": {"U": [-2.0, 10.0]},
            }
        )
    )
    manifest = forcing / "forcing.manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "valid_time": "2020-01-01T00:00:00",
                "forcing_sha256": container_hash,
                "forcing_size_bytes": 1234,
                "source_dynamic": {"sha256": "same-dynamic"},
                "source_static": {"sha256": "same-static"},
                "validation_report": str(validation),
            }
        )
    )
    return {"run_id": run_id}


def test_mechanism_assessor_uses_restart_state_and_audits_forcing(
    tmp_path: Path,
) -> None:
    campaign = tmp_path / "campaign"
    runs = [
        build_run(
            campaign,
            "spinup-summer-convective-00h",
            offset=1.0,
            container_hash="container-a",
        ),
        build_run(
            campaign,
            "spinup-summer-convective-48h",
            offset=0.0,
            container_hash="container-b",
        ),
    ]
    results = campaign / "analysis/wind_spinup_results.json"
    results.parent.mkdir(parents=True)
    results.write_text(json.dumps({"runs": runs}))
    publish(results)
    output = campaign / "analysis/mechanism.json"

    payload = MECHANISM.assess(
        campaign_root=campaign,
        results_path=results,
        case_id="summer-convective",
        output_path=output,
        boundary_cells=1,
        sample_stride=1,
    )

    assert payload["status"] == "PASS"
    assert payload["forcing_identity"]["source_identity_pass"] is True
    assert payload["forcing_identity"]["validation_signature_pass"] is True
    assert (
        payload["forcing_identity"]["container_byte_identity_pass"] is False
    )
    comparison = payload["comparisons"][0]
    assert comparison["spinup_hours"] == 0
    assert np.isclose(
        comparison["metrics"]["wind_full_levels"]["vector_rmse_m_s"], 1.0
    )
    assert np.isclose(
        comparison["metrics"]["atmospheric_state"]["density"]["rmse"], 1.0
    )
    assert Path(f"{output}.ready").is_file()


def write_probe(path: Path, value: float, history: str) -> None:
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.history = history
        dataset.createDimension("time", 2)
        variable = dataset.createVariable("U", "f4", ("time",))
        variable[:] = [value, value + 1.0]
    publish(path)


def test_netcdf_comparator_ignores_container_metadata(tmp_path: Path) -> None:
    first = tmp_path / "first.nc"
    second = tmp_path / "second.nc"
    output = tmp_path / "comparison.json"
    write_probe(first, 1.0, "created first")
    write_probe(second, 1.0, "created second")

    payload = COMPARE.compare_files(first, second, output)

    assert payload["status"] == "PASS"
    assert payload["arrays_identical"] is True
    assert payload["container_byte_identical"] is False
    assert (
        payload["decision"] == "ARRAYS_IDENTICAL_CONTAINER_METADATA_DIFFERS"
    )
    assert payload["global_attributes_identical"]["history"] is False
    assert Path(f"{output}.ready").is_file()


def test_netcdf_comparator_holds_different_arrays(tmp_path: Path) -> None:
    first = tmp_path / "first.nc"
    second = tmp_path / "second.nc"
    output = tmp_path / "comparison.json"
    write_probe(first, 1.0, "same")
    write_probe(second, 2.0, "same")

    payload = COMPARE.compare_files(first, second, output)

    assert payload["status"] == "HOLD"
    assert payload["decision"] == "FORCING_ARRAYS_DIFFER"
    assert payload["variables"][0]["maximum_absolute_difference"] == 1.0


def test_pathway_planner_and_forcing_finalizer(tmp_path: Path) -> None:
    mechanism_dir = tmp_path / "mechanism"
    mechanism_dir.mkdir()
    static = tmp_path / "static.nc"
    static.write_bytes(b"static")
    publish(static)
    completions = {}
    restarts = {}
    for age in (24, 48):
        restart = tmp_path / f"restart-{age}.nc"
        restart.write_bytes(f"restart-{age}".encode())
        completion = tmp_path / f"completion-{age}.json"
        completion.write_text(
            json.dumps({"provenance": {"static_file": str(static)}})
        )
        restarts[age] = restart
        completions[age] = completion
    report = mechanism_dir / "test-case.json"
    report.write_text(
        json.dumps(
            {
                "final_valid_time": "2020-02-11T01:00:00",
                "reference_spinup_hours": 48,
                "reference_restart": str(restarts[48]),
                "reference_completion": str(completions[48]),
                "comparisons": [
                    {
                        "spinup_hours": 24,
                        "restart": str(restarts[24]),
                        "completion": str(completions[24]),
                    }
                ],
            }
        )
    )
    publish(report)
    experiment_root = tmp_path / "experiment"

    experiment = PATHWAY_PREPARE.prepare(
        mechanism_dir, experiment_root, ["test-case"]
    )

    assert len(experiment["runs"]) == 4
    assert {
        (run["sx"], run["advect_density"]) for run in experiment["runs"]
    } == {("on", "on"), ("off", "on")}
    assert len(experiment["cases"][0]["records"]) == 3
    for record in experiment["cases"][0]["records"]:
        forcing = Path(record["forcing_file"])
        forcing.parent.mkdir(parents=True, exist_ok=True)
        forcing.write_bytes(record["valid_time"].encode())
        publish(forcing)
        digest = hashlib.sha256(forcing.read_bytes()).hexdigest()
        forcing.with_suffix(".manifest.json").write_text(
            json.dumps(
                {
                    "status": "PASS",
                    "valid_time": record["valid_time"],
                    "forcing_sha256": digest,
                }
            )
        )
        forcing.with_suffix(".validation.json").write_text(
            json.dumps({"status": "PASS"})
        )

    finalized = PATHWAY_FINALIZE.finalize(
        experiment_root / "experiment_plan.json"
    )

    assert finalized["status"] == "PASS"
    case = experiment["cases"][0]
    assert Path(f"{case['model_plan']}.ready").is_file()
    assert Path(f"{case['forcing_list']}.ready").is_file()
    assert Path(f"{case['forcing_publication']}.ready").is_file()


def test_mechanism_finalizer_selects_wind_only_gate(tmp_path: Path) -> None:
    mechanism_dir = tmp_path / "mechanism"
    mechanism_dir.mkdir()
    case_report = mechanism_dir / "case.json"
    case_report.write_text(
        json.dumps(
            {
                "status": "PASS",
                "decision": "MECHANISM_EVIDENCE_READY",
                "case_id": "test",
                "final_valid_time": "2020-01-01T00:00:00",
                "comparisons": [
                    {
                        "spinup_hours": 24,
                        "metrics": {
                            "wind_full_levels": {"vector_rmse_m_s": 2.0},
                            "wind_10m": {"vector_rmse_m_s": 1.0},
                            "atmospheric_state": {
                                "pressure": {"rmse": 0.0},
                                "density": {"rmse": 0.01},
                                "potential_temperature": {"rmse": 2.0},
                                "qv": {"rmse": 0.001},
                            },
                            "surface_state": {
                                "ustar": {"rmse": 0.1},
                                "hpbl": {"rmse": 200.0},
                            },
                        },
                    }
                ],
            }
        )
    )
    publish(case_report)
    forcing = tmp_path / "forcing.json"
    forcing.write_text(
        json.dumps(
            {
                "status": "PASS",
                "arrays_identical": True,
                "container_byte_identical": False,
                "global_attributes_identical": {"history": False},
            }
        )
    )
    publish(forcing)
    pathway = tmp_path / "pathway.json"
    pathway.write_text(
        json.dumps(
            {
                "status": "PASS",
                "cases": [
                    {
                        "case_id": "test",
                        "screens": {
                            "sx_removed": {
                                "status": "PASS",
                                "full_level_difference_ratio_to_baseline": 0.9,
                                "wind_10m_difference_ratio_to_baseline": 0.85,
                                "full_level_interpretation": "NOT_DOMINANT",
                                "wind_10m_interpretation": "NOT_DOMINANT",
                            }
                        },
                    }
                ],
            }
        )
    )
    publish(pathway)
    output = tmp_path / "conclusion.json"

    result = MECHANISM_FINALIZE.finalize(
        mechanism_dir, forcing, pathway, output
    )

    assert result["decision"] == "COUPLED_HICAR_SEGMENT_STRATEGY_REJECTED"
    assert (
        result["pathway_screen"]["sx_classification"]
        == "SX_NOT_DOMINANT"
    )
    assert (
        result["production_decision"]["recommended_next_gate"]
        == "INSTANTANEOUS_WIND_ONLY_DOWNSCALING"
    )
    assert Path(f"{output}.ready").is_file()
