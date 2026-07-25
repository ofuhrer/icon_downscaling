#!/bin/bash -l
# Apply the frozen paired-event promotion criteria.
#SBATCH --job-name=hicar-event-verdict
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
plan=${SCIENTIFIC_PILOT_PLAN:-$repo_root/case_studies/swiss_200m/config/scientific_pilot_plan.json}
assessor=$repo_root/case_studies/swiss_200m/validation/assess_scientific_pilot_events.py
summer_run=${SUMMER_EVENT_RUN:-$repo_root/case_studies/swiss_200m/runs/science_summer_20200701_00_20200704_00}
winter_run=${WINTER_EVENT_RUN:-$repo_root/case_studies/swiss_200m/runs/science_winter_20200115_00_20200118_00}
report=${EVENT_ASSESSMENT_REPORT:-$repo_root/case_studies/swiss_200m/runs/scientific_pilot_event_assessment.json}
trajectory=${EVENT_RESTART_TRAJECTORY_REPORT:-$repo_root/case_studies/swiss_200m/runs/science_summer_restart_overlap_20200703_00_20200704_00/restart_trajectory_comparison.json}

for path in "$python" "$plan" "$assessor" "$summer_run" "$winter_run" \
  "$trajectory" "$trajectory.ready"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done

"$python" "$assessor" \
  --plan "$plan" \
  --event "summer=$summer_run" \
  --event "winter=$winter_run" \
  --restart-trajectory-report "$trajectory" \
  --report "$report"
test -f "$report.ready"
echo "PASS: paired-event verdict published at $report"
