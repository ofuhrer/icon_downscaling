#!/bin/bash -l
# Assess the published bridge wind-spinup campaign on a shared CPU node.
#SBATCH --job-name=wind-spinup-assess
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=16G
#SBATCH --time=01:00:00

set -euo pipefail

runtime=${WIND_ASSESSMENT_RUNTIME:?Set WIND_ASSESSMENT_RUNTIME}
python=${HICAR_VALIDATION_PYTHON:?Set HICAR_VALIDATION_PYTHON}
campaign_root=${WIND_CAMPAIGN_ROOT:?Set WIND_CAMPAIGN_ROOT}
gate_report=${PLATEAU_GATE_REPORT:?Set PLATEAU_GATE_REPORT}
analysis_root=${WIND_ANALYSIS_ROOT:-$campaign_root/analysis}

experiment="$campaign_root/manifests/experiment.json"
completion="$campaign_root/execution/campaign_completion.json"
results="$analysis_root/wind_spinup_results.json"
report="$analysis_root/wind_spinup_convergence.json"

for path in "$runtime/runtime_release.json" "$runtime/runtime_release.json.ready" \
            "$python" "$experiment" "$experiment.ready" "$completion" \
            "$completion.ready" "$gate_report" "$gate_report.ready"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done
"$python" \
  "$runtime/case_studies/swiss_200m/wind_climatology/prepare_wind_spinup_assessment_release.py" \
  --validate "$runtime/runtime_release.json"
"$python" - "$gate_report" <<'PY'
import json
import sys
if json.load(open(sys.argv[1])).get("status") != "PASS":
    raise SystemExit("Plateau REA-L selection gate is not PASS")
PY

mkdir -p "$analysis_root"
if ! test -f "$results.ready"; then
  "$python" \
    "$runtime/case_studies/swiss_200m/wind_climatology/prepare_wind_spinup_results.py" \
    --experiment "$experiment" \
    --campaign-completion "$completion" \
    --results "$results"
fi
if test -f "$report.ready"; then
  echo "wind-spinup assessment already published: $report"
  exit 0
fi

set +e
"$python" \
  "$runtime/case_studies/swiss_200m/wind_climatology/assess_wind_spinup_convergence.py" \
  --experiment "$experiment" \
  --results "$results" \
  --selection-gate "$gate_report" \
  --report "$report"
assessment_status=$?
set -e
if test "$assessment_status" -ne 0 && test "$assessment_status" -ne 2; then
  exit "$assessment_status"
fi
test -s "$report"
test -f "$report.ready"
exit 0
