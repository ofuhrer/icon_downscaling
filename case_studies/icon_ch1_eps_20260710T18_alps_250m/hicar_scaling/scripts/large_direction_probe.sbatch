#!/bin/bash
# Short-duration, large-domain direction check.  Submit CPU with the defaults;
# submit GPU with --partition=debug --ntasks=2 --gres=gpu:1 --exclusive.
# Both cases use the production 240x160 km inputs but simulate only five minutes.
# Required: BENCH_ROOT, PROBE_ROOT, HICAR_ROOT, PROBE_PLATFORM={cpu,gpu}.
#SBATCH --job-name=hicar_large_direction
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=5
#SBATCH --cpus-per-task=1
#SBATCH --time=00:30:00
#SBATCH --hint=nomultithread

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"
module purge || true
module use "$USER_ENV_ROOT/modules"

BENCH_ROOT=${BENCH_ROOT:?set BENCH_ROOT}
PROBE_ROOT=${PROBE_ROOT:?set PROBE_ROOT}
HICAR_ROOT=${HICAR_ROOT:?set HICAR_ROOT}
PROBE_PLATFORM=${PROBE_PLATFORM:?set PROBE_PLATFORM to cpu or gpu}
PROBE_END_DATE=${HICAR_PROBE_END_DATE:-2026-07-10 18:01:00}
PROBE_OUTPUT_INTERVAL=${HICAR_PROBE_OUTPUT_INTERVAL:-60}
PROBE_RRTMGP_BLOCK_N=${HICAR_PROBE_RRTMGP_BLOCK_N:-}

case "$PROBE_PLATFORM" in
  cpu)
    module load python/3.11.7 gcc/12.3.0 cray-mpich-gcc/8.1.30 \
      netcdf-c/4.8.1-gcc netcdf-fortran/4.5.4-gcc fftw/3.3.10-gcc >/dev/null
    EXE=$HICAR_ROOT/bin/HICAR_release
    SOURCE_INPUT=$BENCH_ROOT/runs/strong_cpu_240x160_p4/repeat_1/input
    TASKS=${HICAR_PROBE_TASKS:-5}
    ;;
  gpu)
    module load python/3.11.7 nvhpc/24.5 cray-mpich-nvhpc/8.1.30 cuda/12.3.0-gcc \
      netcdf-c/4.9.2-nvhpc netcdf-fortran/4.6.1-nvhpc hdf5/1.14.3-nvhpc \
      fftw/3.3.10-gcc >/dev/null
    EXE=$HICAR_ROOT/bin/HICAR_gpu
    SOURCE_INPUT=$BENCH_ROOT/runs/strong_gpu_240x160_p1/repeat_1/input
    # One CPU-only I/O rank is required; allow multi-GPU probes to use one
    # compute rank per GPU plus that final I/O rank.
    TASKS=${HICAR_PROBE_TASKS:-2}
    ;;
  *) echo "unknown PROBE_PLATFORM: $PROBE_PLATFORM" >&2; exit 2 ;;
esac

test -x "$EXE"
test -f "$SOURCE_INPUT/run.nml"
test -f "$HICAR_ROOT/run/NoahmpTable.TBL"
test -d "$HICAR_ROOT/run/rrtmgp_support"
test -d "$HICAR_ROOT/run/mp_support"

RUN=$PROBE_ROOT/$PROBE_PLATFORM
INPUT=$RUN/input
OUT=$RUN/output
RESTART=$RUN/restart
rm -rf "$RUN"
mkdir -p "$INPUT" "$OUT" "$RESTART" "$RUN/logs"
cp "$SOURCE_INPUT/run.nml" "$INPUT/run.nml"
sed -i \
  -e "s#end_date = '2026-07-11 00:00:00'#end_date = '$PROBE_END_DATE'#" \
  -e "s#outputinterval = 3600#outputinterval = $PROBE_OUTPUT_INTERVAL#" \
  -e "s#restart_folder = '.*'#restart_folder = '$RESTART/'#" \
  -e "s#output_folder = '.*'#output_folder = '$OUT/'#" \
  "$INPUT/run.nml"
if [[ -n $PROBE_RRTMGP_BLOCK_N ]]; then
  [[ $PROBE_RRTMGP_BLOCK_N =~ ^[1-9][0-9]*$ ]] || {
    echo "HICAR_PROBE_RRTMGP_BLOCK_N must be a positive integer" >&2
    exit 2
  }
  perl -0pi -e "s#(&rad_parameters\\s*\\n)/#\$1  rrtmgp_block_N = $PROBE_RRTMGP_BLOCK_N\\n/#" "$INPUT/run.nml"
fi
cp -f "$HICAR_ROOT/run/NoahmpTable.TBL" "$INPUT/"
cp -a "$HICAR_ROOT/run/rrtmgp_support" "$INPUT/"
cp -a "$HICAR_ROOT/run/mp_support" "$INPUT/"
git -C "$HICAR_ROOT" rev-parse HEAD > "$RUN/git_sha.txt"
sha256sum "$EXE" > "$RUN/executable.sha256"
scontrol show job "$SLURM_JOB_ID" > "$RUN/slurm.txt"

export HICAR_IO_PER_NODE=1 HICAR_TASKS_PER_NODE=$TASKS OMP_NUM_THREADS=1
cd "$INPUT"
if [[ $PROBE_PLATFORM == gpu ]]; then
  export MPICH_GPU_SUPPORT_ENABLED=0 MPICH_GPU_MANAGED_MEMORY_SUPPORT_ENABLED=0
  srun -u --distribution=block:block --cpu-bind=none \
    "$BENCH_ROOT/scripts/gpu_rank_wrapper.sh" "$EXE" "$INPUT/run.nml" \
    | tee "$RUN/logs/hicar_${SLURM_JOB_ID}.out"
else
  srun -u --cpu-bind=cores "$EXE" "$INPUT/run.nml" \
    | tee "$RUN/logs/hicar_${SLURM_JOB_ID}.out"
fi
! grep -Eq 'HICAR BiCGStab status=[[:space:]]*[1-9]' "$RUN/logs/hicar_${SLURM_JOB_ID}.out"
python3 "$BENCH_ROOT/analysis/validate_run.py" --run "$RUN" --expected-hours 1
printf 'timer/output/solver gate passed\n' > "$RUN/VALIDATED"
