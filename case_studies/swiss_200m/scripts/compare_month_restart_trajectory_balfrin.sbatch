#!/bin/bash -l
# Compare the month pilot across its restart with the uninterrupted overlap.
#SBATCH --job-name=hicar-month-restart
#SBATCH --partition=pp-long
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=128G
#SBATCH --time=04:00:00

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

repo_root=${REPO_ROOT:-$SCRATCH/icon_hicar}
python=${HICAR_VALIDATION_PYTHON:-$repo_root/venv_static/bin/python}
month_plan=${MONTH_PILOT_PLAN:?Set MONTH_PILOT_PLAN to month_pilot_plan.json}
scientific_plan=${SCIENTIFIC_PILOT_PLAN:-$repo_root/case_studies/swiss_200m/config/scientific_pilot_plan.json}
comparator=$repo_root/case_studies/swiss_200m/streaming/compare_segmented_to_uninterrupted.py
report=${MONTH_RESTART_REPORT:-$(dirname "$month_plan")/restart_trajectory_comparison.json}

for path in "$python" "$month_plan" "$month_plan.ready" "$scientific_plan" \
  "$comparator"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done

IFS=$'\t' read -r before after reference start end < <(
  "$python" - "$month_plan" <<'PY'
import json
import pathlib
import sys

plan = json.load(open(sys.argv[1]))
if plan.get("status") != "PLANNED":
    raise SystemExit("month pilot plan is not PLANNED")
segments = plan["segments"]
overlap = plan["uninterrupted_restart_overlap"]
if len(segments) < 3:
    raise SystemExit("month pilot has fewer than three segments")
print(
    pathlib.Path(segments[1]["run_dir"]) / "model_chunk_completion.json",
    pathlib.Path(segments[2]["run_dir"]) / "model_chunk_completion.json",
    pathlib.Path(overlap["run_dir"]) / "model_chunk_completion.json",
    overlap["comparison_start"],
    overlap["comparison_end"],
    sep="\t",
)
PY
)

for path in "$before" "$before.ready" "$after" "$after.ready" \
  "$reference" "$reference.ready"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done

"$python" "$comparator" \
  --segmented-completion "$before" \
  --segmented-completion "$after" \
  --reference-completion "$reference" \
  --scientific-plan "$scientific_plan" \
  --start "$start" \
  --end "$end" \
  --report "$report"
test -f "$report.ready"
echo "PASS: month restart trajectory comparison published at $report"
