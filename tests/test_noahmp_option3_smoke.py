import importlib.util
from pathlib import Path

import netCDF4
import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "validation"
    / "assess_noahmp_option3_smoke.py"
)
SPEC = importlib.util.spec_from_file_location("assess_noahmp_option3_smoke", SCRIPT)
ASSESSOR = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(ASSESSOR)


def _surface(dataset: netCDF4.Dataset, name: str, values: np.ndarray) -> None:
    variable = dataset.createVariable(name, "f4", ("time", "y", "x"))
    variable[0] = values.astype(np.float32)


def _build_fixture(tmp_path: Path) -> tuple[Path, Path]:
    restart_path = tmp_path / "restart.nc"
    static_path = tmp_path / "static.nc"
    ny = nx = 16
    y, x = np.mgrid[:ny, :nx]
    varying = (x + 2.0 * y) / 1000.0

    land = np.ones((ny, nx), dtype=np.float32)
    vegetation_type = np.ones((ny, nx), dtype=np.int32)
    land[4, 4] = 0
    vegetation_type[4, 5] = 15
    with netCDF4.Dataset(static_path, "w") as dataset:
        dataset.createDimension("y", ny)
        dataset.createDimension("x", nx)
        dataset.createVariable("landmask", "f4", ("y", "x"))[:] = land
        dataset.createVariable("landuse", "i4", ("y", "x"))[:] = vegetation_type

    temperature = 278.0 + varying
    mixing_ratio = 0.004 + varying / 100.0
    pressure_interfaces = np.stack(
        (92_000.0 + varying, 91_800.0 + varying, 91_600.0 + varying)
    )
    pressure = 0.5 * (pressure_interfaces[0] + pressure_interfaces[1])
    specific_humidity = mixing_ratio / (1.0 + mixing_ratio)
    vapor_pressure = specific_humidity * pressure / (0.622 + 0.378 * specific_humidity)
    density = (pressure - 0.378 * vapor_pressure) / (
        ASSESSOR.GAS_CONSTANT_DRY_AIR * temperature
    )

    vegetation_fraction = 0.35 + varying
    ch_veg_2m = 0.018 + varying / 10.0
    ch_bare_2m = 0.021 + varying / 10.0
    canopy_temperature = 281.0 + varying
    ground_temperature_bare = 279.0 + varying
    sensible_heat_veg = 22.0 + varying
    sensible_heat_canopy = 14.0 + varying
    sensible_heat_bare = 31.0 + varying
    temperature_2m_veg = canopy_temperature - (
        sensible_heat_veg + sensible_heat_canopy
    ) / (density * ASSESSOR.HEAT_CAPACITY_DRY_AIR * ch_veg_2m)
    temperature_2m_bare = ground_temperature_bare - sensible_heat_bare / (
        density * ASSESSOR.HEAT_CAPACITY_DRY_AIR * ch_bare_2m
    )

    fields = {
        "coeff_momentum_drag": 0.006 + varying / 100.0,
        "coeff_heat_exchange": 0.020 + varying / 10.0,
        "ch_veg": 0.024 + varying / 10.0,
        "ch_veg_2m": ch_veg_2m,
        "ch_bare": 0.027 + varying / 10.0,
        "ch_bare_2m": ch_bare_2m,
        "temperature_2m_veg": temperature_2m_veg,
        "temperature_2m_bare": temperature_2m_bare,
        "mixing_ratio_2m_veg": 0.0045 + varying / 100.0,
        "mixing_ratio_2m_bare": 0.0042 + varying / 100.0,
        "hpbl": 800.0 + varying * 100.0,
        "vegetation_fraction_out": vegetation_fraction,
        "snow_height": np.zeros((ny, nx)),
        "canopy_temperature": canopy_temperature,
        "ground_temperature_bare": ground_temperature_bare,
        "sensible_heat_veg": sensible_heat_veg,
        "sensible_heat_canopy": sensible_heat_canopy,
        "sensible_heat_bare": sensible_heat_bare,
    }
    fields["snow_height"][4, 6] = 0.1

    with netCDF4.Dataset(restart_path, "w") as dataset:
        dataset.createDimension("time", 1)
        dataset.createDimension("level", 2)
        dataset.createDimension("level_i", 3)
        dataset.createDimension("y", ny)
        dataset.createDimension("x", nx)
        setattr(dataset, "physics.lsm", "noahmp")
        setattr(dataset, "lsm.nmp_opt_sfc", "3")
        setattr(dataset, "sfc.iz0tlnd", "1")
        setattr(dataset, "lsm.ice_category", "15")
        for name, values in fields.items():
            _surface(dataset, name, values)

        temperature_variable = dataset.createVariable(
            "temperature", "f4", ("time", "level", "y", "x")
        )
        temperature_variable[0, 0] = temperature.astype(np.float32)
        temperature_variable[0, 1] = (temperature - 1.0).astype(np.float32)
        qv_variable = dataset.createVariable("qv", "f4", ("time", "level", "y", "x"))
        qv_variable[0, 0] = mixing_ratio.astype(np.float32)
        qv_variable[0, 1] = mixing_ratio.astype(np.float32)
        pressure_variable = dataset.createVariable(
            "pressure", "f4", ("time", "level_i", "y", "x")
        )
        pressure_variable[0] = pressure_interfaces.astype(np.float32)

    # Invalid guard values and excluded core points must not influence acceptance.
    with netCDF4.Dataset(restart_path, "a") as dataset:
        for name in fields:
            values = dataset[name][0]
            values[:3] = np.nan
            values[-3:] = np.nan
            values[:, :3] = np.nan
            values[:, -3:] = np.nan
            if name not in {"vegetation_fraction_out", "snow_height"}:
                values[4, 4:7] = np.nan
            dataset[name][0] = values

    return restart_path, static_path


