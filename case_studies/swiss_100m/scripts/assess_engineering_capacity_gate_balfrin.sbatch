#!/bin/bash -l
# Collect accounting and apply the frozen national 100 m capacity verdict.
#SBATCH --job-name=hicar-100m-verdict
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=00:15:00

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

plan=${CAPACITY_GATE_PLAN:?Set CAPACITY_GATE_PLAN}
repo_root=${REPO_ROOT:-$SCRATCH/icon_hicar}
python=${HICAR_VALIDATION_PYTHON:-$repo_root/venv_static/bin/python}
assessor=$repo_root/case_studies/swiss_100m/validation/assess_engineering_capacity_gate.py

declare -a labels=(
  initial_forcing initial_forcing_finalize initial_model initial_solver
  continuation_forcing continuation_forcing_finalize continuation_model
  continuation_solver restart_boundary
)
declare -a variables=(
  INITIAL_FORCING_JOB INITIAL_FORCING_FINALIZE_JOB INITIAL_MODEL_JOB
  INITIAL_SOLVER_JOB CONTINUATION_FORCING_JOB
  CONTINUATION_FORCING_FINALIZE_JOB CONTINUATION_MODEL_JOB
  CONTINUATION_SOLVER_JOB RESTART_BOUNDARY_JOB
)
declare -a ids=()
declare -a job_args=()
for index in "${!labels[@]}"; do
  variable=${variables[$index]}
  value=${!variable:-}
  [[ "$value" =~ ^[0-9]+$ ]] || {
    echo "missing numeric $variable" >&2
    exit 2
  }
  ids+=("$value")
  job_args+=(--job "${labels[$index]}=$value")
done

for path in "$plan" "$plan.ready" "$python" "$assessor"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done
IFS=$'\t' read -r accounting report < <(
  "$python" - "$plan" <<'PY'
import json
import sys
p = json.load(open(sys.argv[1]))
print(p["accounting_report"], p["capacity_assessment_report"], sep="\t")
PY
)
mkdir -p "$(dirname "$accounting")"
joined=$(IFS=,; echo "${ids[*]}")
sacct -P -X -j "$joined" \
  --format=JobIDRaw,JobName,State,ElapsedRaw,AllocTRES,Start,End > "$accounting"
test -s "$accounting"

"$python" "$assessor" \
  --plan "$plan" \
  --accounting "$accounting" \
  "${job_args[@]}" \
  --report "$report"
test -f "$report.ready"
echo "PASS: 100 m engineering-capacity verdict published at $report"
