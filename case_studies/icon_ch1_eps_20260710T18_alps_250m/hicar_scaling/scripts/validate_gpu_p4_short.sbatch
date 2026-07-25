#!/bin/bash
# Short production-physics validation on four compute GPUs plus the CPU-only
# I/O rank.  Inputs and outputs are isolated from the scaling measurements.
#SBATCH --job-name=hicar_gpu_p4_short
#SBATCH --partition=normal
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=5
#SBATCH --cpus-per-task=1
#SBATCH --gres=gpu:4
#SBATCH --exclusive
#SBATCH --hint=nomultithread
#SBATCH --time=01:00:00

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"
module purge || true
module use "$USER_ENV_ROOT/modules"
module load python/3.11.7 nvhpc/24.5 cray-mpich-nvhpc/8.1.30 cuda/12.3.0-gcc \
  netcdf-c/4.9.2-nvhpc netcdf-fortran/4.6.1-nvhpc hdf5/1.14.3-nvhpc \
  fftw/3.3.10-gcc >/dev/null

BENCH_ROOT=${BENCH_ROOT:?set BENCH_ROOT to the scaling root}
RUN_ROOT=${RUN_ROOT:?set RUN_ROOT to an isolated validation directory}
HICAR_ROOT=${HICAR_ROOT:?set HICAR_ROOT to the candidate worktree}
EXE=$HICAR_ROOT/bin/HICAR_gpu
SOURCE_INPUT=$BENCH_ROOT/runs/strong_gpu_80x80_p4/repeat_1/input
INPUT=$RUN_ROOT/input
OUT=$RUN_ROOT/output
RESTART=$RUN_ROOT/restart

test -x "$EXE"
test -f "$BENCH_ROOT/provenance/gpu_READY"
mkdir -p "$INPUT" "$OUT" "$RESTART" "$RUN_ROOT/logs"
rm -f "$OUT"/*.nc "$RESTART"/*
cp "$SOURCE_INPUT/run.nml" "$INPUT/run.nml"
sed -i \
  -e "s#end_date = '2026-07-11 00:00:00'#end_date = '2026-07-10 18:30:00'#" \
  -e "s#restart_folder = '.*'#restart_folder = '$RESTART/'#" \
  -e "s#output_folder = '.*'#output_folder = '$OUT/'#" \
  -e "s#outputinterval = 3600#outputinterval = 1800#" \
  "$INPUT/run.nml"
cp -f "$HICAR_ROOT/run/NoahmpTable.TBL" "$INPUT/"
rm -rf "$INPUT/rrtmgp_support" "$INPUT/mp_support"
cp -a "$HICAR_ROOT/run/rrtmgp_support" "$INPUT/"
cp -a "$HICAR_ROOT/run/mp_support" "$INPUT/"

export HICAR_IO_PER_NODE=1 HICAR_TASKS_PER_NODE=5 OMP_NUM_THREADS=1
git -C "$HICAR_ROOT" rev-parse HEAD > "$RUN_ROOT/git_sha.txt"
sha256sum "$EXE" > "$RUN_ROOT/executable.sha256"
scontrol show job "$SLURM_JOB_ID" > "$RUN_ROOT/slurm.txt"

cd "$INPUT"
srun -u --distribution=block:block --cpu-bind=none \
  "$BENCH_ROOT/scripts/gpu_rank_wrapper.sh" "$EXE" "$INPUT/run.nml" \
  | tee "$RUN_ROOT/logs/hicar_${SLURM_JOB_ID}.out"
python3 "$BENCH_ROOT/analysis/validate_run.py" --run "$RUN_ROOT" --expected-hours 1
