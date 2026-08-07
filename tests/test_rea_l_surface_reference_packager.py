import json
from pathlib import Path
import subprocess
import sys

import netCDF4
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
PACKAGER = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "validation"
    / "package_rea_l_surface_reference.py"
)


def write_surface(
    path: Path,
    precipitation: float | None = None,
    mesh_coordinates: bool = False,
    diagnostics: bool = False,
) -> None:
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("lat_1", 2)
        dataset.createDimension("lon_1", 3)
        if mesh_coordinates:
            latitude, longitude = np.meshgrid(
                [46.0, 46.1], [7.0, 7.1, 7.2], indexing="ij"
            )
            dataset.createVariable(
                "lat_1", "f8", ("lat_1", "lon_1")
            )[:] = latitude
            dataset.createVariable(
                "lon_1", "f8", ("lat_1", "lon_1")
            )[:] = longitude
        else:
            dataset.createVariable("lat_1", "f8", ("lat_1",))[:] = [46.0, 46.1]
            dataset.createVariable("lon_1", "f8", ("lon_1",))[:] = [7.0, 7.1, 7.2]
        if precipitation is None:
            values = {
                "PS": 90_000.0,
                "T_2M": 280.0,
                "TD_2M": 275.0,
                "U_10M": 3.0,
                "V_10M": 4.0,
                "H_SNOW": 0.2,
                "W_SNOW": 40.0,
            }
        else:
            values = {"TOT_PREC": precipitation}
        if diagnostics:
            values.update(
                {
                    "ASWDIR_S": 100.0,
                    "ASWDIFD_S": 50.0,
                    "ATHD_S": 300.0,
                    "ASOB_S": 80.0,
                    "ATHB_S": -40.0,
                    "ALHFL_S": 20.0,
                    "ASHFL_S": 10.0,
                    "RAIN_GSP": 1.0,
                    "SNOW_GSP": 0.5,
                    "GRAU_GSP": 0.25,
                    "CLCT": 60.0,
                }
            )
        for name, value in values.items():
            dataset.createVariable(name, "f4", ("lat_1", "lon_1"))[:] = value


def write_geometry(path: Path) -> None:
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("lat_1", 2)
        dataset.createDimension("lon_1", 3)
        dataset.createVariable("HSURF", "f4", ("lat_1", "lon_1"))[:] = 500.0


def run_packager(
    tmp_path: Path,
    initial: bool = False,
    mesh_coordinates: bool = False,
    precipitation_start: float = 1.25,
    precipitation_end: float = 4.5,
):
    current = tmp_path / "current.nc"
    geometry = tmp_path / "geometry.nc"
    start = tmp_path / "start.nc"
    end = tmp_path / "end.nc"
    output = tmp_path / "reference.nc"
    manifest = tmp_path / "reference.json"
    write_surface(current, mesh_coordinates=mesh_coordinates)
    write_geometry(geometry)
    write_surface(start, precipitation_start)
    write_surface(end, precipitation_end)
    command = [
        sys.executable,
        str(PACKAGER),
        "--current",
        str(current),
        "--geometry",
        str(geometry),
        "--valid-time",
        "2020-07-01T03:00:00+00:00",
        "--interval-start",
        "2020-07-01T00:00:00+00:00",
        "--output",
        str(output),
        "--manifest",
        str(manifest),
        "--source-cycle",
        "20200701T0000",
        "--source-step",
        "3",
    ]
    if initial:
        command.append("--initial-record")
    else:
        command.extend(
            ["--precip-start", str(start), "--precip-end", str(end)]
        )
    result = subprocess.run(command, text=True, capture_output=True)
    return result, output, manifest


