#!/bin/bash -l
# Publish a hash-verified time subset of an existing forcing chunk.
#SBATCH --job-name=hicar-reuse-forcing
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=00:20:00

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

repo_root=${REPO_ROOT:-$SCRATCH/icon_hicar}
python=${HICAR_VALIDATION_PYTHON:-$repo_root/venv_static/bin/python}
publisher=$repo_root/case_studies/swiss_200m/streaming/reuse_published_forcing_chunk.py

"$python" "$publisher" \
  --source-plan "${SOURCE_STREAM_PLAN:?Set SOURCE_STREAM_PLAN}" \
  --source-publication "${SOURCE_FORCING_PUBLICATION:?Set SOURCE_FORCING_PUBLICATION}" \
  --start "${REUSE_START:?Set REUSE_START}" \
  --end "${REUSE_END:?Set REUSE_END}" \
  --chunk-id "${REUSE_CHUNK_ID:?Set REUSE_CHUNK_ID}" \
  --chunk-root "${REUSE_CHUNK_ROOT:?Set REUSE_CHUNK_ROOT}"
