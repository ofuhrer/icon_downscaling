#!/bin/bash
#SBATCH --job-name=hicar_acc_1h
#SBATCH --partition=debug
#SBATCH --nodes=1
#SBATCH --ntasks=5
#SBATCH --ntasks-per-node=5
#SBATCH --cpus-per-task=1
#SBATCH --gres=gpu:4
#SBATCH --time=00:30:00
#SBATCH --exclusive
#SBATCH --hint=nomultithread
#SBATCH --output=/scratch/mch/olifu/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_openacc_1h/logs/hicar_openacc_1h_%j.out
#SBATCH --error=/scratch/mch/olifu/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_openacc_1h/logs/hicar_openacc_1h_%j.err

set -euo pipefail

[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"
module purge || true
module use "$USER_ENV_ROOT/modules"
module load nvhpc/24.5 \
  cray-mpich-nvhpc/8.1.30 \
  cuda/12.3.0-gcc \
  netcdf-c/4.9.2-nvhpc \
  netcdf-fortran/4.6.1-nvhpc \
  hdf5/1.14.3-nvhpc \
  fftw/3.3.10-gcc >/dev/null

CASE_ROOT="/scratch/mch/olifu/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_openacc_1h"
HICAR_ROOT="$SCRATCH/icon_hicar/HICAR"
NML="$CASE_ROOT/input/icon_hicar_250m_openacc_1h.nml"

rm -rf "$CASE_ROOT/gpu_output" "$CASE_ROOT/restart_gpu"
mkdir -p "$CASE_ROOT/gpu_output" "$CASE_ROOT/restart_gpu"

export HICAR_IO_PER_NODE=1
export OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-1}"
export MPICH_GPU_SUPPORT_ENABLED=1
export NVCOMPILER_ACC_NOTIFY="${NVCOMPILER_ACC_NOTIFY:-0}"

nvidia-smi

cd "$HICAR_ROOT"
echo "Running OpenACC executable:"
ls -l "$HICAR_ROOT/bin/HICAR_gpu"
ldd "$HICAR_ROOT/bin/HICAR_gpu" | grep -E "cufft|cuda|netcdf|netcdff|mpi|not found" || true
srun -u -n "$SLURM_NTASKS" --ntasks-per-node="$SLURM_NTASKS_PER_NODE" "$HICAR_ROOT/bin/HICAR_gpu" "$NML"
