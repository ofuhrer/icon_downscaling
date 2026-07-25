#!/bin/bash -l
# Compare every routine diagnostic in 100 m history and exact-end restart.
#SBATCH --job-name=hicar-100m-restart
#SBATCH --partition=pp-long
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=32G
#SBATCH --time=01:00:00

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

plan=${CAPACITY_GATE_PLAN:?Set CAPACITY_GATE_PLAN}
repo_root=${REPO_ROOT:-$SCRATCH/icon_hicar}
python=${HICAR_VALIDATION_PYTHON:-$repo_root/venv_static/bin/python}
comparator=$repo_root/case_studies/swiss_200m/streaming/compare_restart_boundary.py
for path in "$plan" "$plan.ready" "$python" "$comparator"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done

IFS=$'\t' read -r completion restart boundary report < <(
  "$python" - "$plan" <<'PY'
import json
import sys
p = json.load(open(sys.argv[1]))
initial, continuation = p["segments"]
print(
    initial["completion_report"],
    initial["expected_restart_file"],
    continuation["start"],
    p["boundary_comparison_report"],
    sep="\t",
)
PY
)
for path in "$completion" "$completion.ready" "$restart"; do
  test -e "$path" || { echo "missing required result: $path" >&2; exit 2; }
done
before=$(
  "$python" - "$completion" <<'PY'
import json
import sys
p = json.load(open(sys.argv[1]))
if p.get("status") != "PASS":
    raise SystemExit("initial completion is not PASS")
paths = [item["path"] for item in p["output"]["files"]]
if len(paths) != 1:
    raise SystemExit("capacity segment must have exactly one history file")
print(paths[0])
PY
)

if ! test -f "$report.ready"; then
  "$python" "$comparator" \
    --before "$before" \
    --after "$restart" \
    --boundary "$boundary" \
    --atol 1e-6 \
    --rtol 1e-6 \
    --report "$report"
fi
test -f "$report.ready"
echo "PASS: 100 m restart boundary comparison published at $report"
