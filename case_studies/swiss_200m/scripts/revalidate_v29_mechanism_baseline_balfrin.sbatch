#!/bin/bash -l
# Revalidate an already-completed V29 mechanism output; never runs HICAR.
#SBATCH --job-name=hicar-v29-mech-revalidate
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=12G
#SBATCH --time=01:00:00

set -euo pipefail

python=${HICAR_VALIDATION_PYTHON:?Set immutable validation Python}
validator=${HICAR_MODEL_VALIDATOR:?Set immutable model validator}
plan=${V29_PLAN:?Set frozen forcing plan}
run=${V29_RUN_DIR:?Set completed V29 run directory}
static_file=${V29_STATIC_FILE:?Set published static domain}
forcing_publication=${V29_FORCING_PUBLICATION:?Set forcing publication}
executable=${V29_EXECUTABLE:?Set HICAR executable}
report=${V29_REVALIDATION_REPORT:?Set new publication report path}

for path in "$python" "$validator" "$plan" "$run/chunk_plan.json" \
            "$run/forcing_publication.json" "$static_file" "$static_file.ready" \
            "$forcing_publication" "$forcing_publication.ready" "$executable" \
            "$run/executable.sha256" "$run/model.out" "$run/source_commit.txt" \
            "$run/source_tree_status.txt"; do
  test -e "$path" || { echo "missing revalidation input: $path" >&2; exit 2; }
done
test ! -e "$report" || { echo "revalidation report already exists: $report" >&2; exit 2; }
test ! -e "$report.ready" || { echo "revalidation ready marker already exists: $report.ready" >&2; exit 2; }

shopt -s nullglob
output=("$run/output"/*.nc)
restart=("$run/restart"/*.nc)
shopt -u nullglob
test "${#output[@]}" -gt 0 || { echo "no existing model output" >&2; exit 2; }
test "${#restart[@]}" -eq 1 || { echo "expected exactly one existing restart" >&2; exit 2; }

"$python" "$validator" \
  --plan "$plan" \
  --run-dir "$run" \
  --static-file "$static_file" \
  --output-file "${output[@]}" \
  --restart-file "${restart[0]}" \
  --model-log "$run/model.out" \
  --source-commit-file "$run/source_commit.txt" \
  --source-tree-status-file "$run/source_tree_status.txt" \
  --executable "$executable" \
  --executable-digest-file "$run/executable.sha256" \
  --forcing-publication "$forcing_publication" \
  --archived-plan "$run/chunk_plan.json" \
  --archived-forcing-publication "$run/forcing_publication.json" \
  --output-profile mechanism_diagnosis \
  --output-interval-seconds 10800 \
  --report "$report"

test -s "$report"
test -f "$report.ready"
