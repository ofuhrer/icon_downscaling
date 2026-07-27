#!/bin/bash -l
# CPU-side comparison and publication for the Alpine restart bridge.
#SBATCH --job-name=hicar-restart-bridge-check
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --time=00:20:00
#SBATCH --output=/scratch/mch/olifu/icon_hicar/case_studies/swiss_200m/validation/restart_continuity/bridge_check_%j.out
#SBATCH --error=/scratch/mch/olifu/icon_hicar/case_studies/swiss_200m/validation/restart_continuity/bridge_check_%j.err

set -euo pipefail

[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module purge || true
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

case_root=${HICAR_SWISS_CASE:-${SCRATCH:?}/icon_hicar/case_studies/swiss_200m}
source_root=${HICAR_RESTART_SOURCE_ROOT:?Set HICAR_RESTART_SOURCE_ROOT}
expected_commit=${HICAR_RESTART_EXPECTED_COMMIT:?Set HICAR_RESTART_EXPECTED_COMMIT}
expected_parent=${HICAR_RESTART_EXPECTED_PARENT:-2ea31109801a2477a946840693934318f8d50c95}
expected_changed_files=${HICAR_RESTART_EXPECTED_CHANGED_FILES:-}
model_job_id=${HICAR_RESTART_BRIDGE_JOB_ID:?Set HICAR_RESTART_BRIDGE_JOB_ID}
scope=${HICAR_RESTART_SCOPE:-701_X_701_RESTART_CONTINUITY_BRIDGE}
python=${HICAR_VALIDATION_PYTHON:-${SCRATCH}/icon_hicar/venv_static/bin/python}
runner=${HICAR_RESTART_RUNNER:-${case_root}/scripts/run_restart_continuity_bridge_balfrin.sbatch}
validator=${case_root}/validation/validate_restart_continuity_bridge.py
run_root=${HICAR_RESTART_RUN_ROOT:-${case_root}/validation/restart_continuity/bridge_${model_job_id}}
tolerances=${source_root}/tests/tolerances/tolerances_restart.yaml
diagnostic_policy=${HICAR_RESTART_DIAGNOSTIC_POLICY:-}
output=${HICAR_RESTART_BRIDGE_REPORT:-${case_root}/validation/restart_continuity/restart_continuity_bridge.json}

for path in \
  "$python" "$runner" "$validator" "$run_root/model_runs.json" "$tolerances"; do
  test -e "$path" || {
    echo "missing required evidence input: $path" >&2
    exit 2
  }
done
test ! -e "$output"
test ! -e "${output}.ready"
"$python" -c 'import netCDF4, numpy, yaml'
validator_args=(
  --run-root "$run_root" \
  --source-root "$source_root" \
  --expected-commit "$expected_commit" \
  --expected-parent "$expected_parent" \
  --model-job-id "$model_job_id" \
  --scope "$scope" \
  --runner "$runner" \
  --restart-tolerances "$tolerances" \
  --output "$output"
)
if test -n "$diagnostic_policy"; then
  test -f "$diagnostic_policy"
  validator_args+=(--diagnostic-policy "$diagnostic_policy")
fi
if test -n "$expected_changed_files"; then
  # A comma is Slurm's --export separator and silently truncates values.
  # Use a colon-delimited path list for this single environment variable.
  IFS=':' read -r -a changed_file_list <<< "$expected_changed_files"
  for changed_file in "${changed_file_list[@]}"; do
    validator_args+=(--expected-changed-file "$changed_file")
  done
fi
"$python" "$validator" "${validator_args[@]}"
