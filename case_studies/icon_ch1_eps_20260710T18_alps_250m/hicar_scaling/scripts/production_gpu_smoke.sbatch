#!/bin/bash
# Production-physics GPU gate: four compute A100s plus a CPU-only I/O rank.
#SBATCH --job-name=hicar_prod_gpu_smoke
#SBATCH --partition=short
#SBATCH --nodes=1
#SBATCH --ntasks=5
#SBATCH --cpus-per-task=1
#SBATCH --gres=gpu:4
#SBATCH --time=01:00:00
#SBATCH --output=%x-%j.out
#SBATCH --error=%x-%j.err
set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"; module purge || true; module use "$USER_ENV_ROOT/modules"
module load python/3.11.7 nvhpc/24.5 cray-mpich-nvhpc/8.1.30 cuda/12.3.0-gcc netcdf-c/4.9.2-nvhpc netcdf-fortran/4.6.1-nvhpc hdf5/1.14.3-nvhpc fftw/3.3.10-gcc
ROOT=${SCALING_ROOT:?}; HICAR_ROOT=${HICAR_ROOT:-$SCRATCH/icon_hicar/HICAR-scaling}; RUN="$ROOT/runs/weak_cpu_10x10_p1/repeat_1"; DOMAIN="$ROOT/domains/10x10km"
test -f "$ROOT/provenance/production_cpu_physics_READY"; test -f "$DOMAIN/PREFLIGHT_OK" || touch "$DOMAIN/PREFLIGHT_OK"
test -x "$HICAR_ROOT/bin/HICAR_gpu"; test -f "$HICAR_ROOT/run/NoahmpTable.TBL"; test -d "$HICAR_ROOT/run/rrtmgp_support"; test -d "$HICAR_ROOT/run/mp_support"
rm -rf "$RUN/output" "$RUN/restart"
mkdir -p "$RUN/output" "$RUN/restart" "$RUN/logs" "$RUN/input"
rm -rf "$RUN/input/rrtmgp_support" "$RUN/input/mp_support"
cp -f "$HICAR_ROOT/run/NoahmpTable.TBL" "$RUN/input/"; cp -a "$HICAR_ROOT/run/rrtmgp_support" "$RUN/input/"; cp -a "$HICAR_ROOT/run/mp_support" "$RUN/input/"
export HICAR_IO_PER_NODE=1 OMP_NUM_THREADS=1 HICAR_TASKS_PER_NODE=5
cd "$RUN/input"
srun -u --distribution=block:block --cpu-bind=none "$ROOT/scripts/gpu_rank_wrapper.sh" "$HICAR_ROOT/bin/HICAR_gpu" "$RUN/input/run.nml" | tee "$RUN/logs/slurm_${SLURM_JOB_ID}.out"
python3 "$ROOT/analysis/validate_run.py" --run "$RUN" --expected-hours 1
touch "$ROOT/provenance/production_gpu_physics_READY"
