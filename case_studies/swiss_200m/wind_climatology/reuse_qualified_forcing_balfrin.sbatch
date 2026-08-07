#!/bin/bash -l
#SBATCH --job-name=reuse-hicar-forcing
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=8G
#SBATCH --time=01:00:00

set -euo pipefail

: "${HICAR_REUSE_PYTHON:?set HICAR_REUSE_PYTHON}"
: "${HICAR_REUSE_SCRIPT:?set HICAR_REUSE_SCRIPT}"
: "${HICAR_SOURCE_PLAN:?set HICAR_SOURCE_PLAN}"
: "${HICAR_SOURCE_PUBLICATION:?set HICAR_SOURCE_PUBLICATION}"
: "${HICAR_REUSE_START:?set HICAR_REUSE_START}"
: "${HICAR_REUSE_END:?set HICAR_REUSE_END}"
: "${HICAR_REUSE_CHUNK_ID:?set HICAR_REUSE_CHUNK_ID}"
: "${HICAR_REUSE_CHUNK_ROOT:?set HICAR_REUSE_CHUNK_ROOT}"

exec "${HICAR_REUSE_PYTHON}" "${HICAR_REUSE_SCRIPT}" \
  --source-plan "${HICAR_SOURCE_PLAN}" \
  --source-publication "${HICAR_SOURCE_PUBLICATION}" \
  --start "${HICAR_REUSE_START}" \
  --end "${HICAR_REUSE_END}" \
  --chunk-id "${HICAR_REUSE_CHUNK_ID}" \
  --chunk-root "${HICAR_REUSE_CHUNK_ROOT}" \
  --producer-concurrency 1
