#!/usr/bin/env bash
set -euo pipefail
ROOT=${SCALING_ROOT:?}; PREFLIGHT_JOB=${1:?usage: submit_arrays_after_preflight.sh PREFLIGHT_JOB_ID}
for array in "$ROOT"/arrays/*.sbatch; do
  sbatch --dependency="afterok:${PREFLIGHT_JOB}" "$array"
done
