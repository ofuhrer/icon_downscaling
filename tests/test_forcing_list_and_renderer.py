from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import netCDF4
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "case_studies/swiss_200m/scripts/render_hicar_namelist.py"


def static_file(path: Path) -> None:
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("soil_layer", 4)
        dataset.createDimension("level", 80)
        dataset.createDimension("half_level", 81)
        dataset.createDimension("y", 1)
        dataset.createDimension("x", 1)
        for name in ("lat", "lon", "topo", "landmask", "landuse", "swe", "snow_height"):
            dataset.createVariable(name, "f4", ("y", "x"))[:] = 0.0
        dataset.createVariable("soil_type_layer", "i2", ("soil_layer", "y", "x"))[:] = 6
        hhl = np.linspace(0.0, 12_000.0, 81)
        dataset.createVariable("HHL", "f4", ("half_level", "y", "x"))[:] = hhl[:, None, None]
        dataset.createVariable("HFL", "f4", ("level", "y", "x"))[:] = (
            0.5 * (hhl[:-1] + hhl[1:])
        )[:, None, None]
        dataset.sleve_nz = 80
        dataset.sleve_model_top_m = 12_000.0
        dataset.sleve_lowest_layer_m = 26.0
        dataset.sleve_stretch_factor = 0.65
        dataset.required_minimum_sleve_layer_thickness_m = 20.0


def input_pair(root: Path, hour: int) -> tuple[Path, Path]:
    forcing = root / f"forcing_{hour}.nc"
    boundary = root / f"forcing_{hour}.lbc.nc"
    with netCDF4.Dataset(forcing, "w") as dataset:
        dataset.createDimension("time", 1)
        variable = dataset.createVariable("time", "f8", ("time",))
        variable.units = "hours since 2020-01-01 00:00:00"
        variable[:] = hour
        dataset.product_type = "hicarprep_target_forcing_record"
        dataset.water_representation = "dry-air mixing ratio"
    with netCDF4.Dataset(boundary, "w") as dataset:
        dataset.product_type = "hicar_lateral_boundary_state"
        dataset.valid_time = f"2020-01-01T{hour:02d}:00:00Z"
    Path(f"{forcing}.ready").touch()
    Path(f"{boundary}.ready").touch()
    return forcing, boundary


def test_renderer_has_one_explicit_hicarprep_configuration(tmp_path: Path) -> None:
    static = tmp_path / "static.nc"
    static_file(static)
    pairs = [input_pair(tmp_path, hour) for hour in range(3)]
    forcing_list = tmp_path / "forcing.txt"
    boundary_list = tmp_path / "lbc.txt"
    forcing_list.write_text("".join(f'"{forcing}"\n' for forcing, _ in pairs))
    boundary_list.write_text("".join(f'"{boundary}"\n' for _, boundary in pairs))
    namelist = tmp_path / "input.nml"
    result = subprocess.run(
        [
            sys.executable, str(RENDERER), "--static-file", str(static),
            "--forcing-file-list", str(forcing_list),
            "--sparse-lbc-file-list", str(boundary_list),
            "--start-date", "2020-01-01 00:00:00", "--end-date", "2020-01-01 02:00:00",
            "--output-profile", "debug", "--output-dir", str(tmp_path / "output"),
            "--restart-dir", str(tmp_path / "restart"), "--output", str(namelist),
        ],
        text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    text = namelist.read_text()
    for setting in (
        "debug = .False.", "qcvar = 'QC'", "qivar = 'QI'", "wvar = ''",
        "qv_is_spec_humidity = .False.", "relax_filters = .False.",
        "soiltexture_var = 'soil_type_layer'", "nmp_opt_soil = 2",
        "Sx = .True.", "advect_density = .True.", "alpha_const = 1.0",
        "terrain_shading = .False.", "height_lowest_level = 26.0",
    ):
        assert setting in text
    assert str(boundary_list.resolve()) in text


def test_renderer_rejects_static_geometry_that_does_not_match_the_namelist(
    tmp_path: Path,
) -> None:
    static = tmp_path / "static.nc"
    static_file(static)
    with netCDF4.Dataset(static, "a") as dataset:
        dataset.sleve_lowest_layer_m = 15.0
    pairs = [input_pair(tmp_path, hour) for hour in range(2)]
    forcing_list = tmp_path / "forcing.txt"
    boundary_list = tmp_path / "lbc.txt"
    forcing_list.write_text("".join(f'"{forcing}"\n' for forcing, _ in pairs))
    boundary_list.write_text("".join(f'"{boundary}"\n' for _, boundary in pairs))
    result = subprocess.run(
        [
            sys.executable,
            str(RENDERER),
            "--static-file",
            str(static),
            "--forcing-file-list",
            str(forcing_list),
            "--sparse-lbc-file-list",
            str(boundary_list),
            "--start-date",
            "2020-01-01 00:00:00",
            "--end-date",
            "2020-01-01 01:00:00",
            "--output-dir",
            str(tmp_path / "out"),
            "--restart-dir",
            str(tmp_path / "restart"),
            "--output",
            str(tmp_path / "input.nml"),
        ],
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert "SLEVE settings do not match" in result.stderr


def test_renderer_rejects_mismatched_lbc_times(tmp_path: Path) -> None:
    static = tmp_path / "static.nc"
    static_file(static)
    pairs = [input_pair(tmp_path, hour) for hour in range(2)]
    with netCDF4.Dataset(pairs[1][1], "a") as dataset:
        dataset.valid_time = "2020-01-01T03:00:00Z"
    forcing_list = tmp_path / "forcing.txt"
    boundary_list = tmp_path / "lbc.txt"
    forcing_list.write_text("".join(f'"{forcing}"\n' for forcing, _ in pairs))
    boundary_list.write_text("".join(f'"{boundary}"\n' for _, boundary in pairs))
    result = subprocess.run(
        [sys.executable, str(RENDERER), "--static-file", str(static),
         "--forcing-file-list", str(forcing_list), "--sparse-lbc-file-list", str(boundary_list),
         "--start-date", "2020-01-01 00:00:00", "--end-date", "2020-01-01 01:00:00",
         "--output-dir", str(tmp_path / "out"), "--restart-dir", str(tmp_path / "restart"),
         "--output", str(tmp_path / "input.nml")],
        text=True, capture_output=True,
    )
    assert result.returncode != 0
    assert "same two or more times" in result.stderr
