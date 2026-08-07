#!/usr/bin/env bash
#SBATCH --job-name=wind-path-forcing
#SBATCH --partition=pp-short
#SBATCH --time=00:20:00
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4

set -euo pipefail
: "${HICAR_EXPERIMENT_PLAN:?set HICAR_EXPERIMENT_PLAN}"
: "${HICAR_ANALYSIS_RUNTIME:?set HICAR_ANALYSIS_RUNTIME}"
: "${HICAR_ANALYSIS_PYTHON:?set HICAR_ANALYSIS_PYTHON}"

exec "$HICAR_ANALYSIS_PYTHON" \
  "$HICAR_ANALYSIS_RUNTIME/case_studies/swiss_200m/wind_climatology/finalize_wind_pathway_forcing.py" \
  --experiment-plan "$HICAR_EXPERIMENT_PLAN"
