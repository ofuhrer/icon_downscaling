#!/bin/bash -l
# Validate all 24-hour restart boundaries of one completed 72-hour event.
#SBATCH --job-name=hicar-event-restarts
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
validator=$repo_root/case_studies/swiss_200m/validation/validate_event_restart_checkpoints.py
run=${EVENT_RUN_DIR:?Set EVENT_RUN_DIR}
static_basename=${HICAR_STATIC_BASENAME:?Set HICAR_STATIC_BASENAME}
start=${EVENT_START:?Set EVENT_START}
duration=${EVENT_DURATION_HOURS:-72}
interval=${EVENT_RESTART_INTERVAL_HOURS:-24}
expected_commit=${HICAR_EXPECTED_COMMIT:?Set HICAR_EXPECTED_COMMIT}
report=${EVENT_RESTART_REPORT:-$run/scientific_validation/restart_checkpoint_diagnostics.json}

test -x "$python"
test -f "$validator"
test -d "$run"

"$python" "$validator" \
  --run-dir "$run" \
  --static-basename "$static_basename" \
  --start "$start" \
  --duration-hours "$duration" \
  --interval-hours "$interval" \
  --expected-source-commit "$expected_commit" \
  --report "$report"
test -s "$report.ready"
