#!/usr/bin/env python3
"""Create HICAR wind-downscaling design sensitivity run directories."""

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
SENS_ROOT = ROOT / "hicar_wind_design_sensitivity_24h"
HICAR_EXE = Path(os.environ.get(
    "HICAR_EXE",
    f"{os.environ.get('SCRATCH', '/scratch/mch/olifu')}/icon_hicar/HICAR/bin/HICAR_release",
))

CASES = [
    {
        "id": "v1_auto1_n60_top12_s26",
        "label": "primary vertical grid: auto1 n60 top12 km, SLEVE 2/6",
        "auto_level": 1,
        "nz": 60,
        "model_top_height": 12000.0,
        "height_lowest_level": 20.0,
        "stretch_fac": 0.65,
        "decay_l": 2.0,
        "decay_s": 6.0,
    },
    {
        "id": "v2_auto1_n80_top12_s26",
        "label": "high near-surface resolution: auto1 n80 top12 km, SLEVE 2/6",
        "auto_level": 1,
        "nz": 80,
        "model_top_height": 12000.0,
        "height_lowest_level": 15.0,
        "stretch_fac": 0.65,
        "decay_l": 2.0,
        "decay_s": 6.0,
    },
    {
        "id": "v3_auto1_n60_top10_s26",
        "label": "lower lid: auto1 n60 top10 km, SLEVE 2/6",
        "auto_level": 1,
        "nz": 60,
        "model_top_height": 10000.0,
        "height_lowest_level": 20.0,
        "stretch_fac": 0.65,
        "decay_l": 2.0,
        "decay_s": 6.0,
    },
    {
        "id": "v4_auto1_n70_top14_s26",
        "label": "higher lid: auto1 n70 top14 km, SLEVE 2/6",
        "auto_level": 1,
        "nz": 70,
        "model_top_height": 14000.0,
        "height_lowest_level": 20.0,
        "stretch_fac": 0.65,
        "decay_l": 2.0,
        "decay_s": 6.0,
    },
    {
        "id": "v5_auto4_n70_top12_s26",
        "label": "alternative COSMO-like vertical distribution: auto4 n70 top12 km, SLEVE 2/6",
        "auto_level": 4,
        "nz": 70,
        "model_top_height": 12000.0,
        "height_lowest_level": 15.0,
        "stretch_fac": 1.1,
        "decay_l": 2.0,
        "decay_s": 6.0,
    },
    {
        "id": "s0_auto1_n60_top12_s11",
        "label": "slow/conservative SLEVE decay control: auto1 n60 top12 km, SLEVE 1/1",
        "auto_level": 1,
        "nz": 60,
        "model_top_height": 12000.0,
        "height_lowest_level": 20.0,
        "stretch_fac": 0.65,
        "decay_l": 1.0,
        "decay_s": 1.0,
    },
    {
        "id": "s1_auto1_n60_top12_s15_3",
        "label": "conservative compromise SLEVE decay: auto1 n60 top12 km, SLEVE 1.5/3",
        "auto_level": 1,
        "nz": 60,
        "model_top_height": 12000.0,
        "height_lowest_level": 20.0,
        "stretch_fac": 0.65,
        "decay_l": 1.5,
        "decay_s": 3.0,
    },
    {
        "id": "s2_auto1_n60_top12_s24",
        "label": "moderate SLEVE decay: auto1 n60 top12 km, SLEVE 2/4",
        "auto_level": 1,
        "nz": 60,
        "model_top_height": 12000.0,
        "height_lowest_level": 20.0,
        "stretch_fac": 0.65,
        "decay_l": 2.0,
        "decay_s": 4.0,
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
        "'precipitation', 'u', 'v', 'w', 'w_grid', 'z', 'dzdx', 'dzdy', "
        "'jacobian', 'wind_alpha'"
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
  nz = {case['nz']}
  lat_hi = 'lat'
  lon_hi = 'lon'
  hgt_hi = 'topo'
  landvar = 'landmask'
  cropcategory_var = 'landuse'
  soiltype_var = 'soil_type'
  use_map_factors = .True.
  auto_level = {case['auto_level']}
  height_lowest_level = {case['height_lowest_level']:.1f}
  model_top_height = {case['model_top_height']:.1f}
  stretch_fac = {case['stretch_fac']}
  decay_rate_L_topo = {case['decay_l']:.1f}
  decay_rate_S_topo = {case['decay_s']:.1f}
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
  wind = 'variational solver'
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
  Sx = .True.
  smooth_wind_distance = 500.0
/

&output
  output_folder = '{run_root}/output/'
  outputinterval = 3600
  output_vars = {output_vars}
/
"""


def make_sbatch(case: dict[str, object], run_root: Path, namelist: Path) -> str:
    job_id = str(case["id"])
    return f"""#!/bin/bash
#SBATCH --job-name=hwd_{job_id}
#SBATCH --partition=pp-long
#SBATCH --nodes=1
#SBATCH --ntasks=5
#SBATCH --cpus-per-task=1
#SBATCH --time=02:00:00
#SBATCH --output={run_root}/logs/hwd_{job_id}_%j.out
#SBATCH --error={run_root}/logs/hwd_{job_id}_%j.err

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
        "# HICAR Wind-Downscaling Design Sensitivity Runs",
        "",
        f"- Case root: `{ROOT}`",
        f"- Sensitivity root: `{SENS_ROOT}`",
        f"- Start: `{START_DATE}`",
        f"- End: `{END_DATE}`",
        f"- Executable: `{HICAR_EXE}`",
        "- Purpose: explore vertical grid, lid height, and conservative SLEVE decay choices for wind downscaling using the validated release executable.",
        "- Fixed physics baseline: `wind = 'variational solver'`, `Sx = .True.`, `smooth_wind_distance = 500 m`.",
        "- SLEVE choices deliberately avoid aggressive decay rates so candidate settings remain plausible for larger domains, including an all-Switzerland domain with border and terrain up to roughly 4500-5000 m ASL.",
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
