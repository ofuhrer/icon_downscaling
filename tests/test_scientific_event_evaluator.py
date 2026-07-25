import json
from pathlib import Path
import subprocess
import sys

import netCDF4
import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
EVALUATOR = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "validation"
    / "evaluate_scientific_event.py"
)


def write_static(path: Path) -> None:
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("y", 3)
        dataset.createDimension("x", 4)
        dataset.createVariable("landmask", "i2", ("y", "x"))[:] = 1
        landuse = dataset.createVariable("landuse", "i2", ("y", "x"))
        landuse[:] = 7
        landuse[0, 0] = 24
        terrain = dataset.createVariable("topo", "f4", ("y", "x"))
        terrain[:] = np.asarray(
            [
                [3000, 250, 750, 1250],
                [1750, 2500, 3500, 250],
                [750, 1250, 1750, 2500],
            ],
            dtype=np.float32,
        )


def write_output(
    path: Path,
    decreasing_precipitation: bool = False,
    decreasing_runoff: bool = False,
    legacy: bool = False,
) -> None:
    names = (
        "precipitation",
        "taix",
        "hus2m",
        "u10m",
        "v10m",
        "rsds",
        "lwtr",
        "rlus",
        "hfgs",
        "hfss",
        "hfls",
        "albedo",
        "canopy_water",
        "swet",
        "snow_height",
        "soil_column_total_water",
    )
    water_names = (
        ("runoff_surface", "runoff_subsurface")
        if legacy
        else (
            "runoff_surface_cumulative",
            "runoff_subsurface_cumulative",
            "evaporation_net_cumulative",
            "water_aquifer",
            "storage_gw",
            "wetland_h20_store",
        )
    )
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("time", 3)
        dataset.createDimension("soil", 4)
        dataset.createDimension("y", 3)
        dataset.createDimension("x", 4)
        time = dataset.createVariable("time", "f8", ("time",))
        time.units = "hours since 2020-07-01 00:00:00"
        time[:] = [0, 3, 6]
        for name in (*names, *water_names):
            dataset.createVariable(name, "f4", ("time", "y", "x"))
        soil_water = dataset.createVariable(
            "soil_water_content", "f4", ("time", "soil", "y", "x")
        )
        soil_column = (
            np.asarray([100.0, 102.0, 104.0], dtype=np.float32)
            if legacy
            else np.asarray([100.0, 101.0, 102.0], dtype=np.float32)
        )
        soil_water[:] = (
            soil_column / 1500.0
        )[:, None, None, None]
        dataset.createVariable(
            "soil_temperature", "f4", ("time", "soil", "y", "x")
        )[:] = 280.0

        precipitation = [0.0, 3.0, 6.0]
        if decreasing_precipitation:
            precipitation = [0.0, 3.0, 2.0]
        dataset.variables["precipitation"][:] = np.asarray(
            precipitation, dtype=np.float32
        )[:, None, None]
        for name in (
            "precipitation",
            *(
                ()
                if legacy
                else (
                    "runoff_surface_cumulative",
                    "runoff_subsurface_cumulative",
                    "evaporation_net_cumulative",
                )
            ),
        ):
            variable = dataset.variables[name]
            variable.units = "kg m-2"
            variable.accumulation_semantics = (
                "cumulative since simulation start; no output reset; "
                "restart-persistent"
            )
            variable.interval_semantics = (
                "difference consecutive records gives amount over "
                "(previous_time, time]"
            )
        dataset.variables["soil_column_total_water"][:] = soil_column[
            :, None, None
        ]
        dataset.variables["taix"][:] = 280.0
        dataset.variables["hus2m"][:] = 0.005
        dataset.variables["u10m"][:] = 3.0
        dataset.variables["v10m"][:] = 4.0
        dataset.variables["rsds"][:] = 100.0
        dataset.variables["albedo"][:] = 0.2
        dataset.variables["lwtr"][:] = 300.0
        dataset.variables["rlus"][:] = 350.0
        dataset.variables["hfss"][:] = 10.0
        dataset.variables["hfls"][:] = 0.0
        dataset.variables["hfgs"][:] = 20.0
        dataset.variables["canopy_water"][:] = 0.0
        dataset.variables["swet"][:] = 0.0
        dataset.variables["snow_height"][:] = 0.0
        if legacy:
            # A sampled 1/36 kg m-2 last-step amount at a 300 s soil
            # timestep reconstructs to 2 kg m-2 over six hours.
            dataset.variables["runoff_surface"][:] = 1.0 / 36.0
            dataset.variables["runoff_subsurface"][:] = 0.0
        else:
            surface = [0.0, 0.25, 0.5]
            if decreasing_runoff:
                surface = [0.0, 0.25, 0.1]
            dataset.variables["runoff_surface_cumulative"][:] = np.asarray(
                surface, dtype=np.float32
            )[:, None, None]
            dataset.variables["runoff_subsurface_cumulative"][:] = np.asarray(
                [0.0, 0.25, 0.5], dtype=np.float32
            )[:, None, None]
            dataset.variables["evaporation_net_cumulative"][:] = np.asarray(
                [0.0, 0.5, 1.0], dtype=np.float32
            )[:, None, None]
            dataset.variables["water_aquifer"][:] = np.asarray(
                [10.0, 11.0, 12.0], dtype=np.float32
            )[:, None, None]
            dataset.variables["storage_gw"][:] = np.asarray(
                [110.0, 112.0, 114.0], dtype=np.float32
            )[:, None, None]
            dataset.variables["wetland_h20_store"][:] = 0.0
            for name in ("water_aquifer", "storage_gw", "wetland_h20_store"):
                dataset.variables[name].units = "mm"


