from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import netCDF4
import numpy as np

from preprocessing.hicarprep.products import sha256


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "case_studies/swiss_200m/validation/validate_forcing.py"


def files(root: Path, invalid_hfl: bool = False) -> tuple[Path, Path, Path]:
    static = root / "static.nc"
    forcing = root / "forcing.nc"
    boundary = root / "forcing.lbc.nc"
    lat = np.array([[46.0, 46.1], [46.2, 46.3]])
    lon = np.array([[7.0, 7.1], [7.0, 7.1]])
    with netCDF4.Dataset(static, "w") as dataset:
        dataset.createDimension("y", 2); dataset.createDimension("x", 2)
        dataset.createDimension("level", 2); dataset.createDimension("half_level", 3)
        dataset.createVariable("lat", "f8", ("y", "x"))[:] = lat
        dataset.createVariable("lon", "f8", ("y", "x"))[:] = lon
        hhl = np.array([0.0, 100.0, 300.0])[:, None, None] * np.ones((1, 2, 2))
        hfl = np.array([50.0, 200.0])[:, None, None] * np.ones((1, 2, 2))
        dataset.createVariable("HHL", "f8", ("half_level", "y", "x"))[:] = hhl
        dataset.createVariable("HFL", "f8", ("level", "y", "x"))[:] = hfl
    with netCDF4.Dataset(forcing, "w") as dataset:
        for name, size in (("time", 1), ("z", 2), ("z_hl", 3), ("y_1", 2), ("x_1", 2)):
            dataset.createDimension(name, size)
        dataset.product_type = "hicarprep_target_forcing_record"
        dataset.water_representation = "dry-air mixing ratio"
        dataset.static_sha256 = sha256(static)
        dataset.createVariable("lat_1", "f8", ("y_1", "x_1"))[:] = lat
        dataset.createVariable("lon_1", "f8", ("y_1", "x_1"))[:] = lon
        time = dataset.createVariable("time", "f8", ("time",))
        time.units = "hours since 2020-01-01 00:00:00"; time[:] = 1
        for name, value in (("P", 80000.0), ("T", 270.0), ("QV", .004),
                            ("QC", 0.0), ("QI", 0.0), ("U", 5.0), ("V", -2.0)):
            dataset.createVariable(name, "f4", ("time", "z", "y_1", "x_1"))[:] = value
        hhl = np.array([0.0, 100.0, 300.0])[:, None, None] * np.ones((1, 2, 2))
        hfl = np.array([50.0, 200.0 if not invalid_hfl else 210.0])[:, None, None] * np.ones((1, 2, 2))
        dataset.createVariable("HHL", "f4", ("z_hl", "y_1", "x_1"))[:] = hhl
        dataset.createVariable("HFL", "f4", ("z", "y_1", "x_1"))[:] = hfl
        dataset.createVariable("HSURF", "f4", ("y_1", "x_1"))[:] = 0.0
        dataset.createVariable("FR_LAND", "f4", ("y_1", "x_1"))[:] = 1.0
    with netCDF4.Dataset(boundary, "w") as dataset:
        for name, size in (
            ("boundary_point", 2), ("u_boundary_point", 2),
            ("v_boundary_point", 2), ("level", 2), ("half_level", 3),
        ):
            dataset.createDimension(name, size)
        for prefix, rows, columns in (
            ("", [0, 1], [0, 1]), ("u_", [0, 1], [0, 2]),
            ("v_", [0, 2], [0, 1]),
        ):
            dimension = f"{prefix}boundary_point" if prefix else "boundary_point"
            dataset.createVariable(f"{prefix}row", "i4", (dimension,))[:] = rows
            dataset.createVariable(f"{prefix}column", "i4", (dimension,))[:] = columns
            dataset.createVariable(f"{prefix}relaxation_weight", "f8", (dimension,))[:] = 1.0
        for name in ("T", "P", "QV", "QC", "QI", "HFL"):
            dataset.createVariable(name, "f8", ("level", "boundary_point"))[:] = 1.0
        dataset.createVariable("HHL", "f8", ("half_level", "boundary_point"))[:] = 1.0
        dataset.createVariable("U", "f8", ("level", "u_boundary_point"))[:] = 1.0
        dataset.createVariable("V", "f8", ("level", "v_boundary_point"))[:] = 1.0
        dataset.product_type = "hicar_lateral_boundary_state"
        dataset.valid_time = "2020-01-01T01:00:00Z"
        dataset.domain_nx = 2
        dataset.domain_ny = 2
        dataset.hicar_water_conversion = "APPLIED_JOINT_ALL_WATER_SPECIES"
        dataset.lateral_w_policy = "diagnose_in_hicar"
        dataset.target_grid_fingerprint = "target"
        dataset.static_sha256 = "static"
        dataset.relaxation_profile = "cosine_squared"
        dataset.relaxation_update = "stable"
        dataset.relaxation_timescale_seconds = 3600.0
        dataset.initial_condition_sha256 = sha256(forcing)
        dataset.static_sha256 = sha256(static)
    return forcing, static, boundary


def test_valid_hicarprep_record_passes(tmp_path: Path) -> None:
    forcing, static, boundary = files(tmp_path)
    result = subprocess.run(
        [sys.executable, str(VALIDATOR), "--forcing-file", str(forcing),
         "--boundary-file", str(boundary),
         "--static-file", str(static), "--expected-valid-time", "2020-01-01T01:00:00"],
        text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("PASS")


def test_inconsistent_height_fails(tmp_path: Path) -> None:
    forcing, static, boundary = files(tmp_path, invalid_hfl=True)
    result = subprocess.run(
        [sys.executable, str(VALIDATOR), "--forcing-file", str(forcing),
         "--boundary-file", str(boundary), "--static-file", str(static)],
        text=True, capture_output=True,
    )
    assert result.returncode != 0
    assert "forcing HHL/HFL differ" in result.stderr


def test_mismatched_boundary_pair_fails(tmp_path: Path) -> None:
    forcing, static, boundary = files(tmp_path)
    with netCDF4.Dataset(boundary, "a") as dataset:
        dataset.initial_condition_sha256 = "not-the-forcing-record"
    result = subprocess.run(
        [sys.executable, str(VALIDATOR), "--forcing-file", str(forcing),
         "--boundary-file", str(boundary), "--static-file", str(static)],
        text=True, capture_output=True,
    )
    assert result.returncode != 0
    assert "does not belong to the supplied forcing record" in result.stderr
