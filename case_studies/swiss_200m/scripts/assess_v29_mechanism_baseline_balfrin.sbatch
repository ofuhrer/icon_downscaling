#!/bin/bash -l
# Publish the non-promoting mechanism assessment after the diagnostic model run.
# This script performs no HICAR execution and never authorizes a rerun itself.
#SBATCH --job-name=hicar-v29-mech-assess
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --time=00:30:00

set -euo pipefail

python=${HICAR_VALIDATION_PYTHON:?Set the immutable validation Python}
assessor=${V29_MECHANISM_ASSESSOR:?Set the immutable mechanism assessor}
contract=${V29_MECHANISM_CONTRACT:?Set the immutable mechanism contract}
static_file=${V29_STATIC_FILE:?Set the published static file}
history_dir=${V29_HISTORY_DIR:?Set the validated model output directory}
reference_dir=${V29_REFERENCE_DIR:?Set the extracted published REA-L sidecars}
report=${V29_MECHANISM_REPORT:?Set the assessment report path}
model_report=${V29_MODEL_REPORT:?Set the validated model completion report}

for path in "$python" "$assessor" "$contract" "$static_file" "$static_file.ready" \
            "$reference_dir" "$model_report" "$model_report.ready"; do
  test -e "$path" || { echo "missing assessment input: $path" >&2; exit 2; }
done

"$python" - "$model_report" <<'PY'
import json
import sys
if json.load(open(sys.argv[1])).get("status") != "PASS":
    raise SystemExit("model completion report is not PASS")
PY

shopt -s nullglob
history=("$history_dir"/*.nc)
shopt -u nullglob
test "${#history[@]}" -gt 0 || { echo "no HICAR history files in $history_dir" >&2; exit 2; }

history_args=()
for file in "${history[@]}"; do
  history_args+=(--hicar-history "$file")
done

"$python" "$assessor" \
  --contract "$contract" \
  --static-file "$static_file" \
  "${history_args[@]}" \
  --reference-dir "$reference_dir" \
  --report "$report"

test -s "$report"
test -f "$report.ready"
