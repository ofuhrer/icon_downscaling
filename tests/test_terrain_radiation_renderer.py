from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import netCDF4
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "case_studies" / "swiss_200m" / "scripts" / "render_hicar_namelist.py"


def _forcing(path: Path, half_hour: int) -> None:
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("time", 1)
        time = dataset.createVariable("time", "f8", ("time",))
        time.units = "minutes since 2020-07-20 06:30:00"
        time[:] = 30 * half_hour
    Path(f"{path}.ready").touch()


def _static(path: Path, audited: bool) -> None:
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("azimuth", 90)
        dataset.createDimension("y", 2)
        dataset.createDimension("x", 2)
        dataset.createVariable("hlm", "f4", ("azimuth", "y", "x"))[:] = 90.0
        dataset.createVariable("svf", "f4", ("y", "x"))[:] = 1.0
        dataset.createVariable("slope_angle", "f4", ("y", "x"))[:] = 0.0
        dataset.createVariable("aspect_angle", "f4", ("y", "x"))[:] = 0.0
        if audited:
            dataset.terrain_radiation_geometry_sha256 = "a" * 64
            dataset.terrain_radiation_horizon_convention = "hlm_zenith_angle_degrees_flat_90"
            dataset.terrain_radiation_search_distance_km = 20.0
    Path(f"{path}.ready").touch()


def _render(tmp_path: Path, static: Path) -> subprocess.CompletedProcess[str]:
    forcing = []
    for half_hour in range(7):
        path = tmp_path / f"forcing_{half_hour}.nc"
        _forcing(path, half_hour)
        forcing.append(path)
    forcing_list = tmp_path / "forcing.txt"
    forcing_list.write_text("".join(f'"{path}"\n' for path in forcing))
    Path(f"{forcing_list}.ready").touch()
    return subprocess.run(
        [
            sys.executable,
            str(RENDERER),
            "--static-file", str(static),
            "--forcing-file-list", str(forcing_list),
            "--forcing-interval", "1800",
            "--radiation-update-interval", "300",
            "--start-date", "2020-07-20 06:30:00",
            "--end-date", "2020-07-20 09:30:00",
            "--output-interval", "300",
            "--output-profile", "terrain_radiation_gate",
            "--terrain-radiation-profile", "direct-diffuse",
            "--output-dir", str(tmp_path / "output"),
            "--restart-dir", str(tmp_path / "restart"),
            "--output", str(tmp_path / "input.nml"),
        ],
        text=True,
        capture_output=True,
    )


def test_renderer_exposes_audited_direct_diffuse_profile(tmp_path: Path) -> None:
    static = tmp_path / "static.nc"
    _static(static, audited=True)
    result = _render(tmp_path, static)
    assert result.returncode == 0, result.stderr
    rendered = (tmp_path / "input.nml").read_text()
    assert "inputinterval = 1800" in rendered
    assert "update_interval_rad = 300.0" in rendered
    assert "terrain_shading = .True." in rendered
    assert "terrain_direct_sw = .True." in rendered
    assert "terrain_diffuse_sw = .True." in rendered
    assert "terrain_reflected_sw = .False." in rendered
    assert "terrain_longwave = .False." in rendered
    assert "hlm_var = 'hlm'" in rendered
    assert "'shortwave_direct_horizontal'" in rendered


def test_renderer_rejects_unaudited_terrain_geometry(tmp_path: Path) -> None:
    static = tmp_path / "static.nc"
    _static(static, audited=False)
    result = _render(tmp_path, static)
    assert result.returncode != 0
    assert "terrain-radiation static lacks audited attributes" in result.stderr