def test_accepts_active_option3_fields_and_reports_identity_samples(tmp_path: Path) -> None:
    restart, static = _build_fixture(tmp_path)

    summary = ASSESSOR.assess_option3_smoke(
        restart, static, identity_tolerance=1.0e-3, minimum_samples=20
    )

    assert summary["status"] == "accepted"
    assert summary["guard_cells_removed"] == 3
    assert summary["temperature_identities"]["vegetated"]["samples"] >= 20
    assert summary["temperature_identities"]["bare"]["samples"] >= 20
    assert summary["temperature_identities"]["bare"]["max_abs_residual_K"] < 1.0e-3


def test_rejects_namelist_acceptance_without_option3_selection(tmp_path: Path) -> None:
    restart, static = _build_fixture(tmp_path)
    with netCDF4.Dataset(restart, "a") as dataset:
        setattr(dataset, "lsm.nmp_opt_sfc", "1")

    with pytest.raises(ASSESSOR.AssessmentError, match="attribute mismatch"):
        ASSESSOR.assess_option3_smoke(restart, static, minimum_samples=20)


def test_rejects_broken_bare_temperature_identity(tmp_path: Path) -> None:
    restart, static = _build_fixture(tmp_path)
    with netCDF4.Dataset(restart, "a") as dataset:
        values = dataset["temperature_2m_bare"][:]
        values[:, 3:-3, 3:-3] += 0.02
        dataset["temperature_2m_bare"][:] = values

    with pytest.raises(ASSESSOR.AssessmentError, match="bare option-3 T2 identity"):
        ASSESSOR.assess_option3_smoke(
            restart, static, identity_tolerance=1.0e-3, minimum_samples=20
        )


def test_rejects_constant_exchange_field(tmp_path: Path) -> None:
    restart, static = _build_fixture(tmp_path)
    with netCDF4.Dataset(restart, "a") as dataset:
        values = dataset["coeff_heat_exchange"][:]
        values[:, 3:-3, 3:-3] = 0.02
        dataset["coeff_heat_exchange"][:] = values

    with pytest.raises(ASSESSOR.AssessmentError, match="numerically constant"):
        ASSESSOR.assess_option3_smoke(restart, static, minimum_samples=20)
