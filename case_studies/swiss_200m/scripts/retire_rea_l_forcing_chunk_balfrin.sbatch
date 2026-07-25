#!/bin/bash -l
# Retire one verified forcing chunk and publish a durable retirement report.
#SBATCH --job-name=hicar-force-retire
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=00:30:00

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

plan=${STREAM_PLAN:?Set STREAM_PLAN to the published chunk plan}
run=${STREAM_RUN_DIR:?Set STREAM_RUN_DIR to the validated HICAR run}
report=${FORCING_RETIREMENT_REPORT:?Set FORCING_RETIREMENT_REPORT}
repo_root=${REPO_ROOT:-$SCRATCH/icon_hicar}
python=${HICAR_VALIDATION_PYTHON:-$repo_root/venv_static/bin/python}
retirer=$repo_root/case_studies/swiss_200m/streaming/retire_forcing_chunk.py
forcing_publication="$(dirname "$plan")/forcing_publication.json"
model_completion="$run/model_chunk_completion.json"

for path in "$python" "$retirer" "$plan" "$plan.ready" \
  "$model_completion" "$model_completion.ready"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done

if test -f "$report.ready"; then
  "$python" - "$report" "$plan" <<'PY'
import json
import sys
report = json.load(open(sys.argv[1]))
plan = json.load(open(sys.argv[2]))
if report.get("status") != "PASS" or report.get("action") != "RETIRED":
    raise SystemExit("existing forcing-retirement report is not PASS/RETIRED")
if report.get("chunk_id") != plan.get("chunk_id"):
    raise SystemExit("existing forcing-retirement report has the wrong chunk")
PY
  echo "forcing retirement already published: $report"
  exit 0
fi

for path in "$forcing_publication" "$forcing_publication.ready"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done

"$python" "$retirer" \
  --plan "$plan" \
  --forcing-publication "$forcing_publication" \
  --model-completion "$model_completion" \
  --execute \
  --report "$report"
test -f "$report.ready"
echo "forcing retirement published: $report"
