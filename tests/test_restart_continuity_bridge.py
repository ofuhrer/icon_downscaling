from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from netCDF4 import Dataset
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "scripts"
    / "run_restart_continuity_bridge_balfrin.sbatch"
)
NATIONAL_WRAPPER = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "scripts"
    / "run_restart_continuity_national_balfrin.sbatch"
)
VALIDATOR = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "validation"
    / "validate_restart_continuity_bridge.py"
)
VALIDATION_WRAPPER = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "scripts"
    / "validate_restart_continuity_bridge_balfrin.sbatch"
)
BUILD_REFERENCE = (
    ROOT
    / ".agents"
    / "skills"
    / "hicar-balfrin-runtime"
    / "references"
    / "build-and-performance.md"
)
DIAGNOSTIC_POLICY = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "validation"
    / "restart_continuity"
    / "restart_equivalence_diagnostic_policy_v1.json"
)


def test_bridge_uses_normal_gpu_partition_and_frozen_source() -> None:
    text = WRAPPER.read_text()

    assert "#SBATCH --partition=normal" in text
    assert "#SBATCH --nodes=2" in text
    assert "#SBATCH --gres=gpu:4" in text
    for name in (
        "HICAR_RESTART_SOURCE_ROOT",
        "HICAR_RESTART_BUILD_ROOT",
        "HICAR_RESTART_EXPECTED_COMMIT",
    ):
        assert f"${{{name}:?" in text
    assert "git -C \"$source_root\" diff --quiet" in text
    assert "git -C \"$source_root\" diff --cached --quiet" in text
    assert "HICAR_RESTART_EXPECTED_PARENT" in text


def test_bridge_runs_uninterrupted_then_restart_from_one_hour() -> None:
    text = WRAPPER.read_text()

    assert 'printf "\\"%s\\"\\n", $0' in text
    assert 'done < "$forcing_list"' in text
    assert '"forcing_file_list": forcing_list' in text
    assert '"2010-01-01 00:00:00" "2010-01-01 02:00:00" ".False."' in text
    assert '"2010-01-01 01:00:00" "2010-01-01 02:00:00" ".True."' in text
    assert '"2010-01-01 01:00:00"' in text
    assert 'run_model continuous' in text
    assert 'run_model restart' in text
    assert '"*_2010-01-01_01-00-00.nc"' in text
    assert '"*_2010-01-01_02-00-00.nc"' in text
    for variable in (
        "canopy_ice",
        "canopy_liquid",
        "canopy_fwet",
        "lsm_last_precip",
        "lsm_last_snow",
        "snow_height",
        "swe_0",
        "tend_th_lwrad",
        "lsm_timestep_counter",
        "u",
        "v",
        "w",
        "pressure",
        "temperature",
        "potential_temperature",
        "qv",
        "density",
    ):
        assert f"'{variable}'" in text


def test_bridge_manifest_attests_restart_artifacts() -> None:
    text = WRAPPER.read_text()

    assert '"status": "MODEL_RUNS_COMPLETE"' in text
    for key in (
        "source_commit",
        "executable",
        "forcing_list",
        "static_file",
        "continuous_output",
        "restart_output",
        "source_restart",
        "continuous_end_restart",
        "segmented_end_restart",
    ):
        assert f'"{key}"' in text


def test_national_gate_uses_four_node_nccL_topology_and_two_legs() -> None:
    text = NATIONAL_WRAPPER.read_text()

    assert "#SBATCH --partition=normal" in text
    assert "#SBATCH --nodes=4" in text
    assert "#SBATCH --ntasks-per-node=5" in text
    assert "#SBATCH --gres=gpu:4" in text
    assert "export MPICH_GPU_SUPPORT_ENABLED=0" in text
    assert 'run_model continuous' in text
    assert 'run_model restart' in text
    assert 'run_model parent' not in text
    assert '"2020-07-01 00:00:00" "2020-07-01 02:00:00"' in text
    assert '"2020-07-01 01:00:00" "2020-07-01 02:00:00"' in text


def test_national_gate_freezes_source_renderer_and_manifest_artifacts() -> None:
    text = NATIONAL_WRAPPER.read_text()

    for name in (
        "HICAR_RESTART_SOURCE_ROOT",
        "HICAR_RESTART_BUILD_ROOT",
        "HICAR_RESTART_EXPECTED_COMMIT",
        "HICAR_RESTART_EXPECTED_PARENT",
        "HICAR_RESTART_RENDERER_SHA256",
        "HICAR_RESTART_TEMPLATE_SHA256",
    ):
        assert f"${{{name}:?" in text
    assert "git -C \"$source_root\" diff --quiet" in text
    assert "git -C \"$source_root\" diff --cached --quiet" in text
    for key in (
        "source_commit",
        "executable",
        "renderer",
        "forcing_list",
        "static_file",
        "continuous_output",
        "restart_output",
        "source_restart",
        "continuous_end_restart",
        "segmented_end_restart",
    ):
        assert f'"{key}"' in text


def test_validator_requires_narrow_restart_source_scope() -> None:
    text = VALIDATOR.read_text()

    for path in (
        "src/constants/icar_constants.F90",
        "src/io/default_output_metadata.F90",
        "src/physics/lsm_driver.F90",
        "src/physics/pbl_driver.F90",
    ):
        assert f'"{path}"' in text
    assert '"counter_persisted_exactly"' in text
    assert '"cadence_state_within_declared_tolerance"' in text
    assert '"bounded_diagnostic_policy"' in text
    assert 'result["variable"] == "lsm_timestep_counter"' in text
    assert "CADENCE_STATE_FIELDS" in text
    for field in (
        "lsm_update_phase_offset",
        "lsm_next_update_offset",
        "radiation_update_phase_offset",
        "radiation_next_update_offset",
    ):
        assert f'"{field}"' in text
    assert "--expected-parent" in text
    assert "--expected-changed-file" in text
    assert 'checked_artifact(manifest["forcing_list"])' in text
    assert '"restart_tolerances"' in text
    assert '"sha256": sha256(args.restart_tolerances)' in text
    assert "--diagnostic-policy" in text
    assert "--scope" in text
    assert 'ready.unlink()' in text


