#!/bin/bash
# Submit two sequential national wind-profile segments around one restart.

set -euo pipefail

validation_root=${VALIDATION_ROOT:-${SCRATCH:?}/icon_hicar/validation/wind-climatology}
case_root=${HICAR_SWISS_CASE:-${SCRATCH}/icon_hicar/case_studies/swiss_200m}
source_root=${HICAR_SOURCE_ROOT:-${validation_root}/HICAR-national}
build_root=${HICAR_BUILD_ROOT:-${source_root}/build_gpu_nccl}
runner="${case_root}/scripts/run_rea_l_stream_chunk_balfrin.sbatch"
renderer="${case_root}/scripts/render_hicar_namelist.py"
template="${case_root}/config/hicar_swiss_200m.nml.in"
stream_root="${validation_root}/national-stream"
restart_dir="${stream_root}/restart-chain"
first_plan="${stream_root}/wind-national-v2-20100101-0000-0100/chunk_plan.json"
second_plan="${stream_root}/wind-national-v2-20100101-0100-0200/chunk_plan.json"
reducer="${SCRATCH}/icon_hicar/scripts/reduce_hicar_wind_climatology.py"
validator="${case_root}/streaming/validate_model_chunk.py"
build_job=${HICAR_WIND_BUILD_JOB:-}

for path in \
  "${runner}" "${renderer}" "${template}" \
  "${first_plan}" "${first_plan}.ready" \
  "${second_plan}" "${second_plan}.ready" "${reducer}" "${validator}"; do
  test -e "${path}" || {
    echo "missing required path: ${path}" >&2
    exit 2
  }
done
grep -q 'routine|qualification|wind_climatology' "${runner}" || {
  echo "stream runner does not support wind_climatology" >&2
  exit 2
}
grep -q '"wind_climatology"' "${renderer}" || {
  echo "namelist renderer does not support wind_climatology" >&2
  exit 2
}
grep -q 'WIND_CLIMATOLOGY_REQUIRED_VARIABLES' "${validator}" || {
  echo "model validator does not support wind_climatology" >&2
  exit 2
}
git -C "${source_root}" diff --quiet
test "$(git -C "${source_root}" rev-parse HEAD)" = \
  2999c9bdf6e0ed50a7f44311e2c8555e26848d31
mkdir -p "${stream_root}/logs" "${restart_dir}"

dependency_args=()
if test -n "${build_job}"; then
  dependency_args=("--dependency=afterok:${build_job}")
else
  test -x "${build_root}/HICAR_gpu" || {
    echo "build is absent; set HICAR_WIND_BUILD_JOB or build first" >&2
    exit 2
  }
fi

common_exports="ALL,HICAR_MULTILEVEL_ROOT=${source_root},HICAR_MULTILEVEL_BUILD=${build_root},HICAR_SWISS_CASE=${case_root},HICAR_WIND_REDUCER=${reducer},HICAR_STREAM_VALIDATOR=${validator},STREAM_OUTPUT_PROFILE=wind_climatology,STREAM_OUTPUT_INTERVAL=1800,STREAM_WIND_REDUCTION_INTERVAL=3600,STREAM_RESTART_DIR=${restart_dir},STREAM_REA_L_LAND_INITIALIZATION=0"

first_job=$(
  sbatch --parsable \
    "${dependency_args[@]}" \
    --job-name=hicar-wind-ch1 \
    --output="${stream_root}/logs/segment-00-01-%j.out" \
    --error="${stream_root}/logs/segment-00-01-%j.err" \
    --export="${common_exports},STREAM_PLAN=${first_plan},STREAM_RUN_DIR=${stream_root}/runs/segment-00-01" \
    "${runner}"
)
second_job=$(
  sbatch --parsable \
    --dependency="afterok:${first_job}" \
    --job-name=hicar-wind-ch2 \
    --output="${stream_root}/logs/segment-01-02-%j.out" \
    --error="${stream_root}/logs/segment-01-02-%j.err" \
    --export="${common_exports},STREAM_PLAN=${second_plan},STREAM_RUN_DIR=${stream_root}/runs/segment-01-02,STREAM_RESTART_FROM=2010-01-01T01:00:00" \
    "${runner}"
)

printf 'FIRST_JOB=%s\nSECOND_JOB=%s\n' "${first_job}" "${second_job}"
