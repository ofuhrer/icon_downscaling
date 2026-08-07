#!/usr/bin/env bash
#SBATCH --job-name=wind-mechanism
#SBATCH --partition=pp-short
#SBATCH --time=00:59:00
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --array=0-3%4
#SBATCH --output=wind-mechanism-%A_%a.out
#SBATCH --error=wind-mechanism-%A_%a.err

set -euo pipefail

: "${HICAR_CAMPAIGN_ROOT:?set HICAR_CAMPAIGN_ROOT}"
: "${HICAR_ANALYSIS_RUNTIME:?set HICAR_ANALYSIS_RUNTIME}"
: "${HICAR_ANALYSIS_PYTHON:?set HICAR_ANALYSIS_PYTHON}"

cases=(
  summer-convective
  winter-stable
  sabine-strong-wind
  plateau-inversion
)
case_id=${cases[SLURM_ARRAY_TASK_ID]}
script="$HICAR_ANALYSIS_RUNTIME/case_studies/swiss_200m/wind_climatology/assess_wind_spinup_mechanism.py"
results="$HICAR_CAMPAIGN_ROOT/analysis/wind_spinup_results.json"
output="$HICAR_CAMPAIGN_ROOT/analysis/mechanism-v1/${case_id}.json"

exec "$HICAR_ANALYSIS_PYTHON" "$script" \
  --campaign-root "$HICAR_CAMPAIGN_ROOT" \
  --results "$results" \
  --case-id "$case_id" \
  --output "$output"
