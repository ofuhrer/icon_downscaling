#!/bin/bash
#SBATCH --job-name=hicar_io_cpu_smoke
#SBATCH --partition=debug
#SBATCH --nodes=1
#SBATCH --ntasks=5
#SBATCH --ntasks-per-node=5
#SBATCH --cpus-per-task=1
#SBATCH --gres=gpu:4
#SBATCH --exclusive
#SBATCH --time=00:15:00
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
RANK_MAP="$ROOT/input/gpu_io_cpu_rank_map.conf"
mkdir -p "$ROOT/logs" "$ROOT/output" "$ROOT/restart" "$ROOT/provenance"
rm -f "$ROOT/provenance/PASS" "$ROOT/provenance/rank_environment.txt"
test -x "$EXE"
test -f "$NML"
test -f "$RANK_MAP"

export HICAR_IO_PER_NODE=1
export OMP_NUM_THREADS=1

# With five local ranks HICAR makes rank 4 the I/O server.  Compute ranks each
# receive one physical GPU before MPI_Init; the I/O rank receives none.
srun --cpu-bind=cores --multi-prog "$RANK_MAP" | tee "$ROOT/provenance/model.out"
grep -q 'Simulation completed successfully!' "$ROOT/provenance/model.out"
test -n "$(find "$ROOT/output" -type f -name '*.nc' -print -quit)"
test "$(wc -l < "$ROOT/provenance/rank_environment.txt")" -eq 5
touch "$ROOT/provenance/PASS"