def test_packages_reference_and_differences_precipitation(tmp_path):
    result, output, manifest = run_packager(tmp_path)
    assert result.returncode == 0, result.stderr + result.stdout
    assert output.is_file()
    assert Path(f"{output}.ready").is_file()
    assert Path(f"{manifest}.ready").is_file()
    payload = json.loads(manifest.read_text())
    assert payload["status"] == "PASS"
    assert payload["precipitation_roundoff"]["clipped_negative_cells"] == 0
    with netCDF4.Dataset(output) as dataset:
        np.testing.assert_allclose(
            dataset.variables["precipitation_interval_ref"][:], 3.25
        )
        np.testing.assert_allclose(dataset.variables["swe_ref"][:], 40.0)
        assert dataset.variables["time_bounds"].shape == (1, 2)
        assert 0.0 < float(dataset.variables["hus2m_ref"][0, 0, 0]) < 0.1


def test_initial_reference_has_zero_precipitation(tmp_path):
    result, output, manifest = run_packager(
        tmp_path, initial=True, mesh_coordinates=True
    )
    assert result.returncode == 0, result.stderr + result.stdout
    with netCDF4.Dataset(output) as dataset:
        np.testing.assert_array_equal(
            dataset.variables["precipitation_interval_ref"][:], 0.0
        )


def test_clips_only_small_cumulative_precipitation_roundoff(tmp_path):
    result, output, manifest = run_packager(
        tmp_path, precipitation_start=1.25, precipitation_end=1.249
    )
    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(manifest.read_text())
    assert payload["precipitation_roundoff"]["clipped_negative_cells"] == 6
    with netCDF4.Dataset(output) as dataset:
        np.testing.assert_array_equal(
            dataset.variables["precipitation_interval_ref"][:], 0.0
        )


def test_rejects_material_cumulative_precipitation_decrease(tmp_path):
    result, output, manifest = run_packager(
        tmp_path, precipitation_start=1.25, precipitation_end=1.20
    )
    assert result.returncode != 0
    assert not output.exists()
    assert not manifest.exists()


def test_packages_interval_surface_diagnostics_from_cycle_endpoints(tmp_path):
    result, output, manifest = run_packager(tmp_path)
    assert result.returncode == 0, result.stderr + result.stdout

    start = tmp_path / "diagnostics_start.nc"
    end = tmp_path / "diagnostics_end.nc"
    current = tmp_path / "current.nc"
    current.unlink()
    write_surface(current, diagnostics=True)
    write_surface(start, precipitation=0.0, diagnostics=True)
    write_surface(end, precipitation=0.0, diagnostics=True)
    with netCDF4.Dataset(end, "a") as dataset:
        dataset.variables["ASWDIR_S"][:] = 150.0
        dataset.variables["RAIN_GSP"][:] = 4.0

    command = [
        sys.executable,
        str(PACKAGER),
        "--current",
        str(current),
        "--geometry",
        str(tmp_path / "geometry.nc"),
        "--precip-start",
        str(tmp_path / "start.nc"),
        "--precip-end",
        str(tmp_path / "end.nc"),
        "--diagnostics-start",
        str(start),
        "--diagnostics-end",
        str(end),
        "--diagnostics-start-hours",
        "3",
        "--diagnostics-end-hours",
        "6",
        "--valid-time",
        "2020-07-01T06:00:00+00:00",
        "--interval-start",
        "2020-07-01T03:00:00+00:00",
        "--output",
        str(output),
        "--manifest",
        str(manifest),
        "--source-cycle",
        "20200701T0000",
        "--source-step",
        "6",
    ]
    result = subprocess.run(command, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr + result.stdout
    with netCDF4.Dataset(output) as dataset:
        np.testing.assert_allclose(
            dataset.variables["sw_direct_down_interval_ref"][:], 200.0
        )
        np.testing.assert_allclose(dataset.variables["rain_interval_ref"][:], 3.0)
        np.testing.assert_allclose(
            dataset.variables["cloud_area_fraction_ref"][:], 0.6
        )
    payload = json.loads(manifest.read_text())
    assert payload["diagnostic_endpoint_hours"] == [3.0, 6.0]
