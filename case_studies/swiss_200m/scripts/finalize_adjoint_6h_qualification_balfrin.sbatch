#!/bin/bash -l
# Publish the final measured qualification manifest after both validators pass.
#SBATCH --job-name=hicar-qualify-6h
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=2G
#SBATCH --time=00:15:00

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module purge || true
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module load python/3.11.7

model_job=${HICAR_MODEL_JOB:?Set HICAR_MODEL_JOB}
output_job=${HICAR_OUTPUT_JOB:?Set HICAR_OUTPUT_JOB}
source_job=${HICAR_SOURCE_JOB:?Set HICAR_SOURCE_JOB}
run=${HICAR_SWISS_RUN:?Set HICAR_SWISS_RUN}
case_root=${HICAR_SWISS_CASE:-$SCRATCH/icon_hicar/case_studies/swiss_200m}
finalizer="$case_root/validation/finalize_production_qualification.py"
report="$case_root/validation/production_6h_qualification_${model_job}.json"

for path in "$finalizer" "$run/output_validation.json.ready" \
            "$run/source_aware_validation.json.ready"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done
rm -f "$report" "$report.ready"
python3 "$finalizer" \
  --run "$run" \
  --model-job "$model_job" \
  --output-job "$output_job" \
  --source-job "$source_job" \
  --report "$report"
test "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["status"])' "$report")" = PASS
touch "$report.ready"
echo "Final six-hour production qualification passed: report=$report"
