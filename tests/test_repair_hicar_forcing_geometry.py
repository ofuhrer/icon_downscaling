from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import netCDF4
import numpy as np

from preprocessing.hicarprep.products import sha256


ROOT = Path(__file__).resolve().parents[1]
REPAIR = ROOT / "scripts/repair_hicar_forcing_geometry.py"


def test_repair_replaces_only_geometry_and_rebinds_boundary(tmp_path: Path) -> None:
    static = tmp_path / "static.nc"
    forcing = tmp_path / "forcing.nc"
    boundary = tmp_path / "forcing.lbc.nc"
    lat = np.array([[46.0, 46.1], [46.2, 46.3]])
    lon = np.array([[7.0, 7.1], [7.0, 7.1]])
    hhl = np.array([0.0, 100.0, 300.0])[:, None, None] * np.ones((1, 2, 2))
    hfl = np.array([47.0, 188.0])[:, None, None] * np.ones((1, 2, 2))
    with netCDF4.Dataset(static, "w") as dataset:
        for name, size in (("y", 2), ("x", 2), ("level", 2), ("half_level", 3)):
            dataset.createDimension(name, size)
        dataset.createVariable("lat", "f8", ("y", "x"))[:] = lat
        dataset.createVariable("lon", "f8", ("y", "x"))[:] = lon
        dataset.createVariable("HHL", "f8", ("half_level", "y", "x"))[:] = hhl
        dataset.createVariable("HFL", "f8", ("level", "y", "x"))[:] = hfl
        dataset.createVariable("landmask", "i1", ("y", "x"))[:] = np.array(
            [[1, 0], [1, 1]]
        )
    static_sha = sha256(static)

    payload = np.arange(8, dtype=np.float32).reshape(1, 2, 2, 2)
    with netCDF4.Dataset(forcing, "w") as dataset:
        for name, size in (
            ("time", 1), ("z", 2), ("z_hl", 3), ("y_1", 2), ("x_1", 2),
        ):
            dataset.createDimension(name, size)
        dataset.product_type = "hicarprep_target_forcing_record"
        dataset.water_representation = "dry-air mixing ratio"
        dataset.static_sha256 = static_sha
        dataset.createVariable("lat_1", "f8", ("y_1", "x_1"))[:] = lat
        dataset.createVariable("lon_1", "f8", ("y_1", "x_1"))[:] = lon
        time = dataset.createVariable("time", "f8", ("time",))
        time.units = "hours since 2020-01-01 00:00:00"
        time[:] = 1
        for name, values in (
            ("P", np.full_like(payload, 80_000.0)),
            ("T", np.full_like(payload, 270.0)),
            ("QV", np.full_like(payload, 0.004)),
            ("QC", np.zeros_like(payload)),
            ("QI", np.zeros_like(payload)),
            ("U", payload),
            ("V", -payload),
            ("W", np.full_like(payload, 0.2)),
        ):
            dataset.createVariable(name, "f4", ("time", "z", "y_1", "x_1"))[:] = values
        dataset.createVariable("HHL", "f4", ("z_hl", "y_1", "x_1"))[:] = hhl
        dataset.createVariable("HFL", "f4", ("z", "y_1", "x_1"))[:] = 0.5 * (
            hhl[:-1] + hhl[1:]
        )
        dataset.createVariable("HSURF", "f4", ("y_1", "x_1"))[:] = 0.0
        dataset.createVariable("FR_LAND", "f4", ("y_1", "x_1"))[:] = np.array(
            [[1.0, 0.0], [1.0, 1.0]]
        )
        sst = dataset.createVariable("SST", "f4", ("time", "y_1", "x_1"))
        sst[:] = 277.0
        sst.units = "K"
        dataset.createVariable(
            "SST_global_fallback_mask", "i1", ("y_1", "x_1")
        )[:] = 0
        dataset.createVariable(
            "SST_global_fallback_distance_km", "f8", ("y_1", "x_1")
        )[:] = np.nan
        dataset.sst_source_sha256 = "synthetic-target-sst"
        dataset.sst_target_product_sha256 = "synthetic-target-sst"
        dataset.sst_valid_time = "2020-01-01T01:00:00Z"
        dataset.sst_source_variable = "SKT"
        dataset.sst_native_source_sha256 = "synthetic-native-sst"
        dataset.sst_remap_policy = "same-surface water support; RBF baseline on land"
        dataset.sst_water_cell_count = 1
        dataset.sst_water_local_fallback_count = 0
        dataset.sst_water_global_fallback_count = 0
        dataset.sst_maximum_fallback_distance_km = 0.0
        dataset.sst_maximum_global_fallback_distance_km = 0.0
        dataset.target_w_vertical_coordinate = "authoritative_static_HFL"
        dataset.target_w_terrain_wind_basis = "HICAR_grid_relative"
    original_payload = sha256(forcing)

    with netCDF4.Dataset(boundary, "w") as dataset:
        for name, size in (
            ("boundary_point", 2), ("level", 2), ("half_level", 3),
        ):
            dataset.createDimension(name, size)
        dataset.createVariable("row", "i4", ("boundary_point",))[:] = [0, 1]
        dataset.createVariable("column", "i4", ("boundary_point",))[:] = [0, 1]
        dataset.createVariable("relaxation_weight", "f8", ("boundary_point",))[:] = 1.0
        for name in ("T", "P", "QV", "QC", "QI", "HFL"):
            dataset.createVariable(name, "f8", ("level", "boundary_point"))[:] = 1.0
        dataset.createVariable("HHL", "f8", ("half_level", "boundary_point"))[:] = 1.0
        dataset.product_type = "hicar_lateral_boundary_state"
        dataset.valid_time = "2020-01-01T01:00:00Z"
        dataset.domain_nx = 2
        dataset.domain_ny = 2
        dataset.hicar_water_conversion = "APPLIED_JOINT_ALL_WATER_SPECIES"
        dataset.lateral_w_policy = "regular_forcing_initial_guess_then_hicar_projection"
        dataset.target_grid_fingerprint = "target"
        dataset.static_sha256 = static_sha
        dataset.relaxation_profile = "cosine_squared"
        dataset.relaxation_update = "stable"
        dataset.relaxation_timescale_seconds = 3600.0
        dataset.initial_condition_sha256 = original_payload
    Path(f"{forcing}.ready").touch()
    Path(f"{boundary}.ready").touch()

    result = subprocess.run(
        [
            sys.executable, str(REPAIR), "--forcing-file", str(forcing),
            "--boundary-file", str(boundary), "--static-file", str(static),
        ],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    with netCDF4.Dataset(forcing) as repaired, netCDF4.Dataset(boundary) as repaired_boundary:
        expected_hfl = hfl.astype(np.float32)
        expected_hfl[-1] = np.nextafter(expected_hfl[-1], np.float32(np.inf))
        np.testing.assert_array_equal(repaired["HFL"][:], expected_hfl)
        np.testing.assert_array_equal(repaired["U"][:], payload)
        np.testing.assert_array_equal(repaired_boundary["HFL"][:], hfl[:, [0, 1], [0, 1]])
        assert repaired_boundary.initial_condition_sha256 == sha256(forcing)
    assert Path(f"{forcing}.ready").exists()
    assert Path(f"{boundary}.ready").exists()

    marker_time_ns = 1_600_000_000_000_000_000
    os.utime(Path(f"{forcing}.ready"), ns=(marker_time_ns, marker_time_ns))
    os.utime(Path(f"{boundary}.ready"), ns=(marker_time_ns, marker_time_ns))
    repeated = subprocess.run(
        [
            sys.executable, str(REPAIR), "--forcing-file", str(forcing),
            "--boundary-file", str(boundary), "--static-file", str(static),
        ],
        text=True,
        capture_output=True,
    )
    assert repeated.returncode == 0, repeated.stderr
    assert Path(f"{forcing}.ready").exists()
    assert Path(f"{boundary}.ready").exists()
    assert Path(f"{forcing}.ready").stat().st_mtime_ns == marker_time_ns
    assert Path(f"{boundary}.ready").stat().st_mtime_ns == marker_time_ns
