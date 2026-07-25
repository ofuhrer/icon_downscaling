#!/usr/bin/env bash
#SBATCH --job-name=icon_hicar_prep
#SBATCH --partition=pp-long
#SBATCH --time=04:00:00
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --output=/scratch/mch/olifu/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/logs/prepare_icon_forcing_%j.out
#SBATCH --error=/scratch/mch/olifu/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/logs/prepare_icon_forcing_%j.err

set -euo pipefail

CASE_ROOT=/scratch/mch/olifu/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m
SCRIPT=$CASE_ROOT/scripts/prepare_icon_inputs.sh
ICON_DIR=$CASE_ROOT/icon_native
OUT_DIR=$CASE_ROOT/forcing
WORK_ROOT=$CASE_ROOT/work/prepare_icon_inputs
TARGET_GRID=$(cat "$CASE_ROOT/config/fieldextra_target_grid.txt")
STATIC_GRIB=$ICON_DIR/lfff00000000c

mkdir -p "$OUT_DIR" "$WORK_ROOT" "$CASE_ROOT/logs"

export OMP_NUM_THREADS=${SLURM_CPUS_PER_TASK:-4}
export OMP_STACKSIZE=${OMP_STACKSIZE:-500M}
ulimit -s unlimited 2>/dev/null || true

echo "case_root=$CASE_ROOT"
echo "target_grid=$TARGET_GRID"
echo "omp_num_threads=$OMP_NUM_THREADS"
echo "omp_stacksize=$OMP_STACKSIZE"
echo "stack_limit=$(ulimit -s)"
date -u +"start_utc=%Y-%m-%dT%H:%M:%SZ"

for step in $(seq -f "%03g" 0 33); do
  in_grib=$ICON_DIR/lfff_f$step
  out_nc=$OUT_DIR/hicar_forcing_f$step.nc
  work_dir=$WORK_ROOT/f$step
  ready_file=$out_nc.ready

  if [ -s "$out_nc" ] && [ -e "$ready_file" ]; then
    echo "skip f$step: existing output and ready marker"
    continue
  fi

  echo "prepare f$step"
  rm -f "$ready_file"
  "$SCRIPT" \
    --input "$in_grib" \
    --static "$STATIC_GRIB" \
    --output "$out_nc.tmp" \
    --target-grid "$TARGET_GRID" \
    --compression 1 \
    --work-dir "$work_dir" \
    --load-balfrin-modules

  mv "$out_nc.tmp" "$out_nc"
  printf 'step=f%s\noutput=%s\ncompleted_utc=%s\n' "$step" "$out_nc" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$ready_file"
done

date -u +"end_utc=%Y-%m-%dT%H:%M:%SZ"
