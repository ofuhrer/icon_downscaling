from __future__ import annotations

import json
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import netCDF4
import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "streaming"
    / "validate_model_chunk.py"
)
VALIDATOR_SPEC = importlib.util.spec_from_file_location(
    "streaming_model_validator",
    VALIDATOR,
)
VALIDATOR_MODULE = importlib.util.module_from_spec(VALIDATOR_SPEC)
VALIDATOR_SPEC.loader.exec_module(VALIDATOR_MODULE)
ROUTINE = (
    "precipitation",
    "psfc",
    "taix",
    "hus2m",
    "u10m",
    "v10m",
    "rsds",
    "lwtr",
    "rlus",
    "hfgs",
    "emiss",
)

QUALIFICATION = ROUTINE + (
    "snowfall",
    "graupel",
    "hfss",
    "hfls",
    "tsfe",
    "albedo",
    "canopy_water",
    "swet",
    "snow_height",
    "soil_column_total_water",
    "soil_water_content",
    "soil_temperature",
    "runoff_surface",
    "runoff_subsurface",
    "runoff_surface_cumulative",
    "runoff_subsurface_cumulative",
    "evaporation_net_cumulative",
    "water_aquifer",
    "storage_gw",
    "wetland_h20_store",
)

def write_output(
    path: Path,
    hours: list[int],
    variables: tuple[str, ...] = ROUTINE,
    include_z: bool = False,
) -> None:
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("time", len(hours))
        dataset.createDimension("y", 1)
        dataset.createDimension("x", 1)
        time = dataset.createVariable("time", "f8", ("time",))
        time.units = "hours since 2020-01-01 00:00:00"
        time[:] = hours
        for name in variables:
            value = {
                "psfc": 90000.0,
                "taix": 280.0,
                "tsfe": 280.0,
                "soil_temperature": 280.0,
                "emiss": 0.95,
                "albedo": 0.2,
                # Multi-day accumulated amounts can legitimately exceed the
                # old short-smoke limit of 10 kg m-2.
                "snowfall": 31.75,
                "graupel": 190.25,
            }.get(name, 0.0)
            variable = dataset.createVariable(
                name, "f4", ("time", "y", "x")
            )
            variable[:] = value
            if name in {
                "precipitation",
                "runoff_surface_cumulative",
                "runoff_subsurface_cumulative",
                "evaporation_net_cumulative",
            }:
                variable.units = "kg m-2"
                variable.accumulation_semantics = (
                    "cumulative since simulation start; no output reset; "
                    "restart-persistent"
                )
                variable.interval_semantics = (
                    "difference consecutive records gives amount over "
                    "(previous_time, time]"
                )
        if include_z:
            dataset.createDimension("z", 1)
            dataset.createVariable("z", "f4", ("z", "y", "x"))[:] = 1


def write_wind_output(path: Path, hours: list[int], *, bad_height: bool = False) -> None:
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("time", len(hours))
        dataset.createDimension("height_agl", 6)
        dataset.createDimension("lat_y", 2)
        dataset.createDimension("lon_x", 2)
        time = dataset.createVariable("time", "f8", ("time",))
        time.units = "hours since 2020-01-01 00:00:00"
        time[:] = hours
        height = dataset.createVariable("height_agl", "f4", ("height_agl",))
        height.standard_name = "height"
        height.units = "m"
        height.positive = "up"
        height.axis = "Z"
        height[:] = [50, 75, 100, 125, 150, 175 if bad_height else 200]
        u10m = dataset.createVariable(
            "u10m", "f4", ("time", "lat_y", "lon_x")
        )
        u10m.standard_name = "eastward_wind"
        u10m.units = "m s-1"
        u10m[:] = 4.0
        v10m = dataset.createVariable(
            "v10m", "f4", ("time", "lat_y", "lon_x")
        )
        v10m.standard_name = "northward_wind"
        v10m.units = "m s-1"
        v10m[:] = -2.0
        metadata = {
            "u_agl": ("eastward_wind", "m s-1", 5.0),
            "v_agl": ("northward_wind", "m s-1", -1.0),
            "rho_agl": ("air_density", "kg m-3", 1.1),
        }
        for name, (standard_name, units, value) in metadata.items():
            variable = dataset.createVariable(
                name,
                "f4",
                ("time", "height_agl", "lat_y", "lon_x"),
            )
            variable.standard_name = standard_name
            variable.units = units
            variable.interpolation = (
                "linear in geometric height AGL; no extrapolation"
            )
            variable[:] = value
        pbl_metadata = {
            "ustar": (
                "magnitude_of_surface_friction_velocity_in_air",
                "m s-1",
                0.4,
            ),
            "surface_roughness": (
                "surface_roughness_length_for_momentum_in_air",
                "m",
                0.1,
            ),
            # Raw bulk Richardson number may exceed the SBRLIM=250 value
            # used internally by the similarity-function inversion.
            "sfc_Ri": (None, "1", 350.0),
            "hpbl": ("atmosphere_boundary_layer_thickness", "m", 600.0),
        }
        for name, (standard_name, units, value) in pbl_metadata.items():
            variable = dataset.createVariable(
                name,
                "f4",
                ("time", "lat_y", "lon_x"),
            )
            if standard_name is not None:
                variable.standard_name = standard_name
            variable.units = units
            variable[:] = value


