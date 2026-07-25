#!/usr/bin/env python3
"""Create HICAR upper-level vertical-wind sensitivity run directories."""

from __future__ import annotations

import os
from pathlib import Path


START_DATE = "2026-07-10 18:00:00"
END_DATE = "2026-07-11 18:00:00"
CASE_NAME = "icon_ch1_eps_20260710T18_alps_250m"
ROOT = Path(os.environ.get(
    "CASE_ROOT",
    f"{os.environ.get('SCRATCH', '/scratch/mch/olifu')}/icon_hicar/case_studies/{CASE_NAME}",
))
SENS_ROOT = ROOT / "hicar_w_sensitivity_24h"
HICAR_EXE = Path(os.environ.get(
    "HICAR_EXE",
    f"{os.environ.get('SCRATCH', '/scratch/mch/olifu')}/icon_hicar/HICAR/bin/HICAR_debug",
))

CASES = [
    {
        "id": "none_20km",
        "label": "current wind none, 20 km lid",
        "wind": "none",
        "model_top_height": 20000.0,
        "wind_block": "",
        "extra_outputs": "",
    },
    {
        "id": "var_no_sx_20km",
        "label": "variational solver, no Sx, 20 km lid",
        "wind": "variational solver",
        "model_top_height": 20000.0,
        "wind_block": "  Sx = .False.\n  smooth_wind_distance = 0.0\n",
        "extra_outputs": ", 'wind_alpha'",
    },
    {
        "id": "var_sx_20km",
        "label": "variational solver, Sx, 20 km lid",
        "wind": "variational solver",
        "model_top_height": 20000.0,
        "wind_block": "  Sx = .True.\n  smooth_wind_distance = 500.0\n",
        "extra_outputs": ", 'wind_alpha'",
    },
    {
        "id": "var_sx_top12km",
        "label": "variational solver, Sx, 12 km lid",
        "wind": "variational solver",
        "model_top_height": 12000.0,
        "wind_block": "  Sx = .True.\n  smooth_wind_distance = 500.0\n",
        "extra_outputs": ", 'wind_alpha'",
    },
]


def write_text(path: Path, text: str, executable: bool = False) -> None:
    path.write_text(text, encoding="utf-8")
    if executable:
        path.chmod(path.stat().st_mode | 0o111)


def make_namelist(case: dict[str, object], run_root: Path) -> str:
    forcing_file_list = run_root / "input" / "forcing_file_list.txt"
    output_vars = (
        "'qv', 'temperature', 'potential_temperature', 'pressure', 'density', "
        "'precipitation', 'u', 'v', 'w', 'w_grid', 'z', 'dzdx', 'dzdy', 'jacobian'"
        f"{case['extra_outputs']}"
    )
    return f"""&general
  start_date = '{START_DATE}'
  end_date = '{END_DATE}'
/

&restart
  restart_folder = '{run_root}/restart/'
/

&domain
  init_conditions_file = '{ROOT}/static/domain_static_relaxed.nc'
  wait_for_ready_file = .True.
  ready_file_timeout = 120
  dx = 250.0
  nz = 40
  lat_hi = 'lat'
  lon_hi = 'lon'
  hgt_hi = 'topo'
  landvar = 'landmask'
  cropcategory_var = 'landuse'
  soiltype_var = 'soil_type'
  use_map_factors = .True.
  auto_level = 3
  model_top_height = {case['model_top_height']:.1f}
  stretch_fac = 1.0
  decay_rate_L_topo = 1.0
  decay_rate_S_topo = 1.0
/

&forcing
  forcing_file_list = '{forcing_file_list}'
  wait_for_ready_file = .True.
  ready_file_timeout = 120
  inputinterval = 3600
  time_var = 'time'
  pvar = 'P'
  tvar = 'T'
  qvvar = 'QV'
  uvar = 'U'
  vvar = 'V'
  wvar = 'W'
  hgtvar = 'HSURF'
  zvar = 'HFL'
  latvar = 'lat_1'
  lonvar = 'lon_1'
  qv_is_spec_humidity = .True.
  t_is_potential = .False.
  relax_filters = .True.
/

&physics
  wind = '{case['wind']}'
  mp = 'morrison'
  pbl = 'none'
  lsm = 'none'
  sfc = 'none'
  water = 'none'
  rad = 'none'
/

&time_parameters
  RK3 = .True.
  cfl_reduction_factor = 1.6
/

&lt_parameters
/

&mp_parameters
/

&adv_parameters
  h_order = 3
  v_order = 3
  flux_corr = 1
  cz_diff_order = 0
/

&sm_parameters
/

&lsm_parameters
  LU_Categories = 'USGS'
/

&cu_parameters
/

&rad_parameters
/

&pbl_parameters
/

&sfc_parameters
/

&wind
{case['wind_block']}/

&output
  output_folder = '{run_root}/output/'
  outputinterval = 3600
  output_vars = {output_vars}
/
"""


def make_sbatch(case: dict[str, object], run_root: Path, namelist: Path) -> str:
    job_id = str(case["id"])
    return f"""#!/bin/bash
#SBATCH --job-name=hicar_{job_id}
#SBATCH --partition=pp-long
#SBATCH --nodes=1
#SBATCH --ntasks=5
#SBATCH --cpus-per-task=1
#SBATCH --time=04:00:00
#SBATCH --output={run_root}/logs/hicar_{job_id}_%j.out
#SBATCH --error={run_root}/logs/hicar_{job_id}_%j.err

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"
module purge || true
module use "$USER_ENV_ROOT/modules"
module load gcc/12.3.0 cray-mpich-gcc/8.1.30 netcdf-c/4.8.1-gcc netcdf-fortran/4.5.4-gcc fftw/3.3.10-gcc >/dev/null

export HICAR_IO_PER_NODE=1
cd "$SCRATCH/icon_hicar/HICAR"
srun -n "$SLURM_NTASKS" "{HICAR_EXE}" "{namelist}"
"""


def main() -> None:
    SENS_ROOT.mkdir(parents=True, exist_ok=True)
    forcing_list = "\n".join(
        f'"{ROOT}/forcing/hicar_forcing_f{i:03d}.nc"' for i in range(34)
    ) + "\n"
    lines = [
        "# HICAR upper-level vertical-wind sensitivity runs",
        "",
        f"- Case root: `{ROOT}`",
        f"- Sensitivity root: `{SENS_ROOT}`",
        f"- Start: `{START_DATE}`",
        f"- End: `{END_DATE}`",
        "- Purpose: isolate effects of the wind solver, Sx terrain sheltering, and model-lid height on upper-level `w`.",
        "",
        "| Case | Description |",
        "| --- | --- |",
    ]
    for case in CASES:
        run_root = SENS_ROOT / str(case["id"])
        for subdir in ("input", "logs", "output", "restart"):
            (run_root / subdir).mkdir(parents=True, exist_ok=True)
        write_text(run_root / "input" / "forcing_file_list.txt", forcing_list)
        namelist = run_root / "input" / f"icon_hicar_250m_{case['id']}.nml"
        write_text(namelist, make_namelist(case, run_root))
        sbatch = run_root / f"run_hicar_{case['id']}.sbatch"
        write_text(sbatch, make_sbatch(case, run_root, namelist), executable=True)
        lines.append(f"| `{case['id']}` | {case['label']} |")
        print(f"{case['id']} {sbatch}")
    write_text(SENS_ROOT / "README.md", "\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