def run_evaluator(
    tmp_path: Path,
    decreasing_precipitation: bool = False,
    decreasing_runoff: bool = False,
    legacy: bool = False,
):
    static = tmp_path / "static.nc"
    output = tmp_path / "output.nc"
    report = tmp_path / "report.json"
    write_static(static)
    write_output(output, decreasing_precipitation, decreasing_runoff, legacy)
    command = [
            sys.executable,
            str(EVALUATOR),
            "--event-name",
            "synthetic",
            "--static-file",
            str(static),
            "--output-file",
            str(output),
            "--expected-start",
            "2020-07-01T00:00:00",
            "--expected-end",
            "2020-07-01T06:00:00",
            "--expected-interval-seconds",
            "10800",
            "--report",
            str(report),
        ]
    if legacy:
        command.extend(["--legacy-runoff-timestep-seconds", "300"])
    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
    )
    return result, json.loads(report.read_text()), report


def test_scientific_event_evaluator_closes_synthetic_surface_budgets(tmp_path):
    result, report, path = run_evaluator(tmp_path)
    assert result.returncode == 0, result.stderr + result.stdout
    assert report["status"] == "PASS"
    assert report["water_budget_contract"]["production_eligible"] is True
    assert report["water_budget_contract"]["mode"] == "production_cumulative"
    assert Path(f"{path}.ready").is_file()
    assert report["mask_contract"]["usgs_snow_ice_24_cells"] == 1
    water = report["classes"]["active_soil_all"]["water_diagnostic_kg_m2"]
    assert water["precipitation"] == 6.0
    assert water["evaporation"] == pytest.approx(1.0)
    assert water["runoff_surface"] == pytest.approx(0.5)
    assert water["runoff_subsurface"] == pytest.approx(0.5)
    assert water["resolved_storage_change"] == pytest.approx(4.0, abs=2.0e-5)
    assert water["residual"] == pytest.approx(0.0, abs=2.0e-5)
    energy = report["classes"]["active_soil_all"]["surface_energy_diagnostic"]
    assert abs(energy["mean_residual_w_m2"]) < 1.0e-5


def test_scientific_event_evaluator_rejects_decreasing_accumulation(tmp_path):
    result, report, path = run_evaluator(tmp_path, decreasing_precipitation=True)
    assert result.returncode == 1
    assert report["status"] == "FAIL"
    assert not Path(f"{path}.ready").exists()
    assert report["precipitation_decrease_cells"] > 0


def test_scientific_event_evaluator_rejects_cumulative_runoff_reset(tmp_path):
    result, report, path = run_evaluator(tmp_path, decreasing_runoff=True)
    assert result.returncode == 1
    assert report["status"] == "FAIL"
    assert not Path(f"{path}.ready").exists()
    assert report["surface_runoff_decrease_cells"] > 0


def test_legacy_reconstruction_is_explicitly_not_production_eligible(tmp_path):
    result, report, path = run_evaluator(tmp_path, legacy=True)
    assert result.returncode == 0, result.stderr + result.stdout
    assert Path(f"{path}.ready").is_file()
    contract = report["water_budget_contract"]
    assert contract["mode"] == "legacy_snapshot_reconstruction"
    assert contract["production_eligible"] is False
    assert contract["representativeness_limited"] is True
    water = report["classes"]["active_soil_all"]["water_diagnostic_kg_m2"]
    assert water["runoff"] == pytest.approx(2.0, abs=2.0e-6)
    assert water["residual"] == pytest.approx(0.0, abs=2.0e-5)
