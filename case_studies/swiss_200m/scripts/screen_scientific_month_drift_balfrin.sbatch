#!/bin/bash -l
# Apply the frozen post-spinup drift screen to whole-month physical diagnostics.
#SBATCH --job-name=hicar-month-drift
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=2G
#SBATCH --time=00:10:00

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

month_plan=${MONTH_PILOT_PLAN:?Set MONTH_PILOT_PLAN}
repo_root=${REPO_ROOT:-$SCRATCH/icon_hicar}
python=${HICAR_VALIDATION_PYTHON:-$repo_root/venv_static/bin/python}
screen=$repo_root/case_studies/swiss_200m/validation/screen_scientific_month_drift.py

IFS=$'\t' read -r scientific_plan diagnostics report < <(
  "$python" - "$month_plan" <<'PY'
import json
import sys
p = json.load(open(sys.argv[1]))
print(
    p["scientific_plan"],
    p["validation_reports"]["physical"],
    p["validation_reports"]["drift_screen"],
    sep="\t",
)
PY
)
for path in "$month_plan" "$month_plan.ready" "$scientific_plan" \
            "$diagnostics" "$diagnostics.ready" "$screen"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done

"$python" "$screen" \
  --month-plan "$month_plan" \
  --scientific-plan "$scientific_plan" \
  --diagnostics "$diagnostics" \
  --report "$report"
test -f "$report.ready"
echo "PASS: month drift screen published at $report"
