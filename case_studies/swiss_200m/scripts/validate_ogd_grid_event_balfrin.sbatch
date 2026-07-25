#!/bin/bash -l
# Compare a completed HICAR event and its REA-L driver with public OGD grids.
#SBATCH --job-name=hicar-ogd-validate
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
comparator=$case_root/validation/compare_hicar_rea_l_to_ogd_grids.py
static_file=${HICAR_STATIC_FILE:?Set HICAR_STATIC_FILE to the event static domain}
ogd_manifest=${OGD_REFERENCE_MANIFEST:-$repo_root/observations/ogd_grid/2020/ogd_grid_reference_2020_events.json}
event_root=$(dirname "$plan")
reference_list="$event_root/reference/reference_list.txt"
model_completion="$run/model_chunk_completion.json"
validation_dir="$run/scientific_validation"
report="$validation_dir/ogd_grid_comparison.json"
mkdir -p "$validation_dir"

for path in "$plan" "$plan.ready" "$run" "$model_completion" \
  "$model_completion.ready" "$python" "$comparator" "$static_file" \
  "$static_file.ready" "$reference_list" "$reference_list.ready" \
  "$ogd_manifest" "$ogd_manifest.ready"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done

IFS=$'\t' read -r event_name event_month < <(
  "$python" - "$plan" "$model_completion" "$ogd_manifest" <<'PY'
import datetime as dt
import json
import pathlib
import sys

plan = json.load(open(sys.argv[1]))
completion = json.load(open(sys.argv[2]))
manifest = json.load(open(sys.argv[3]))
if plan.get("status") != "PLANNED":
    raise SystemExit("event plan is not PLANNED")
if completion.get("status") != "PASS":
    raise SystemExit("model completion is not PASS")
if manifest.get("status") != "PASS":
    raise SystemExit("OGD reference manifest is not PASS")
month = dt.datetime.fromisoformat(plan["start"]).month
for asset in manifest.get("assets", []):
    if asset["product"] in {"rhiresd", "tabsd"} or asset.get("month") == month:
        path = pathlib.Path(asset["path"])
        if not path.is_file() or not pathlib.Path(f"{path}.ready").is_file():
            raise SystemExit(f"OGD asset is not published: {path}")
print(plan["chunk_id"], month, sep="\t")
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
    --ogd-manifest "$ogd_manifest" \
    --report "$report"
fi
test -f "$report.ready"
echo "PASS: event month $event_month OGD-grid comparison published at $report"
