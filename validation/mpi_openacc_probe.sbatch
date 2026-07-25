#!/bin/bash
#SBATCH --job-name=hicar_mpi_acc_probe
#SBATCH --partition=debug
#SBATCH --nodes=1
#SBATCH --ntasks=5
#SBATCH --ntasks-per-node=5
#SBATCH --cpus-per-task=1
#SBATCH --gres=gpu:4
#SBATCH --exclusive
#SBATCH --time=00:05:00
#SBATCH --output=/scratch/mch/olifu/icon_hicar/validation/gpu_io_cpu_smoke/logs/%x_%j.out
#SBATCH --error=/scratch/mch/olifu/icon_hicar/validation/gpu_io_cpu_smoke/logs/%x_%j.err

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"
module purge || true
module use "$USER_ENV_ROOT/modules"
module load nvhpc/24.5 cray-mpich-nvhpc/8.1.30 cuda/12.3.0-gcc >/dev/null

ROOT="$SCRATCH/icon_hicar/validation/gpu_io_cpu_smoke"
mkdir -p "$ROOT/bin" "$ROOT/logs"
mpifort -acc -gpu=cc80 -o "$ROOT/bin/mpi_openacc_probe" "$ROOT/input/mpi_openacc_probe.F90"
srun --cpu-bind=cores --multi-prog "$ROOT/input/mpi_openacc_probe.conf"