def test_model_validator_accepts_split_daily_output(tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "chunk_id": "split",
                "start": "2020-01-01T00:00:00",
                "end": "2020-01-01T02:00:00",
                "hours": 2,
            }
        )
    )
    first = tmp_path / "first.nc"
    second = tmp_path / "second.nc"
    write_output(first, [0, 1])
    write_output(second, [2])
    restart = tmp_path / "restart.nc"
    with netCDF4.Dataset(restart, "w") as dataset:
        dataset.createDimension("time", 1)
        time = dataset.createVariable("time", "f8", ("time",))
        time.units = "hours since 2020-01-01 00:00:00"
        time[:] = [2]
        dataset.dt_seconds = np.float32(3.75)
    model_log = tmp_path / "model.out"
    model_log.write_text(
        "\n".join(
            (
                "HICAR discretely adjoint wind projection enabled",
                "HICAR SLEVE geometry gate:",
                "Simulation completed successfully!",
                "Timing across all compute images:",
            )
        )
    )
    static = tmp_path / "static.nc"
    static.write_bytes(b"static-domain")
    source_commit = tmp_path / "source_commit.txt"
    source_commit.write_text("a" * 40 + "\n")
    source_tree_status = tmp_path / "source_tree_status.txt"
    source_tree_status.write_text("")
    executable = tmp_path / "HICAR_gpu"
    executable.write_bytes(b"frozen-executable")
    executable_digest = tmp_path / "executable.sha256"
    executable_digest.write_text(
        f"{hashlib.sha256(executable.read_bytes()).hexdigest()}  {executable}\n"
    )
    forcing_publication = tmp_path / "forcing_publication_source.json"
    forcing_publication.write_text(json.dumps({"status": "PASS"}))
    Path(f"{forcing_publication}.ready").touch()
    archived_plan = tmp_path / "chunk_plan.json"
    archived_plan.write_bytes(plan.read_bytes())
    archived_forcing = tmp_path / "forcing_publication.json"
    archived_forcing.write_bytes(forcing_publication.read_bytes())
    report = tmp_path / "completion.json"
    result = subprocess.run(
        [
            sys.executable,
            str(VALIDATOR),
            "--plan",
            str(plan),
            "--run-dir",
            str(tmp_path),
            "--static-file",
            str(static),
            "--output-file",
            str(first),
            str(second),
            "--restart-file",
            str(restart),
            "--model-log",
            str(model_log),
            "--source-commit-file",
            str(source_commit),
            "--source-tree-status-file",
            str(source_tree_status),
            "--executable",
            str(executable),
            "--executable-digest-file",
            str(executable_digest),
            "--forcing-publication",
            str(forcing_publication),
            "--archived-plan",
            str(archived_plan),
            "--archived-forcing-publication",
            str(archived_forcing),
            "--report",
            str(report),
        ],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(report.read_text())
    assert payload["status"] == "PASS"
    assert payload["schema_version"] == 2
    assert payload["provenance"]["status"] == "PASS"
    assert payload["provenance"]["source_tree_clean"] is True
    assert payload["provenance"]["static_sha256"] == hashlib.sha256(
        static.read_bytes()
    ).hexdigest()
    assert payload["model_log_artifact"]["sha256"] == hashlib.sha256(
        model_log.read_bytes()
    ).hexdigest()
    assert len(payload["output"]["files"]) == 2
    assert Path(f"{report}.ready").is_file()


def test_model_validator_provenance_rejects_tampered_executable(tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"chunk_id": "tampered"}))
    archived_plan = tmp_path / "chunk_plan.json"
    archived_plan.write_bytes(plan.read_bytes())
    forcing = tmp_path / "forcing-source.json"
    forcing.write_text(json.dumps({"status": "PASS"}))
    Path(f"{forcing}.ready").touch()
    archived_forcing = tmp_path / "forcing.json"
    archived_forcing.write_bytes(forcing.read_bytes())
    executable = tmp_path / "HICAR_gpu"
    executable.write_bytes(b"changed-after-recording")
    digest = tmp_path / "executable.sha256"
    digest.write_text(f"{'0' * 64}  {executable}\n")
    commit = tmp_path / "source_commit.txt"
    commit.write_text("a" * 40 + "\n")
    tree_status = tmp_path / "source_tree_status.txt"
    tree_status.write_text("")
    static = tmp_path / "static.nc"
    static.write_bytes(b"static")
    failures = []
    payload = VALIDATOR_MODULE.validate_provenance(
        SimpleNamespace(
            plan=plan,
            static_file=static,
            source_commit_file=commit,
            source_tree_status_file=tree_status,
            executable=executable,
            executable_digest_file=digest,
            forcing_publication=forcing,
            archived_plan=archived_plan,
            archived_forcing_publication=archived_forcing,
        ),
        failures,
    )
    assert payload["status"] == "FAIL"
    assert any("executable no longer matches" in failure for failure in failures)


