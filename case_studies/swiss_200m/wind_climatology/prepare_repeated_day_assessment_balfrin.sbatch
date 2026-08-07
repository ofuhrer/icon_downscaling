#!/bin/bash -l
#SBATCH --job-name=repeat-day-manifest
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=2G
#SBATCH --time=00:10:00
#SBATCH --output=repeat-day-manifest-%j.out
#SBATCH --error=repeat-day-manifest-%j.err

set -euo pipefail

: "${HICAR_REPEAT_RUNTIME_DIR:?set HICAR_REPEAT_RUNTIME_DIR}"
: "${HICAR_REPEAT_ROOT:?set HICAR_REPEAT_ROOT}"
: "${HICAR_REPEAT_CYCLE_COUNT:?set HICAR_REPEAT_CYCLE_COUNT}"
: "${HICAR_REPEAT_CYCLE_ONE_COMPLETION:?set HICAR_REPEAT_CYCLE_ONE_COMPLETION}"
: "${HICAR_REPEAT_EXPERIMENT:?set HICAR_REPEAT_EXPERIMENT}"
: "${HICAR_REPEAT_MANIFEST:?set HICAR_REPEAT_MANIFEST}"
: "${HICAR_REPEAT_PYTHON:?set HICAR_REPEAT_PYTHON}"

args=(
  --cycle-root "${HICAR_REPEAT_ROOT}"
  --cycle-count "${HICAR_REPEAT_CYCLE_COUNT}"
  --cycle-one-completion "${HICAR_REPEAT_CYCLE_ONE_COMPLETION}"
  --experiment "${HICAR_REPEAT_EXPERIMENT}"
  --output "${HICAR_REPEAT_MANIFEST}"
)
if test -n "${HICAR_REPEAT_CYCLE_ONE_RESULTS:-}"; then
  args+=(
    --cycle-one-results "${HICAR_REPEAT_CYCLE_ONE_RESULTS}"
    --cycle-one-run-id "${HICAR_REPEAT_CYCLE_ONE_RUN_ID:?set HICAR_REPEAT_CYCLE_ONE_RUN_ID}"
  )
fi

"${HICAR_REPEAT_PYTHON}" \
  "${HICAR_REPEAT_RUNTIME_DIR}/prepare_repeated_day_assessment.py" \
  "${args[@]}"
