#!/bin/bash
#SBATCH --job-name=hicar_33h_ready
#SBATCH --partition=pp-long
#SBATCH --nodes=1
#SBATCH --ntasks=5
#SBATCH --cpus-per-task=1
#SBATCH --time=06:00:00
#SBATCH --output=/scratch/mch/olifu/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_ready_full_33h/logs/hicar_33h_ready_%j.out
#SBATCH --error=/scratch/mch/olifu/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_ready_full_33h/logs/hicar_33h_ready_%j.err

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"
module purge || true
module use "$USER_ENV_ROOT/modules"
module load gcc/12.3.0   cray-mpich-gcc/8.1.30   netcdf-c/4.8.1-gcc   netcdf-fortran/4.5.4-gcc   fftw/3.3.10-gcc >/dev/null

export HICAR_IO_PER_NODE=1
cd "$SCRATCH/icon_hicar/HICAR"
srun -n "$SLURM_NTASKS" "$SCRATCH/icon_hicar/HICAR/bin/HICAR_debug" "/scratch/mch/olifu/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_ready_full_33h/input/icon_hicar_250m_ready_full_33h.nml"
