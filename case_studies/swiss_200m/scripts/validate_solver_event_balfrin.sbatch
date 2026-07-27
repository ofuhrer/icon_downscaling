#!/bin/bash -l
# Audit every HICAR wind solve in a completed event log.
#SBATCH --job-name=hicar-solver-audit
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=2G
#SBATCH --time=00:10:00

set -euo pipefail
. "$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)/scripts/load_balfrin_site_config.sh"
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

plan=${STREAM_PLAN:?Set STREAM_PLAN to the event chunk_plan.json}
run=${EVENT_RUN_DIR:?Set EVENT_RUN_DIR to the validated HICAR run}
repo_root=${REPO_ROOT:-$SCRATCH/icon_hicar}
python=${HICAR_VALIDATION_PYTHON:-$repo_root/venv_static/bin/python}
evaluator=${HICAR_SOLVER_EVALUATOR:-$repo_root/case_studies/swiss_200m/validation/evaluate_hicar_solver_log.py}
completion="$run/model_chunk_completion.json"
model_log="$run/model.out"
report="$run/scientific_validation/solver_log_diagnostics.json"

for path in "$plan" "$plan.ready" "$run" "$completion" "$completion.ready" \
  "$model_log" "$python" "$evaluator"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done

hours=$(
  "$python" - "$plan" "$completion" <<'PY'
import json
import sys
plan = json.load(open(sys.argv[1]))
completion = json.load(open(sys.argv[2]))
if completion.get("status") != "PASS":
    raise SystemExit("model completion is not PASS")
if completion.get("chunk_id") != plan.get("chunk_id"):
    raise SystemExit("model completion does not match plan")
print(plan["hours"])
PY
)
if ! test -f "$report.ready"; then
  "$python" "$evaluator" \
    --model-log "$model_log" \
    --expected-hours "$hours" \
    --report "$report"
fi
test -f "$report.ready"
echo "PASS: solver diagnostics published at $report"
