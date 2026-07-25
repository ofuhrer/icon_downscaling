#!/bin/bash
#SBATCH --job-name=hicar_dbg_1h
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=5
#SBATCH --cpus-per-task=1
#SBATCH --time=00:30:00
#SBATCH --output=/scratch/mch/olifu/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_cpu_compare_1h/logs/hicar_debug_1h_%j.out
#SBATCH --error=/scratch/mch/olifu/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_cpu_compare_1h/logs/hicar_debug_1h_%j.err

set -euo pipefail

[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"
module purge || true
module use "$USER_ENV_ROOT/modules"
module load gcc/12.3.0 \
  cray-mpich-gcc/8.1.30 \
  netcdf-c/4.8.1-gcc \
  netcdf-fortran/4.5.4-gcc \
  fftw/3.3.10-gcc >/dev/null

CASE_ROOT="/scratch/mch/olifu/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_cpu_compare_1h"
HICAR_ROOT="$SCRATCH/icon_hicar/HICAR"
NML="$CASE_ROOT/input/icon_hicar_250m_compare_debug_1h.nml"

rm -rf "$CASE_ROOT/debug_output" "$CASE_ROOT/restart_debug"
mkdir -p "$CASE_ROOT/debug_output" "$CASE_ROOT/restart_debug"

export HICAR_IO_PER_NODE=1
export OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-1}"

cd "$HICAR_ROOT"
echo "Running debug executable:"
ls -l "$HICAR_ROOT/bin/HICAR_debug"
srun -n "$SLURM_NTASKS" "$HICAR_ROOT/bin/HICAR_debug" "$NML"
