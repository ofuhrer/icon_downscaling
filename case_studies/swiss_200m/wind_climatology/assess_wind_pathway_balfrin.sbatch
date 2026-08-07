#!/usr/bin/env bash
#SBATCH --job-name=wind-path-assess
#SBATCH --partition=pp-short
#SBATCH --time=00:59:00
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8

set -euo pipefail
: "${HICAR_EXPERIMENT_PLAN:?set HICAR_EXPERIMENT_PLAN}"
: "${HICAR_ANALYSIS_RUNTIME:?set HICAR_ANALYSIS_RUNTIME}"
: "${HICAR_ANALYSIS_PYTHON:?set HICAR_ANALYSIS_PYTHON}"
: "${HICAR_PATHWAY_REPORT:?set HICAR_PATHWAY_REPORT}"

exec "$HICAR_ANALYSIS_PYTHON" \
  "$HICAR_ANALYSIS_RUNTIME/case_studies/swiss_200m/wind_climatology/assess_wind_pathway_experiment.py" \
  --experiment-plan "$HICAR_EXPERIMENT_PLAN" \
  --mechanism-script \
  "$HICAR_ANALYSIS_RUNTIME/case_studies/swiss_200m/wind_climatology/assess_wind_spinup_mechanism.py" \
  --output "$HICAR_PATHWAY_REPORT"
