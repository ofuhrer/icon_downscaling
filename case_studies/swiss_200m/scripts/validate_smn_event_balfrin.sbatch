#!/bin/bash -l
# Compare a completed HICAR event and its REA-L driver with SwissMetNet.
#SBATCH --job-name=hicar-smn-validate
#SBATCH --partition=pp-long
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=64G
#SBATCH --time=02:00:00

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

plan=${STREAM_PLAN:?Set STREAM_PLAN to the event chunk_plan.json}
run=${EVENT_RUN_DIR:?Set EVENT_RUN_DIR to the validated HICAR run}
repo_root=${REPO_ROOT:-$SCRATCH/icon_hicar}
python=${HICAR_VALIDATION_PYTHON:-$repo_root/venv_static/bin/python}
case_root=$repo_root/case_studies/swiss_200m
comparator=${HICAR_SMN_COMPARATOR:-$case_root/validation/compare_hicar_rea_l_to_smn.py}
static_file=${HICAR_STATIC_FILE:?Set HICAR_STATIC_FILE to the event static domain}
event_root=$(dirname "$plan")
reference_list="$event_root/reference/reference_list.txt"
observations="$event_root/observations/swissmetnet_hourly.csv"
observation_manifest="$event_root/observations/swissmetnet_hourly.manifest.json"
model_completion="$run/model_chunk_completion.json"
validation_dir="$run/scientific_validation"
report="$validation_dir/swissmetnet_comparison.json"
mkdir -p "$validation_dir"

for path in "$plan" "$plan.ready" "$run" "$model_completion" \
  "$model_completion.ready" "$python" "$comparator" "$static_file" \
  "$static_file.ready" "$reference_list" "$reference_list.ready" \
  "$observations" "$observations.ready" "$observation_manifest" \
  "$observation_manifest.ready"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done

event_name=$(
  "$python" - "$plan" "$model_completion" "$observation_manifest" <<'PY'
import json
import sys
plan = json.load(open(sys.argv[1]))
completion = json.load(open(sys.argv[2]))
observations = json.load(open(sys.argv[3]))
if plan.get("status") != "PLANNED":
    raise SystemExit("event plan is not PLANNED")
if completion.get("status") != "PASS":
    raise SystemExit("model completion is not PASS")
if observations.get("status") != "PASS":
    raise SystemExit("observation manifest is not PASS")
if observations.get("start") != plan.get("start"):
    raise SystemExit("observation start does not match event plan")
if observations.get("end") != plan.get("end"):
    raise SystemExit("observation end does not match event plan")
print(plan["chunk_id"])
PY
)

if ! test -f "$report.ready"; then
  shopt -s nullglob
  output_files=("$run/output"/*.nc)
  shopt -u nullglob
  test "${#output_files[@]}" -gt 0 || {
    echo "no HICAR output files under $run/output" >&2
    exit 2
  }
  comparator_args=()
  for file in "${output_files[@]}"; do
    comparator_args+=(--output-file "$file")
  done
  "$python" "$comparator" \
    --event-name "$event_name" \
    --static-file "$static_file" \
    "${comparator_args[@]}" \
    --reference-list "$reference_list" \
    --observations "$observations" \
    --observation-manifest "$observation_manifest" \
    --report "$report"
fi
test -f "$report.ready"
echo "PASS: SwissMetNet comparison published at $report"
