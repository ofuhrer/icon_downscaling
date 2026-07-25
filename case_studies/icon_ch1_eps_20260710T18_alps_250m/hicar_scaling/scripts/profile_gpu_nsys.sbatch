#!/bin/bash
# Representative HICAR GPU profile: production physics, 80x80 km / 250 m,
# one A100 compute rank plus the CPU-only I/O rank, thirty simulated minutes.
# The isolated output root prevents mutation of benchmark runs.
#SBATCH --job-name=hicar_nsys_80x80_p1
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
HICAR_ROOT=${HICAR_ROOT:-$SCRATCH/icon_hicar/HICAR-scaling}
PROFILE_END_DATE=${HICAR_PROFILE_END_DATE:-2026-07-10 18:30:00}
# validate_run.py uses an integer number of expected output files; one
# consolidated NetCDF file is valid for both the 5-minute screen and 30-minute
# confirmation profiles.
PROFILE_EXPECTED_HOURS=${HICAR_PROFILE_EXPECTED_HOURS:-1}
RRTMGP_BLOCK_N=${HICAR_RRTMGP_BLOCK_N:-}
# Set to a non-empty path to record device-memory usage once per second while
# HICAR runs.  This is intentionally opt-in so normal Nsight profiles retain
# their existing overhead and output.
GPU_MEMORY_LOG=${HICAR_GPU_MEMORY_LOG:-}
GPU_MEMORY_INTERVAL=${HICAR_GPU_MEMORY_INTERVAL:-1}
EXE=$HICAR_ROOT/bin/HICAR_gpu
SOURCE_RUN=$BENCH_ROOT/runs/strong_gpu_80x80_p1/repeat_1
INPUT=$PROFILE_ROOT/input
OUT=$PROFILE_ROOT/output
RESTART=$PROFILE_ROOT/restart
PROFILE=$PROFILE_ROOT/nsys

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
if [[ -n "$RRTMGP_BLOCK_N" ]]; then
  sed -i "/^&rad_parameters/a\\  rrtmgp_block_N = $RRTMGP_BLOCK_N" "$INPUT/run.nml"
fi
cp -f "$HICAR_ROOT/run/NoahmpTable.TBL" "$INPUT/"
rm -rf "$INPUT/rrtmgp_support" "$INPUT/mp_support"
cp -a "$HICAR_ROOT/run/rrtmgp_support" "$INPUT/"
cp -a "$HICAR_ROOT/run/mp_support" "$INPUT/"

export HICAR_IO_PER_NODE=1 HICAR_TASKS_PER_NODE=2 OMP_NUM_THREADS=1
git -C "$HICAR_ROOT" rev-parse HEAD > "$PROFILE_ROOT/git_sha.txt"
sha256sum "$EXE" > "$PROFILE_ROOT/executable.sha256"
nsys --version > "$PROFILE_ROOT/nsys_version.txt"
scontrol show job "$SLURM_JOB_ID" > "$PROFILE_ROOT/slurm.txt"

cd "$INPUT"
GPU_MEMORY_PID=""
if [[ -n "$GPU_MEMORY_LOG" ]]; then
  mkdir -p "$(dirname "$GPU_MEMORY_LOG")"
  printf 'timestamp,index,memory.used [MiB],memory.total [MiB]\n' > "$GPU_MEMORY_LOG"
  (
    while :; do
      nvidia-smi --query-gpu=timestamp,index,memory.used,memory.total \
        --format=csv,noheader,nounits >> "$GPU_MEMORY_LOG"
      sleep "$GPU_MEMORY_INTERVAL"
    done
  ) &
  GPU_MEMORY_PID=$!
fi
cleanup_gpu_memory_sampler() {
  if [[ -n "$GPU_MEMORY_PID" ]]; then
    kill "$GPU_MEMORY_PID" 2>/dev/null || true
    wait "$GPU_MEMORY_PID" 2>/dev/null || true
  fi
}
trap cleanup_gpu_memory_sampler EXIT
srun -u --distribution=block:block --cpu-bind=none \
  "$BENCH_ROOT/scripts/profile_gpu_rank_wrapper.sh" "$EXE" "$INPUT/run.nml" "$PROFILE" \
  | tee "$PROFILE_ROOT/logs/hicar_${SLURM_JOB_ID}.out"
cleanup_gpu_memory_sampler
trap - EXIT

test -f "$PROFILE/hicar_rank0.nsys-rep"
nsys stats --force-overwrite=true --format csv --output "$PROFILE/stats" \
  --report cuda_gpu_kern_sum,cuda_api_sum,cuda_gpu_mem_time_sum \
  "$PROFILE/hicar_rank0.nsys-rep"
python3 "$BENCH_ROOT/analysis/validate_run.py" --run "$PROFILE_ROOT" --expected-hours "$PROFILE_EXPECTED_HOURS"
