#!/bin/bash -l
# Publish a passed baseline source and a non-authorizing canonical-plan candidate.
#SBATCH --job-name=hicar-v29-source-final
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
publisher=${HICAR_BASELINE_SOURCE_PUBLISHER:?Set source publisher}
plan_preparer=${HICAR_BASELINE_CANONICAL_PLAN_PREPARER:?Set canonical-plan preparer}
transition_report=${HICAR_BASELINE_TRANSITION_REPORT:?Set passed transition report}
transition_plan=${HICAR_BASELINE_TRANSITION_PLAN:?Set published transition plan}
assessment_contract=${HICAR_BASELINE_ASSESSMENT_CONTRACT:?Set frozen assessment contract}
source_bundle=${HICAR_BASELINE_SOURCE_BUNDLE:?Set durable candidate source bundle}
canonical_plan=${HICAR_CANONICAL_SCIENTIFIC_PLAN:?Set current canonical scientific plan}
source_qualification=${HICAR_BASELINE_SOURCE_QUALIFICATION:?Set output source qualification}
candidate_plan=${HICAR_BASELINE_CANDIDATE_CANONICAL_PLAN:?Set candidate canonical plan}

for path in \
  "$python" "$publisher" "$plan_preparer" \
  "$transition_report" "$transition_report.ready" \
  "$transition_plan" "$transition_plan.ready" \
  "$assessment_contract" "$assessment_contract.ready" \
  "$source_bundle" "$canonical_plan"; do
  test -e "$path" || { echo "missing source-finalization input: $path" >&2; exit 2; }
done

"$python" "$publisher" \
  --transition-report "$transition_report" \
  --transition-plan "$transition_plan" \
  --assessment-contract "$assessment_contract" \
  --source-bundle "$source_bundle" \
  --report "$source_qualification"
test -s "$source_qualification"
test -f "$source_qualification.ready"

"$python" "$plan_preparer" \
  --scientific-plan "$canonical_plan" \
  --source-qualification "$source_qualification" \
  --transition-report "$transition_report" \
  --output "$candidate_plan"
test -s "$candidate_plan"
test -f "$candidate_plan.ready"

echo "PASS: source qualification and candidate canonical plan published"
echo "No month, annual, 20-year, or 100 m compute was authorized."
