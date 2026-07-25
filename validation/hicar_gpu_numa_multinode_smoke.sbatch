#!/bin/bash
#SBATCH --job-name=hicar_gpu_numa_2n
#SBATCH --partition=debug
#SBATCH --nodes=2
#SBATCH --ntasks=10
#SBATCH --ntasks-per-node=5
#SBATCH --cpus-per-task=1
#SBATCH --gres=gpu:4
#SBATCH --exclusive
#SBATCH --time=00:08:00
#SBATCH --output=/scratch/mch/olifu/icon_hicar/validation/gpu_io_cpu_smoke/logs/%x_%j.out
#SBATCH --error=/scratch/mch/olifu/icon_hicar/validation/gpu_io_cpu_smoke/logs/%x_%j.err

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"
module purge || true
module use "$USER_ENV_ROOT/modules"
module load nvhpc/24.5 cray-mpich-nvhpc/8.1.30 cuda/12.3.0-gcc \
  netcdf-c/4.9.2-nvhpc netcdf-fortran/4.6.1-nvhpc hdf5/1.14.3-nvhpc \
  fftw/3.3.10-gcc >/dev/null

ROOT="$SCRATCH/icon_hicar/validation/gpu_io_cpu_smoke"
EXE="$SCRATCH/icon_hicar/HICAR-scaling/bin/HICAR_gpu"
NML="$ROOT/input/gpu_io_cpu_smoke.nml"
WRAPPER="$ROOT/input/balfrin_rank_pin_wrapper.sh"
test -x "$EXE"
test -f "$NML"
test -x "$WRAPPER"

rm -rf "$ROOT/output" "$ROOT/restart"
mkdir -p "$ROOT/output" "$ROOT/restart" "$ROOT/provenance"
rm -f "$ROOT/provenance/MULTINODE_PASS"

srun --distribution=block:block --cpu-bind=none "$WRAPPER" "$EXE" "$NML" | tee "$ROOT/provenance/multinode_model.out"
grep -q 'Simulation completed successfully!' "$ROOT/provenance/multinode_model.out"
test -n "$(find "$ROOT/output" -type f -name '*.nc' -print -quit)"
touch "$ROOT/provenance/MULTINODE_PASS"
