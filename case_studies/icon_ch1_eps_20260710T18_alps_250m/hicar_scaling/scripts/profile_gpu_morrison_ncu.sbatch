#!/bin/bash
# Capture one representative Morrison sedimentation-kernel launch with Nsight
# Compute.  This is intentionally separate from the normal Nsight Systems
# trace: NCU replays a kernel and is unsuitable for throughput measurements.
#SBATCH --job-name=hicar_ncu_morrison
#SBATCH --partition=normal
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=2
#SBATCH --cpus-per-task=1
#SBATCH --gres=gpu:1
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

BENCH_ROOT=${BENCH_ROOT:?set BENCH_ROOT to the existing scaling root}
PROFILE_ROOT=${PROFILE_ROOT:?set PROFILE_ROOT to an isolated profile root}
HICAR_ROOT=${HICAR_ROOT:?set HICAR_ROOT to the Morrison experiment worktree}
PROFILE_END_DATE=${HICAR_PROFILE_END_DATE:-2026-07-10 18:30:00}
EXE=$HICAR_ROOT/bin/HICAR_gpu
SOURCE_RUN=$BENCH_ROOT/runs/strong_gpu_80x80_p1/repeat_1
INPUT=$PROFILE_ROOT/input
OUT=$PROFILE_ROOT/output
RESTART=$PROFILE_ROOT/restart
PROFILE=$PROFILE_ROOT/ncu

test -x "$EXE"
test -f "$BENCH_ROOT/provenance/gpu_READY"
mkdir -p "$INPUT" "$OUT" "$RESTART" "$PROFILE" "$PROFILE_ROOT/logs"
rm -f "$OUT"/*.nc "$RESTART"/* "$PROFILE"/*
cp "$SOURCE_RUN/input/run.nml" "$INPUT/run.nml"
sed -i \
  -e "s#end_date = '2026-07-11 00:00:00'#end_date = '$PROFILE_END_DATE'#" \
  -e "s#restart_folder = '.*'#restart_folder = '$RESTART/'#" \
  -e "s#output_folder = '.*'#output_folder = '$OUT/'#" \
  -e "s#outputinterval = 3600#outputinterval = 1800#" \
  "$INPUT/run.nml"
cp -f "$HICAR_ROOT/run/NoahmpTable.TBL" "$INPUT/"
rm -rf "$INPUT/rrtmgp_support" "$INPUT/mp_support"
cp -a "$HICAR_ROOT/run/rrtmgp_support" "$INPUT/"
cp -a "$HICAR_ROOT/run/mp_support" "$INPUT/"

export HICAR_IO_PER_NODE=1 HICAR_TASKS_PER_NODE=2 OMP_NUM_THREADS=1
git -C "$HICAR_ROOT" rev-parse HEAD > "$PROFILE_ROOT/git_sha.txt"
sha256sum "$EXE" > "$PROFILE_ROOT/executable.sha256"
ncu --version > "$PROFILE_ROOT/ncu_version.txt"
scontrol show job "$SLURM_JOB_ID" > "$PROFILE_ROOT/slurm.txt"

cd "$INPUT"
srun -u --distribution=block:block --cpu-bind=none \
  "$BENCH_ROOT/scripts/profile_gpu_morrison_ncu_rank_wrapper.sh" "$EXE" "$INPUT/run.nml" "$PROFILE" \
  | tee "$PROFILE_ROOT/logs/hicar_${SLURM_JOB_ID}.out"

test -f "$PROFILE/hicar_morrison.ncu-rep"
ncu --import "$PROFILE/hicar_morrison.ncu-rep" --csv --page details \
  > "$PROFILE/details.csv"
