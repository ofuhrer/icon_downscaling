#!/bin/bash -l
# Publish the annual scientific plan after an explicit GO_ANNUAL_CYCLE.
# This job plans only; it never submits the annual computation.
#SBATCH --job-name=hicar-annual-plan
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=00:30:00

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

repo_root=${REPO_ROOT:-$SCRATCH/icon_hicar}
python=${HICAR_VALIDATION_PYTHON:-$repo_root/venv_static/bin/python}
planner=$repo_root/case_studies/swiss_200m/streaming/prepare_scientific_annual_pilot.py
scientific_plan=${SCIENTIFIC_PLAN:-$repo_root/case_studies/swiss_200m/config/scientific_pilot_plan.json}
month_assessment=${MONTH_ASSESSMENT:?Set MONTH_ASSESSMENT}
annual_root=${ANNUAL_ROOT:?Set ANNUAL_ROOT}
annual_run_root=${ANNUAL_RUN_ROOT:?Set ANNUAL_RUN_ROOT}
annual_static=${ANNUAL_STATIC_FILE:?Set ANNUAL_STATIC_FILE}
annual_initialization=${ANNUAL_INITIALIZATION_MANIFEST:?Set ANNUAL_INITIALIZATION_MANIFEST}
winter_static=${WINTER_STATIC_FILE:?Set WINTER_STATIC_FILE}
winter_initialization=${WINTER_INITIALIZATION_MANIFEST:?Set WINTER_INITIALIZATION_MANIFEST}
summer_static=${SUMMER_STATIC_FILE:?Set SUMMER_STATIC_FILE}
summer_initialization=${SUMMER_INITIALIZATION_MANIFEST:?Set SUMMER_INITIALIZATION_MANIFEST}
report=${ANNUAL_PILOT_PLAN:-$annual_root/annual_pilot_plan.json}

for path in "$python" "$planner" "$scientific_plan" \
            "$month_assessment" "$month_assessment.ready" \
            "$annual_static" "$annual_static.ready" \
            "$annual_initialization" "$annual_initialization.ready" \
            "$winter_static" "$winter_static.ready" \
            "$winter_initialization" "$winter_initialization.ready" \
            "$summer_static" "$summer_static.ready" \
            "$summer_initialization" "$summer_initialization.ready"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done

"$python" "$planner" \
  --scientific-plan "$scientific_plan" \
  --month-assessment "$month_assessment" \
  --annual-root "$annual_root" \
  --run-root "$annual_run_root" \
  --static-file "$annual_static" \
  --land-initialization-manifest "$annual_initialization" \
  --winter-static-file "$winter_static" \
  --winter-initialization-manifest "$winter_initialization" \
  --summer-static-file "$summer_static" \
  --summer-initialization-manifest "$summer_initialization" \
  --report "$report"

test -f "$report.ready"
echo "Annual planning publication completed; no compute jobs were submitted."
