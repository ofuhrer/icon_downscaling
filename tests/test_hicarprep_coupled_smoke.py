from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import netCDF4
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "assess_hicarprep_coupled_smoke",
    ROOT / "case_studies/swiss_200m/validation/assess_hicarprep_coupled_smoke.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_coupled_runner_rejects_stale_depth_varying_soil_binary() -> None:
    script = (
        ROOT
        / "case_studies/swiss_200m/scripts/run_hicarprep_coupled_smoke_balfrin.sbatch"
    ).read_text()
    assert '"$exe" -v soiltexture_var' in script
    assert "not a valid namelist variable" in script
    assert "ERROR reading 'domain' namelist" in script


def write_run(path: Path, soil_water: float, method: str, case_date: str) -> None:
    (path / "output").mkdir(parents=True)
    executable = path / "HICAR_gpu"
    executable.write_bytes(b"same qualification executable")
    executable_hash = hashlib.sha256(executable.read_bytes()).hexdigest()
    (path / "executable.sha256").write_text(f"{executable_hash}  {executable}\n")
    (path / "hicar_build_provenance.txt").write_text(
        "source_commit=0123456789abcdef\nvariant=gpu-nccl\n"
    )
    runtime = path / "runtime.nc"
    with netCDF4.Dataset(runtime, "w") as dataset:
        dataset.createDimension("soil", 4)
        dataset.createDimension("y", 2)
        dataset.createDimension("x", 2)
        dataset.createVariable("soil_type_layer", "i2", ("soil", "y", "x"))[:] = 6
        dataset.createVariable("landmask", "i2", ("y", "x"))[:] = 1
        dataset.createVariable("landuse", "i2", ("y", "x"))[:] = 7
        dataset.land_state_soil_water_method = method
        dataset.land_state_valid_time = (
            f"{case_date[:4]}-{case_date[4:6]}-{case_date[6:]}T00:00:00Z"
        )
        dataset.land_state_static_epoch_back_extrapolation = "explicit_research_override"
    runtime_hash = hashlib.sha256(runtime.read_bytes()).hexdigest()
    (path / "runtime_domain.sha256").write_text(f"{runtime_hash}  {runtime}\n")
    forcing_lines = []
    for hour in ("0000", "0100"):
        forcing = path.parent / f"forcing_{case_date}_{hour}.nc"
        forcing.write_bytes(f"forcing {case_date} {hour}".encode())
        forcing_lines.append(
            f"{hashlib.sha256(forcing.read_bytes()).hexdigest()}  {forcing}\n"
        )
    (path / "forcing.sha256").write_text("".join(forcing_lines))
    (path / "input.nml").write_text(f"&domain\n init_conditions_file = '{runtime}'\n/\n")
    (path / "soiltexture_var_query.txt").write_text(
        "Namelist Variable: soiltexture_var\n"
    )
    log = path / "model.out"
    log.write_text(
        "Git commit: 0123456789ab\n"
        "Reading Land Variables\n"
        "Read surface temperature field from: surface_temperature\n"
        "Simulation completed successfully!\n"
    )
    Path(f"{log}.ready").touch()
    for filename, seconds in (("initial.nc", 0.0), ("state.nc", 600.0)):
        with netCDF4.Dataset(path / f"output/{filename}", "w") as dataset:
            dataset.createDimension("time", 1)
            dataset.createDimension("y", 2)
            dataset.createDimension("x", 2)
            dataset.createDimension("soil", 4)
            time = dataset.createVariable("time", "f8", ("time",))
            time[:] = seconds
            time.units = "seconds since 2020-01-01 00:00:00"
            time.calendar = "proleptic_gregorian"
            for name, value, dimensions in (
                ("soil_water_content", soil_water, ("soil", "y", "x")),
                ("soil_temperature", 280.0, ("soil", "y", "x")),
                ("tsfe", 281.0, ("y", "x")),
                ("snow_height", 0.1, ("y", "x")),
                ("soil_column_total_water", 300.0, ("y", "x")),
                ("hfss", 10.0, ("y", "x")),
                ("hfls", 20.0, ("y", "x")),
            ):
                variable = dataset.createVariable(name, "f8", ("time", *dimensions))
                variable[:] = value


def test_coupled_smoke_assessment_requires_distinct_plausible_arms(tmp_path: Path) -> None:
    payloads = {}
    arrays = {}
    for date in ("20200115", "20200702"):
        for method, value in (("smi", 0.3), ("relative_saturation", 0.25)):
            run = tmp_path / f"{date}_{method}"
            write_run(run, value, method, date)
            payloads[(date, method)], arrays[(date, method)] = MODULE.read_run(
                date, method, run
            )
    assert payloads[("20200115", "smi")]["diagnostics"]["soil_water_content"]["p50"] == 0.3
    difference = arrays[("20200702", "smi")]["soil_water_content"] - arrays[
        ("20200702", "relative_saturation")
    ]["soil_water_content"]
    assert np.isclose(
        MODULE.quantiles(np.ma.masked_invalid(difference))["p50"], 0.05
    )
