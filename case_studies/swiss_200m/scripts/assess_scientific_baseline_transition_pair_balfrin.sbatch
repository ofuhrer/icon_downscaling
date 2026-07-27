#!/bin/bash -l
# Run the frozen paired-event assessor and the V29 transition assessor.
#SBATCH --job-name=hicar-v29-pair-gate
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --time=00:30:00

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

python=${HICAR_VALIDATION_PYTHON:-$SCRATCH/icon_hicar/venv_static/bin/python}
paired_assessor=${HICAR_BASELINE_PAIRED_EVENT_ASSESSOR:?Set paired assessor}
transition_assessor=${HICAR_BASELINE_TRANSITION_ASSESSOR:?Set transition assessor}
contract=${HICAR_BASELINE_ASSESSMENT_CONTRACT:?Set frozen transition contract}
transition_plan=${HICAR_BASELINE_TRANSITION_PLAN:?Set transition plan}
candidate_plan=${HICAR_BASELINE_CANDIDATE_PLAN:?Set candidate scientific plan}
runtime_manifest=${HICAR_BASELINE_RUNTIME_MANIFEST:?Set event runtime manifest}
summer_run=${HICAR_BASELINE_SUMMER_RUN:?Set summer run}
winter_run=${HICAR_BASELINE_WINTER_RUN:?Set winter run}
overlap_completion=${HICAR_BASELINE_OVERLAP_COMPLETION:?Set overlap completion}
trajectory=${HICAR_BASELINE_TRAJECTORY_REPORT:?Set trajectory comparison}
source_bundle=${HICAR_BASELINE_SOURCE_BUNDLE:?Set durable source bundle}
paired_report=${HICAR_BASELINE_PAIRED_EVENT_REPORT:?Set paired report}
transition_report=${HICAR_BASELINE_TRANSITION_REPORT:?Set transition report}

for path in \
  "$python" "$paired_assessor" "$transition_assessor" \
  "$contract" "$contract.ready" "$transition_plan" "$transition_plan.ready" \
  "$candidate_plan" "$candidate_plan.ready" "$runtime_manifest" \
  "$runtime_manifest.ready" "$summer_run" "$winter_run" \
  "$overlap_completion" "$overlap_completion.ready" \
  "$trajectory" "$trajectory.ready" "$source_bundle"; do
  test -e "$path" || { echo "missing paired transition input: $path" >&2; exit 2; }
done

if ! test -f "$paired_report.ready"; then
  "$python" "$paired_assessor" \
    --plan "$candidate_plan" \
    --event "summer=$summer_run" \
    --event "winter=$winter_run" \
    --restart-trajectory-report "$trajectory" \
    --report "$paired_report"
fi
test -s "$paired_report.ready"

if ! test -f "$transition_report.ready"; then
  "$python" "$transition_assessor" \
    --contract "$contract" \
    --transition-plan "$transition_plan" \
    --candidate-plan "$candidate_plan" \
    --runtime-manifest "$runtime_manifest" \
    --paired-event-assessment "$paired_report" \
    --summer-run "$summer_run" \
    --winter-run "$winter_run" \
    --restart-overlap-completion "$overlap_completion" \
    --restart-trajectory-report "$trajectory" \
    --source-bundle "$source_bundle" \
    --report "$transition_report"
fi
test -s "$transition_report.ready"
