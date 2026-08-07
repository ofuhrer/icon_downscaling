from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import netCDF4


ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "case_studies" / "swiss_200m" / "scripts" / "render_hicar_namelist.py"


def _forcing(path: Path, hour: int) -> None:
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("time", 1)
        time = dataset.createVariable("time", "f8", ("time",))
        time.units = "hours since 2020-01-01 00:00:00"
        time[:] = hour
    Path(f"{path}.ready").touch()


def _render(tmp_path: Path, static: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    forcing = []
    for hour in range(3):
        path = tmp_path / f"forcing_{hour}.nc"
        _forcing(path, hour)
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
            "--start-date", "2020-01-01 00:00:00",
            "--end-date", "2020-01-01 02:00:00",
            "--output-profile", "engineering",
            "--output-dir", str(tmp_path / "output"),
            "--restart-dir", str(tmp_path / "restart"),
            "--output", str(tmp_path / "input.nml"),
            *extra,
        ],
        text=True,
        capture_output=True,
    )


def test_renderer_selects_four_layer_noahmp_texture(tmp_path: Path) -> None:
    static = tmp_path / "static.nc"
    with netCDF4.Dataset(static, "w") as dataset:
        dataset.createDimension("soil_layer", 4)
        dataset.createDimension("y", 1)
        dataset.createDimension("x", 1)
        dataset.createVariable("soil_type_layer", "i2", ("soil_layer", "y", "x"))[:] = 6
    Path(f"{static}.ready").touch()

    result = _render(tmp_path, static, "--depth-varying-soil")
    assert result.returncode == 0, result.stderr
    rendered = (tmp_path / "input.nml").read_text()
    assert "soiltexture_var = 'soil_type_layer'" in rendered
    assert "nmp_opt_soil = 2" in rendered
    assert "ice_category = 24" in rendered


def test_renderer_rejects_missing_four_layer_texture(tmp_path: Path) -> None:
    static = tmp_path / "static.nc"
    with netCDF4.Dataset(static, "w") as dataset:
        dataset.createDimension("y", 1)
        dataset.createDimension("x", 1)
    Path(f"{static}.ready").touch()

    result = _render(tmp_path, static, "--depth-varying-soil")
    assert result.returncode != 0
    assert "requires static variable soil_type_layer" in result.stderr
