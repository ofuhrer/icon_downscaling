#!/bin/bash -l
# CPU validation and evidence publication for the national water-budget gate.
#SBATCH --job-name=hicar-water-budget-validate
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --time=01:00:00
#SBATCH --output=/scratch/mch/olifu/icon_hicar/case_studies/swiss_200m/validation/water_budget/source_qualification_validate_%j.out
#SBATCH --error=/scratch/mch/olifu/icon_hicar/case_studies/swiss_200m/validation/water_budget/source_qualification_validate_%j.err

set -euo pipefail

[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module purge || true
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module load netcdf-c/4.8.1-gcc netcdf-fortran/4.5.4-gcc

case_root=${HICAR_SWISS_CASE:-${SCRATCH:?}/icon_hicar/case_studies/swiss_200m}
parent_root=${HICAR_WATER_BUDGET_PARENT_ROOT:-${SCRATCH}/icon_hicar/HICAR-radiation-cloud-fraction}
child_root=${HICAR_WATER_BUDGET_CHILD_ROOT:-${SCRATCH}/icon_hicar/HICAR-water-budget-p0}
child_build=${HICAR_WATER_BUDGET_CHILD_BUILD:-${child_root}/build_gpu_nccl_water_budget_p0}
child_commit=${HICAR_WATER_BUDGET_CHILD_COMMIT:?Set HICAR_WATER_BUDGET_CHILD_COMMIT}
model_job_id=${HICAR_WATER_BUDGET_MODEL_JOB_ID:?Set HICAR_WATER_BUDGET_MODEL_JOB_ID}
build_job_id=${HICAR_WATER_BUDGET_BUILD_JOB_ID:?Set HICAR_WATER_BUDGET_BUILD_JOB_ID}
bridge_job_id=${HICAR_WATER_BUDGET_BRIDGE_JOB_ID:?Set HICAR_WATER_BUDGET_BRIDGE_JOB_ID}
validator=${HICAR_WATER_BUDGET_VALIDATOR:?Set HICAR_WATER_BUDGET_VALIDATOR}
validator_sha256=${HICAR_WATER_BUDGET_VALIDATOR_SHA256:?Set HICAR_WATER_BUDGET_VALIDATOR_SHA256}
python=${HICAR_VALIDATION_PYTHON:-${SCRATCH}/icon_hicar/venv_static/bin/python}
run_root=${HICAR_WATER_BUDGET_RUN_ROOT:-${case_root}/validation/water_budget/national_source_qualification_${model_job_id}}
output_dir=${HICAR_WATER_BUDGET_EVIDENCE_DIR:-${run_root}/evidence}
build_log=${case_root}/validation/water_budget_build_${build_job_id}.out
build_script=${case_root}/scripts/build_water_budget_p0_balfrin.sbatch
bridge_run=${case_root}/validation/radiation_cloud_fraction/bridge_${bridge_job_id}
tolerances=${child_root}/tests/tolerances/tolerances_restart.yaml

for path in \
  "$validator" "${validator}.ready" "$python" "$run_root/model_runs.json" \
  "$build_log" "$build_script" "$bridge_run/model.out" "$tolerances"; do
  test -e "$path" || {
    echo "missing required validation input: $path" >&2
    exit 2
  }
done
test "$(sha256sum "$validator" | awk '{print $1}')" = "$validator_sha256"
test ! -e "$output_dir"

"$python" "$validator" \
  --run-root "$run_root" \
  --parent-root "$parent_root" \
  --child-root "$child_root" \
  --child-build "$child_build" \
  --child-commit "$child_commit" \
  --build-job-id "$build_job_id" \
  --build-log "$build_log" \
  --build-script "$build_script" \
  --bridge-job-id "$bridge_job_id" \
  --bridge-run "$bridge_run" \
  --restart-tolerances "$tolerances" \
  --output-dir "$output_dir"

test -f "$output_dir/month_source_qualification.json"
test -f "$output_dir/month_source_qualification.json.ready"
echo "water-budget source qualification evidence published: $output_dir"
