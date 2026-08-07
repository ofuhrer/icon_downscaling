#!/usr/bin/env bash
#SBATCH --job-name=wind-mech-final
#SBATCH --partition=pp-short
#SBATCH --time=00:20:00
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4

set -euo pipefail
: "${HICAR_MECHANISM_DIR:?set HICAR_MECHANISM_DIR}"
: "${HICAR_FORCING_COMPARISON:?set HICAR_FORCING_COMPARISON}"
: "${HICAR_PATHWAY_ASSESSMENT:?set HICAR_PATHWAY_ASSESSMENT}"
: "${HICAR_MECHANISM_CONCLUSION:?set HICAR_MECHANISM_CONCLUSION}"
: "${HICAR_ANALYSIS_RUNTIME:?set HICAR_ANALYSIS_RUNTIME}"
: "${HICAR_ANALYSIS_PYTHON:?set HICAR_ANALYSIS_PYTHON}"

exec "$HICAR_ANALYSIS_PYTHON" \
  "$HICAR_ANALYSIS_RUNTIME/case_studies/swiss_200m/wind_climatology/finalize_wind_mechanism_assessment.py" \
  --mechanism-dir "$HICAR_MECHANISM_DIR" \
  --forcing-comparison "$HICAR_FORCING_COMPARISON" \
  --pathway-assessment "$HICAR_PATHWAY_ASSESSMENT" \
  --output "$HICAR_MECHANISM_CONCLUSION"
