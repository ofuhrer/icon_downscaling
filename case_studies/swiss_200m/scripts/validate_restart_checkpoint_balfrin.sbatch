#!/bin/bash -l
# Validate one completed HICAR restart checkpoint on a post-processing node.
#SBATCH --job-name=hicar-restart-check
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --time=00:20:00

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

repo_root=${REPO_ROOT:-$SCRATCH/icon_hicar}
python=${HICAR_VALIDATION_PYTHON:-$repo_root/venv_static/bin/python}
validator=$repo_root/case_studies/swiss_200m/validation/validate_restart_checkpoint.py
checkpoint=${RESTART_CHECKPOINT:?Set RESTART_CHECKPOINT}
expected_time=${RESTART_EXPECTED_TIME:?Set RESTART_EXPECTED_TIME}
report=${RESTART_CHECKPOINT_REPORT:?Set RESTART_CHECKPOINT_REPORT}

test -x "$python"
test -f "$validator"
test -s "$checkpoint"

args=(
  --checkpoint "$checkpoint"
  --expected-time "$expected_time"
  --report "$report"
)
if test -n "${HICAR_EXPECTED_COMMIT:-}"; then
  args+=(--expected-source-commit "$HICAR_EXPECTED_COMMIT")
fi

"$python" "$validator" "${args[@]}"
test -s "$report.ready"
