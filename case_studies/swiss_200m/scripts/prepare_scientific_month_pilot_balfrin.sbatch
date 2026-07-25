#!/bin/bash -l
# Publish the month-pilot segment plans only after a passing event verdict.
#SBATCH --job-name=hicar-month-plan
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=2G
#SBATCH --time=00:10:00

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

repo_root=${REPO_ROOT:-$SCRATCH/icon_hicar}
python=${HICAR_VALIDATION_PYTHON:-$repo_root/venv_static/bin/python}
planner=$repo_root/case_studies/swiss_200m/streaming/prepare_scientific_month_pilot.py
scientific_plan=${SCIENTIFIC_PILOT_PLAN:-$repo_root/case_studies/swiss_200m/config/scientific_pilot_plan.json}
event_assessment=${EVENT_ASSESSMENT_REPORT:-$repo_root/case_studies/swiss_200m/runs/scientific_pilot_event_assessment.json}
month_root=${MONTH_STREAM_ROOT:-$repo_root/streaming/rea_l_ch1/science_month_20200701_20200801}
run_root=${MONTH_RUN_ROOT:-$repo_root/case_studies/swiss_200m/runs/science_month_20200701_20200801}
static_file=${HICAR_STATIC_FILE:-$repo_root/case_studies/swiss_200m/static_initializations/domain_static_swiss_200m_rea_l_20200701_0000.nc}
land_manifest=${LAND_INITIALIZATION_MANIFEST:-$repo_root/case_studies/swiss_200m/validation/rea_l_land_initialization_20200701_0000.json}

for path in "$python" "$planner" "$scientific_plan" "$event_assessment" \
  "$event_assessment.ready" "$static_file" "$static_file.ready" \
  "$land_manifest" "$land_manifest.ready"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done

"$python" "$planner" \
  --scientific-plan "$scientific_plan" \
  --event-assessment "$event_assessment" \
  --month-root "$month_root" \
  --run-root "$run_root" \
  --static-file "$static_file" \
  --land-initialization-manifest "$land_manifest"
