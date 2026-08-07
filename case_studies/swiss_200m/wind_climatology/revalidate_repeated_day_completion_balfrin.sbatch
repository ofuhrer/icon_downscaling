#!/bin/bash -l
#SBATCH --job-name=repeat-day-revalidate
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=60G
#SBATCH --time=01:00:00
#SBATCH --output=repeat-day-revalidate-%j.out
#SBATCH --error=repeat-day-revalidate-%j.err

set -euo pipefail

: "${HICAR_REPEAT_RUNTIME_DIR:?set HICAR_REPEAT_RUNTIME_DIR}"
: "${HICAR_REPEAT_PYTHON:?set HICAR_REPEAT_PYTHON}"
: "${HICAR_REPEAT_VALIDATOR:?set HICAR_REPEAT_VALIDATOR}"
: "${HICAR_REPEAT_PLAN:?set HICAR_REPEAT_PLAN}"
: "${HICAR_REPEAT_RUN_DIR:?set HICAR_REPEAT_RUN_DIR}"
: "${HICAR_REPEAT_STATIC_FILE:?set HICAR_REPEAT_STATIC_FILE}"
: "${HICAR_REPEAT_RESTART_FILE:?set HICAR_REPEAT_RESTART_FILE}"
: "${HICAR_REPEAT_RESTART_INPUT_FILE:?set HICAR_REPEAT_RESTART_INPUT_FILE}"
: "${HICAR_REPEAT_TRANSFORM_REPORT:?set HICAR_REPEAT_TRANSFORM_REPORT}"
: "${HICAR_REPEAT_COMPATIBILITY_REPORT:?set HICAR_REPEAT_COMPATIBILITY_REPORT}"
: "${HICAR_REPEAT_EXECUTABLE:?set HICAR_REPEAT_EXECUTABLE}"
: "${HICAR_REPEAT_FORCING_PUBLICATION:?set HICAR_REPEAT_FORCING_PUBLICATION}"

report="${HICAR_REPEAT_RUN_DIR}/model_chunk_completion.json"
if test -f "${report}.ready"; then
  echo "refusing to replace a ready model completion: ${report}" >&2
  exit 2
fi

if ! test -f "${HICAR_REPEAT_COMPATIBILITY_REPORT}.ready"; then
  "${HICAR_REPEAT_PYTHON}" \
    "${HICAR_REPEAT_RUNTIME_DIR}/publish_repeated_day_restart_compatibility.py" \
    --transform "${HICAR_REPEAT_TRANSFORM_REPORT}" \
    --restart "${HICAR_REPEAT_RESTART_INPUT_FILE}" \
    --output "${HICAR_REPEAT_COMPATIBILITY_REPORT}"
fi

mapfile -t output_files < <(find "${HICAR_REPEAT_RUN_DIR}/output" \
  -maxdepth 1 -type f -name '*.nc' -print | sort)
test "${#output_files[@]}" -gt 0

"${HICAR_REPEAT_PYTHON}" "${HICAR_REPEAT_VALIDATOR}" \
  --plan "${HICAR_REPEAT_PLAN}" \
  --run-dir "${HICAR_REPEAT_RUN_DIR}" \
  --static-file "${HICAR_REPEAT_STATIC_FILE}" \
  --output-file "${output_files[@]}" \
  --restart-file "${HICAR_REPEAT_RESTART_FILE}" \
  --model-log "${HICAR_REPEAT_RUN_DIR}/model.out" \
  --source-commit-file "${HICAR_REPEAT_RUN_DIR}/source_commit.txt" \
  --source-tree-status-file "${HICAR_REPEAT_RUN_DIR}/source_tree_status.txt" \
  --executable "${HICAR_REPEAT_EXECUTABLE}" \
  --executable-digest-file "${HICAR_REPEAT_RUN_DIR}/executable.sha256" \
  --forcing-publication "${HICAR_REPEAT_FORCING_PUBLICATION}" \
  --archived-plan "${HICAR_REPEAT_RUN_DIR}/chunk_plan.json" \
  --archived-forcing-publication "${HICAR_REPEAT_RUN_DIR}/forcing_publication.json" \
  --output-profile wind_climatology \
  --output-interval-seconds 1800 \
  --restart-continuation \
  --restart-input-file "${HICAR_REPEAT_RESTART_INPUT_FILE}" \
  --restart-input-report "${HICAR_REPEAT_COMPATIBILITY_REPORT}" \
  --report "${report}"