def test_validation_runs_on_cpu_partition_with_frozen_model_job() -> None:
    text = VALIDATION_WRAPPER.read_text()

    assert "#SBATCH --partition=pp-short" in text
    assert "${HICAR_RESTART_BRIDGE_JOB_ID:?" in text
    assert "--model-job-id \"$model_job_id\"" in text
    assert "HICAR_RESTART_EXPECTED_PARENT" in text
    assert "HICAR_RESTART_EXPECTED_CHANGED_FILES" in text
    assert "IFS=':'" in text
    assert "Slurm's --export separator" in text
    assert "test ! -e \"$output\"" in text
    assert "HICAR_RESTART_DIAGNOSTIC_POLICY" in text
    assert "HICAR_RESTART_RUN_ROOT" in text
    assert "HICAR_RESTART_SCOPE" in text
    assert "HICAR_RESTART_RUNNER" in text
    assert 'validator_args+=(--diagnostic-policy "$diagnostic_policy")' in text


def _load_restart_validator():
    spec = importlib.util.spec_from_file_location(
        "validate_restart_continuity_bridge", VALIDATOR
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_diagnostic_fixture(path: Path, values: np.ndarray) -> None:
    with Dataset(path, "w") as dataset:
        dataset.createDimension("time", values.shape[0])
        dataset.createDimension("level", values.shape[1])
        dataset.createDimension("lat_y", values.shape[2])
        dataset.createDimension("lon_x", values.shape[3])
        variable = dataset.createVariable(
            "tend_th_lwrad",
            "f4",
            ("time", "level", "lat_y", "lon_x"),
        )
        variable[:] = values


def test_bounded_diagnostic_policy_downgrades_only_within_all_caps(
    tmp_path: Path,
) -> None:
    validator = _load_restart_validator()
    reference = tmp_path / "reference.nc"
    candidate = tmp_path / "candidate.nc"
    reference_values = np.zeros((1, 2, 10, 10), dtype=np.float32)
    candidate_values = reference_values.copy()
    candidate_values[0, 0, 0, 0] = 5.0e-5
    _write_diagnostic_fixture(reference, reference_values)
    _write_diagnostic_fixture(candidate, candidate_values)
    policy = json.loads(DIAGNOSTIC_POLICY.read_text())
    policy["diagnostic_fields"]["tend_th_lwrad"].update(
        {
            "max_violation_fraction": 0.01,
            "max_abs_difference": 1.0e-4,
            "max_rms_difference": 1.0e-5,
            "max_abs_mean_signed_difference": 1.0e-6,
        }
    )
    comparison = {
        "failure_count": 1,
        "warning_count": 0,
        "results": [
            {
                "variable": "tend_th_lwrad",
                "status": "FAIL",
                "finite_count": 200,
                "violations": 1,
                "introduced_nonfinite": 0,
                "fill_mismatch": 0,
            }
        ],
    }

    evaluations = validator.apply_diagnostic_policy(
        comparison,
        reference,
        candidate,
        policy,
        last_time=True,
    )

    assert evaluations[0]["status"] == "PASS"
    assert comparison["failure_count"] == 0
    assert comparison["warning_count"] == 1
    assert comparison["results"][0]["status"] == "WARN"


def test_bounded_diagnostic_policy_preserves_failure_outside_any_cap(
    tmp_path: Path,
) -> None:
    validator = _load_restart_validator()
    reference = tmp_path / "reference.nc"
    candidate = tmp_path / "candidate.nc"
    reference_values = np.zeros((1, 1, 2, 2), dtype=np.float32)
    candidate_values = reference_values.copy()
    candidate_values[0, 0, 0, 0] = 3.0e-3
    _write_diagnostic_fixture(reference, reference_values)
    _write_diagnostic_fixture(candidate, candidate_values)
    policy = json.loads(DIAGNOSTIC_POLICY.read_text())
    comparison = {
        "failure_count": 1,
        "warning_count": 0,
        "results": [
            {
                "variable": "tend_th_lwrad",
                "status": "FAIL",
                "finite_count": 4,
                "violations": 1,
                "introduced_nonfinite": 0,
                "fill_mismatch": 0,
            }
        ],
    }

    evaluations = validator.apply_diagnostic_policy(
        comparison,
        reference,
        candidate,
        policy,
        last_time=True,
    )

    assert evaluations[0]["status"] == "FAIL"
    assert not evaluations[0]["checks"]["max_abs_difference"]
    assert comparison["failure_count"] == 1
    assert comparison["results"][0]["status"] == "FAIL"


def test_runtime_reference_freezes_cpu_and_gpu_build_contracts() -> None:
    text = BUILD_REFERENCE.read_text()

    assert "## CPU release build" in text
    assert "-DOPENACC=OFF -DNCCL=OFF" in text
    assert "## A100 OpenACC build: single-node GPU-aware MPI" in text
    assert "-DOPENACC=ON -DNCCL=OFF" in text
    assert "## A100 OpenACC build: multi-node NCCL production topology" in text
    assert "-DOPENACC=ON -DNCCL=ON" in text
    assert "-DCMAKE_C_COMPILER=nvc -DCMAKE_CXX_COMPILER=nvc++" in text
    assert "Do not export `CC=mpicc`" in text
    assert "! ldd \"$BUILD/HICAR_gpu\" | grep -q 'not found'" in text
