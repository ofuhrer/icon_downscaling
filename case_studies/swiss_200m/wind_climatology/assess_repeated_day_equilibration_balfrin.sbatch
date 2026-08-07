#!/bin/bash -l
#SBATCH --job-name=repeat-day-assess
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=60G
#SBATCH --time=01:00:00
#SBATCH --output=repeat-day-assess-%j.out
#SBATCH --error=repeat-day-assess-%j.err

set -euo pipefail

: "${HICAR_REPEAT_RUNTIME_DIR:?set HICAR_REPEAT_RUNTIME_DIR}"
: "${HICAR_REPEAT_MANIFEST:?set HICAR_REPEAT_MANIFEST}"
: "${HICAR_REPEAT_OUTPUT:?set HICAR_REPEAT_OUTPUT}"
: "${HICAR_REPEAT_PYTHON:?set HICAR_REPEAT_PYTHON}"

"${HICAR_REPEAT_PYTHON}" \
  "${HICAR_REPEAT_RUNTIME_DIR}/assess_repeated_day_equilibration.py" \
  --manifest "${HICAR_REPEAT_MANIFEST}" \
  --output "${HICAR_REPEAT_OUTPUT}"
