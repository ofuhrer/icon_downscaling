#!/bin/bash -l
#SBATCH --job-name=assess-wind-fix
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --time=00:30:00

set -euo pipefail

python=${HICAR_VALIDATION_PYTHON:?Set HICAR_VALIDATION_PYTHON}
assessor=${HICAR_WIND_FIX_ASSESSOR:?Set HICAR_WIND_FIX_ASSESSOR}
root=${HICAR_WIND_FIX_ROOT:?Set HICAR_WIND_FIX_ROOT}

"$python" "$assessor" \
  --qualification-root "$root" \
  --continuous-job-id "${CONTINUOUS_JOB_ID:?Set CONTINUOUS_JOB_ID}" \
  --cross-node-continuation-job-id \
    "${CROSS_NODE_CONTINUATION_JOB_ID:?Set CROSS_NODE_CONTINUATION_JOB_ID}" \
  --tolerance-spec "${RESTART_TOLERANCE_SPEC:?Set RESTART_TOLERANCE_SPEC}" \
  --output "$root/wind_tendency_fix_qualification.json"
