#!/bin/bash -l
# Run class-aware physical budgets and REA-L driver-consistency validation.
#SBATCH --job-name=hicar-event-validate
#SBATCH --partition=pp-long
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=128G
#SBATCH --time=04:00:00

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

plan=${STREAM_PLAN:?Set STREAM_PLAN to the event chunk_plan.json}
run=${EVENT_RUN_DIR:?Set EVENT_RUN_DIR to the validated HICAR run}
repo_root=${REPO_ROOT:-$SCRATCH/icon_hicar}
python=${HICAR_VALIDATION_PYTHON:-$repo_root/venv_static/bin/python}
case_root=$repo_root/case_studies/swiss_200m
evaluator=$case_root/validation/evaluate_scientific_event.py
comparator=$case_root/validation/compare_hicar_to_rea_l_surface.py
static_file=${HICAR_STATIC_FILE:?Set HICAR_STATIC_FILE to the event static domain}
reference_list="$(dirname "$plan")/reference/reference_list.txt"
model_completion="$run/model_chunk_completion.json"
validation_dir="$run/scientific_validation"
mkdir -p "$validation_dir"

for path in "$plan" "$plan.ready" "$run" "$model_completion" \
  "$model_completion.ready" "$python" "$evaluator" "$comparator" \
  "$static_file" "$static_file.ready" "$reference_list" "$reference_list.ready"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done

IFS=$'\t' read -r event_name start end interval < <(
  "$python" - "$plan" "$model_completion" <<'PY'
import json
import sys
plan = json.load(open(sys.argv[1]))
completion = json.load(open(sys.argv[2]))
if plan.get("status") != "PLANNED":
    raise SystemExit("event plan is not PLANNED")
if completion.get("status") != "PASS":
    raise SystemExit("model completion is not PASS")
print(
    plan["chunk_id"],
    plan["start"],
    plan["end"],
    completion["output_interval_seconds"],
    sep="\t",
)
PY
)

shopt -s nullglob
output_files=("$run/output"/*.nc)
shopt -u nullglob
test "${#output_files[@]}" -gt 0 || {
  echo "no HICAR output files under $run/output" >&2
  exit 2
}

budget_report="$validation_dir/scientific_event_diagnostics.json"
source_report="$validation_dir/rea_l_source_comparison.json"
if ! test -f "$budget_report.ready"; then
  "$python" "$evaluator" \
    --event-name "$event_name" \
    --static-file "$static_file" \
    --output-file "${output_files[@]}" \
    --expected-start "$start" \
    --expected-end "$end" \
    --expected-interval-seconds "$interval" \
    --report "$budget_report"
fi
test -f "$budget_report.ready"

if ! test -f "$source_report.ready"; then
  comparator_args=()
  for file in "${output_files[@]}"; do
    comparator_args+=(--output-file "$file")
  done
  "$python" "$comparator" \
    --event-name "$event_name" \
    --static-file "$static_file" \
    "${comparator_args[@]}" \
    --reference-list "$reference_list" \
    --report "$source_report"
fi
test -f "$source_report.ready"
echo "PASS: event scientific validation published under $validation_dir"
