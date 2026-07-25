#!/bin/bash
#SBATCH --job-name=hwd_s1_auto1_n60_top12_s15_3
#SBATCH --partition=pp-long
#SBATCH --nodes=1
#SBATCH --ntasks=5
#SBATCH --cpus-per-task=1
#SBATCH --time=02:00:00
#SBATCH --output=/scratch/mch/olifu/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_wind_design_sensitivity_24h/s1_auto1_n60_top12_s15_3/logs/hwd_s1_auto1_n60_top12_s15_3_%j.out
#SBATCH --error=/scratch/mch/olifu/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_wind_design_sensitivity_24h/s1_auto1_n60_top12_s15_3/logs/hwd_s1_auto1_n60_top12_s15_3_%j.err

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"
module purge || true
module use "$USER_ENV_ROOT/modules"
module load gcc/12.3.0 cray-mpich-gcc/8.1.30 netcdf-c/4.8.1-gcc netcdf-fortran/4.5.4-gcc fftw/3.3.10-gcc >/dev/null

export HICAR_IO_PER_NODE=1
cd "$SCRATCH/icon_hicar/HICAR"
srun -n "$SLURM_NTASKS" "/scratch/mch/olifu/icon_hicar/HICAR/bin/HICAR_release" "/scratch/mch/olifu/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_wind_design_sensitivity_24h/s1_auto1_n60_top12_s15_3/input/icon_hicar_250m_s1_auto1_n60_top12_s15_3.nml"