def test_model_validator_accepts_restart_output_after_boundary(tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "chunk_id": "restart",
                "start": "2020-01-01T02:00:00",
                "end": "2020-01-01T04:00:00",
                "hours": 2,
            }
        )
    )
    output = tmp_path / "output.nc"
    write_output(output, [3, 4])
    restart = tmp_path / "restart.nc"
    with netCDF4.Dataset(restart, "w") as dataset:
        dataset.createDimension("time", 1)
        time = dataset.createVariable("time", "f8", ("time",))
        time.units = "hours since 2020-01-01 00:00:00"
        time[:] = [4]
        dataset.dt_seconds = np.float32(3.75)
    model_log = tmp_path / "model.out"
    model_log.write_text(
        "\n".join(
            (
                "HICAR discretely adjoint wind projection enabled",
                "HICAR SLEVE geometry gate:",
                "Simulation completed successfully!",
                "Timing across all compute images:",
            )
        )
    )
    report = tmp_path / "completion.json"
    result = subprocess.run(
        [
            sys.executable,
            str(VALIDATOR),
            "--plan",
            str(plan),
            "--run-dir",
            str(tmp_path),
            "--output-file",
            str(output),
            "--restart-file",
            str(restart),
            "--model-log",
            str(model_log),
            "--restart-continuation",
            "--report",
            str(report),
        ],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(report.read_text())
    assert payload["status"] == "PASS"
    assert payload["restart_continuation"]


def test_model_validator_accepts_three_hour_qualification_output(tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "chunk_id": "qualification",
                "start": "2020-01-01T00:00:00",
                "end": "2020-01-01T06:00:00",
                "hours": 6,
            }
        )
    )
    output = tmp_path / "output.nc"
    write_output(output, [0, 3, 6], QUALIFICATION, include_z=True)
    static = tmp_path / "static.nc"
    with netCDF4.Dataset(static, "w") as dataset:
        dataset.createDimension("y", 1)
        dataset.createDimension("x", 1)
        dataset.createVariable("landmask", "i2", ("y", "x"))[:] = 1
        dataset.createVariable("landuse", "i2", ("y", "x"))[:] = 7
    restart = tmp_path / "restart.nc"
    with netCDF4.Dataset(restart, "w") as dataset:
        dataset.createDimension("time", 1)
        time = dataset.createVariable("time", "f8", ("time",))
        time.units = "hours since 2020-01-01 00:00:00"
        time[:] = [6]
        dataset.dt_seconds = np.float32(3.75)
    model_log = tmp_path / "model.out"
    model_log.write_text(
        "\n".join(
            (
                "HICAR discretely adjoint wind projection enabled",
                "HICAR SLEVE geometry gate:",
                "Simulation completed successfully!",
                "Timing across all compute images:",
            )
        )
    )
    report = tmp_path / "completion.json"
    result = subprocess.run(
        [
            sys.executable,
            str(VALIDATOR),
            "--plan",
            str(plan),
            "--run-dir",
            str(tmp_path),
            "--static-file",
            str(static),
            "--output-file",
            str(output),
            "--restart-file",
            str(restart),
            "--model-log",
            str(model_log),
            "--output-profile",
            "qualification",
            "--output-interval-seconds",
            "10800",
            "--report",
            str(report),
        ],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(report.read_text())
    assert payload["status"] == "PASS"
    assert payload["output_profile"] == "qualification"
    assert payload["output_interval_seconds"] == 10800
    assert payload["output"]["ranges"]["soil_water_content"] == [0.0, 0.0]


def test_model_validator_accepts_fixed_height_wind_output(tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "chunk_id": "wind",
                "start": "2020-01-01T00:00:00",
                "end": "2020-01-01T02:00:00",
                "hours": 2,
            }
        )
    )
    output = tmp_path / "output.nc"
    write_wind_output(output, [0, 1, 2])
    restart = tmp_path / "restart.nc"
    with netCDF4.Dataset(restart, "w") as dataset:
        dataset.createDimension("time", 1)
        time = dataset.createVariable("time", "f8", ("time",))
        time.units = "hours since 2020-01-01 00:00:00"
        time[:] = [2]
        dataset.dt_seconds = np.float32(3.75)
    model_log = tmp_path / "model.out"
    model_log.write_text(
        "\n".join(
            (
                "HICAR discretely adjoint wind projection enabled",
                "HICAR SLEVE geometry gate:",
                "Simulation completed successfully!",
                "Timing across all compute images:",
            )
        )
    )
    report = tmp_path / "completion.json"
    result = subprocess.run(
        [
            sys.executable,
            str(VALIDATOR),
            "--plan",
            str(plan),
            "--run-dir",
            str(tmp_path),
            "--output-file",
            str(output),
            "--restart-file",
            str(restart),
            "--model-log",
            str(model_log),
            "--output-profile",
            "wind_climatology",
            "--report",
            str(report),
        ],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(report.read_text())
    assert payload["status"] == "PASS"
    assert payload["output_profile"] == "wind_climatology"
    assert payload["output"]["ranges"]["rho_agl"] == [
        pytest.approx(1.1),
        pytest.approx(1.1),
    ]
    assert payload["output"]["ranges"]["hpbl"] == [
        pytest.approx(600.0),
        pytest.approx(600.0),
    ]
    assert "50/75/100/125/150/200 m AGL" in payload["output"]["range_scope"]


def test_model_validator_rejects_wrong_wind_height_coordinate(tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "chunk_id": "wind-bad-height",
                "start": "2020-01-01T00:00:00",
                "end": "2020-01-01T01:00:00",
                "hours": 1,
            }
        )
    )
    output = tmp_path / "output.nc"
    write_wind_output(output, [0, 1], bad_height=True)
    restart = tmp_path / "restart.nc"
    with netCDF4.Dataset(restart, "w") as dataset:
        dataset.createDimension("time", 1)
        time = dataset.createVariable("time", "f8", ("time",))
        time.units = "hours since 2020-01-01 00:00:00"
        time[:] = [1]
        dataset.dt_seconds = np.float32(3.75)
    model_log = tmp_path / "model.out"
    model_log.write_text(
        "\n".join(
            (
                "HICAR discretely adjoint wind projection enabled",
                "HICAR SLEVE geometry gate:",
                "Simulation completed successfully!",
                "Timing across all compute images:",
            )
        )
    )
    report = tmp_path / "completion.json"
    result = subprocess.run(
        [
            sys.executable,
            str(VALIDATOR),
            "--plan",
            str(plan),
            "--run-dir",
            str(tmp_path),
            "--output-file",
            str(output),
            "--restart-file",
            str(restart),
            "--model-log",
            str(model_log),
            "--output-profile",
            "wind_climatology",
            "--report",
            str(report),
        ],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 1
    assert "invalid height_agl coordinate" in result.stdout
