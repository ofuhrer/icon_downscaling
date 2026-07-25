#!/bin/bash -l
# Compare a bounded restarted event continuation with its continuous reference.
#SBATCH --job-name=hicar-event-trajectory
#SBATCH --partition=pp-long
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=128G
#SBATCH --time=04:00:00

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

repo_root=${REPO_ROOT:-$SCRATCH/icon_hicar}
python=${HICAR_VALIDATION_PYTHON:-$repo_root/venv_static/bin/python}
comparator=$repo_root/case_studies/swiss_200m/streaming/compare_segmented_to_uninterrupted.py
scientific_plan=${SCIENTIFIC_PILOT_PLAN:-$repo_root/case_studies/swiss_200m/config/scientific_pilot_plan.json}
restarted=${RESTARTED_COMPLETION:?Set RESTARTED_COMPLETION}
continuous=${CONTINUOUS_COMPLETION:?Set CONTINUOUS_COMPLETION}
start=${TRAJECTORY_START:?Set TRAJECTORY_START}
end=${TRAJECTORY_END:?Set TRAJECTORY_END}
report=${EVENT_RESTART_TRAJECTORY_REPORT:?Set EVENT_RESTART_TRAJECTORY_REPORT}

for path in "$python" "$comparator" "$scientific_plan" \
  "$restarted" "$restarted.ready" "$continuous" "$continuous.ready"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done

"$python" "$comparator" \
  --segmented-completion "$restarted" \
  --reference-completion "$continuous" \
  --scientific-plan "$scientific_plan" \
  --start "$start" \
  --end "$end" \
  --report "$report"
test -s "$report.ready"
