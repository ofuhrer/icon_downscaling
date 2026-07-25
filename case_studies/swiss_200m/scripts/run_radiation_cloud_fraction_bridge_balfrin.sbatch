#!/bin/bash -l
# 701 x 701 Alpine bridge gate for the level-local cloud-fraction GPU kernel.
# Submit only after the matching HICAR build and four-GPU unit/halo gates pass.
#SBATCH --job-name=hicar-rad-bridge
#SBATCH --partition=debug
#SBATCH --nodes=2
#SBATCH --ntasks-per-node=5
#SBATCH --cpus-per-task=1
#SBATCH --gres=gpu:4
#SBATCH --time=00:20:00
#SBATCH --exclusive
#SBATCH --output=/scratch/mch/olifu/icon_hicar/case_studies/swiss_200m/validation/radiation_cloud_fraction/bridge_%j.out
#SBATCH --error=/scratch/mch/olifu/icon_hicar/case_studies/swiss_200m/validation/radiation_cloud_fraction/bridge_%j.out

set -euo pipefail

[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module purge || true
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module load nvhpc/24.5 cray-mpich-nvhpc/8.1.30 cuda/12.3.0-gcc \
  netcdf-c/4.9.2-nvhpc netcdf-fortran/4.6.1-nvhpc \
  hdf5/1.14.3-nvhpc fftw/3.3.10-gcc nco/5.0.1-gcc

source_root=${HICAR_RAD_SOURCE_ROOT:-${SCRATCH:?}/icon_hicar/HICAR-radiation-cloud-fraction}
build_root=${HICAR_RAD_BUILD_ROOT:-${source_root}/build_gpu_nccl}
case_root=${HICAR_SWISS_CASE:-${SCRATCH}/icon_hicar/case_studies/swiss_200m}
bridge_root=${HICAR_BRIDGE_CASE:-${SCRATCH}/icon_hicar/case_studies/alpine_bridge_200m}
template=${HICAR_BRIDGE_NML_SOURCE:-${case_root}/runs/integration_20100101_0030_dynamic_prec4/hicar.nml}
static_file=${bridge_root}/static/domain_static_alpine_bridge_200m.nc
wrapper=${case_root}/scripts/gpu_rank_wrapper.sh
validation_root=${case_root}/validation/radiation_cloud_fraction
run_root=${validation_root}/bridge_${SLURM_JOB_ID:?}
exe=${build_root}/HICAR_gpu

test -x "${exe}" && test -x "${wrapper}"
test -f "${template}" && test -f "${static_file}" && test -f "${static_file}.ready"
git -C "${source_root}" diff --quiet
source_commit=$(git -C "${source_root}" rev-parse HEAD)
if test -n "${HICAR_EXPECTED_COMMIT:-}"; then
  test "${source_commit}" = "${HICAR_EXPECTED_COMMIT}"
fi

mkdir -p "${run_root}/output" "${run_root}/restart"
forcing_list_source=$(awk -F"'" \
  '/^[[:space:]]*forcing_file_list[[:space:]]*=/{print $2; exit}' \
  "${template}")
test -s "${forcing_list_source}"
forcing_list=${run_root}/forcing_files.txt
awk 'NF && $1 !~ /^#/ {gsub(/^"|"$/, "", $0); printf "\"%s\"\n", $0}' \
  "${forcing_list_source}" > "${forcing_list}"
while IFS= read -r forcing; do
  forcing=${forcing#\"}
  forcing=${forcing%\"}
  test -f "${forcing}" && test -f "${forcing}.ready"
done < "${forcing_list}"

sed \
  -e "/^[[:space:]]*restart_folder[[:space:]]*=/c\\  restart_folder = '${run_root}/restart/'" \
  -e "/^[[:space:]]*output_folder[[:space:]]*=/c\\  output_folder = '${run_root}/output/'" \
  -e "/^[[:space:]]*forcing_file_list[[:space:]]*=/c\\  forcing_file_list = '${forcing_list}'" \
  -e "/^[[:space:]]*init_conditions_file[[:space:]]*=/c\\  init_conditions_file = '${static_file}'" \
  -e "/^[[:space:]]*outputinterval[[:space:]]*=/c\\  outputinterval = 1800" \
  -e "/^[[:space:]]*output_vars[[:space:]]*=/c\\  output_vars = 'precipitation', 'psfc', 'psl', 'rsds', 'lwtr', 'rlus', 'hfgs', 'emiss', 'cldfrac'" \
  "${template}" > "${run_root}/input.nml"

for asset in NoahmpTable.TBL rrtmgp_support mp_support; do
  cp -a "${source_root}/run/${asset}" "${run_root}/"
done
printf '%s\n' "${source_commit}" > "${run_root}/source_commit.txt"
sha256sum "${exe}" > "${run_root}/executable.sha256"
scontrol show job "${SLURM_JOB_ID}" > "${run_root}/slurm.txt"

export OMP_NUM_THREADS=1
export MPICH_GPU_SUPPORT_ENABLED=0
export MPICH_GPU_MANAGED_MEMORY_SUPPORT_ENABLED=0
export HICAR_WIND_ADJOINT_PROJECTION=1
export HICAR_WIND_RECYCLE_DIM=0
unset HICAR_WIND_MULTILEVEL
unset HICAR_WIND_OPERATOR_AUDIT
unset HICAR_WIND_OPERATOR_AUDIT_MAX_ITERS

cd "${run_root}"
srun --distribution=block:block --cpu-bind=none \
  "${wrapper}" "${exe}" "${run_root}/input.nml" 2>&1 | tee "${run_root}/model.out"

grep -q 'HICAR discretely adjoint wind projection enabled' "${run_root}/model.out"
grep -q 'HICAR SLEVE geometry gate:' "${run_root}/model.out"
grep -q 'HICAR exact Galerkin hierarchy ready:' "${run_root}/model.out"
grep -q 'HICAR adjoint conservation: relative_Bq=' "${run_root}/model.out"
grep -q 'Timing across all compute images:' "${run_root}/model.out"
! grep -q 'Accelerator Fatal Error' "${run_root}/model.out"
awk -f "${source_root}/tools/check_adjoint_projection_log.awk" \
  "${run_root}/model.out"

shopt -s nullglob
outputs=("${run_root}"/output/*.nc)
test "${#outputs[@]}" -eq 1
test -s "${outputs[0]}"
cldfrac_metadata=$(ncks -m -v cldfrac "${outputs[0]}")
grep -Fq 'cldfrac:standard_name = "cloud_area_fraction"' <<<"$cldfrac_metadata"
printf 'PASS source_commit=%s output=%s bytes=%s\n' \
  "${source_commit}" "${outputs[0]}" "$(stat -c %s "${outputs[0]}")"
