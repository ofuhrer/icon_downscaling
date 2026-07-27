#!/bin/bash -l
# Apply all frozen source-transition and scientific screens to one event.
#SBATCH --job-name=hicar-v29-event-gate
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=00:15:00

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

python=${HICAR_VALIDATION_PYTHON:-$SCRATCH/icon_hicar/venv_static/bin/python}
assessor=${HICAR_BASELINE_EVENT_ASSESSOR:?Set frozen event assessor}
event_name=${HICAR_BASELINE_EVENT_NAME:?Set summer or winter}
run=${HICAR_BASELINE_EVENT_RUN:?Set completed event run}
candidate_plan=${HICAR_BASELINE_CANDIDATE_PLAN:?Set candidate scientific plan}
contract=${HICAR_BASELINE_ASSESSMENT_CONTRACT:?Set frozen transition contract}
report=${HICAR_BASELINE_EVENT_ASSESSMENT_REPORT:?Set output report}

for path in "$python" "$assessor" "$run" "$candidate_plan" \
  "$candidate_plan.ready" "$contract" "$contract.ready"; do
  test -e "$path" || { echo "missing event gate input: $path" >&2; exit 2; }
done

"$python" "$assessor" \
  --event-name "$event_name" \
  --run-dir "$run" \
  --candidate-plan "$candidate_plan" \
  --transition-contract "$contract" \
  --report "$report"
test -s "$report.ready"
