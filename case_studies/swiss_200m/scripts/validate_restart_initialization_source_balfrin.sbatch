#!/bin/bash -l
# Publish the isolated restart-initialization source qualification.
#SBATCH --job-name=hicar-restart-source-evidence
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --time=00:30:00
#SBATCH --output=/scratch/mch/olifu/icon_hicar/case_studies/swiss_200m/validation/restart_continuity/source_evidence_%j.out
#SBATCH --error=/scratch/mch/olifu/icon_hicar/case_studies/swiss_200m/validation/restart_continuity/source_evidence_%j.err

set -euo pipefail

[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module purge || true
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module load python/3.11.7

case_root=${HICAR_SWISS_CASE:-${SCRATCH:?}/icon_hicar/case_studies/swiss_200m}
parent_root=${HICAR_RESTART_PARENT_ROOT:-${SCRATCH}/icon_hicar/HICAR-radiation-cloud-fraction}
child_root=${HICAR_RESTART_CHILD_ROOT:?Set HICAR_RESTART_CHILD_ROOT}
child_build=${HICAR_RESTART_CHILD_BUILD:?Set HICAR_RESTART_CHILD_BUILD}
child_commit=${HICAR_RESTART_CHILD_COMMIT:?Set HICAR_RESTART_CHILD_COMMIT}
build_job_id=${HICAR_RESTART_BUILD_JOB_ID:?Set HICAR_RESTART_BUILD_JOB_ID}
bridge_job_id=${HICAR_RESTART_BRIDGE_JOB_ID:?Set HICAR_RESTART_BRIDGE_JOB_ID}
national_job_id=${HICAR_RESTART_NATIONAL_JOB_ID:?Set HICAR_RESTART_NATIONAL_JOB_ID}
report_name=${HICAR_RESTART_REPORT_NAME:-restart_initialization_qualification.json}
python=${HICAR_VALIDATION_PYTHON:-${SCRATCH}/icon_hicar/venv_static/bin/python}
validator=${case_root}/validation/validate_restart_initialization_source.py
build_script=${SCRATCH}/icon_hicar/case_studies/swiss_200m/scripts/build_water_budget_p0_balfrin.sbatch
build_log=${case_root}/validation/water_budget_build_${build_job_id}.out
bridge_run=${case_root}/validation/radiation_cloud_fraction/bridge_${bridge_job_id}
national_run=${case_root}/validation/water_budget/national_source_qualification_${national_job_id}
tolerances=${child_root}/tests/tolerances/tolerances_restart.yaml
output=${case_root}/validation/restart_continuity/${report_name}

for path in \
  "$python" "$validator" "$build_script" "$build_log" "$bridge_run/model.out" \
  "$bridge_run/source_commit.txt" "$national_run/model_runs.json" "$tolerances"; do
  test -e "$path" || {
    echo "missing required evidence input: $path" >&2
    exit 2
  }
done
test ! -e "$output"
test ! -e "${output}.ready"
"$python" -c 'import netCDF4, numpy, yaml'

"$python" "$validator" \
  --run-root "$national_run" \
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
  --output "$output"
