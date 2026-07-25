#!/bin/bash
# 701 x 701 Alpine bridge gate for the HICAR-native wind profile.
#SBATCH --job-name=hicar-wind-bridge
#SBATCH --partition=normal
#SBATCH --nodes=2
#SBATCH --ntasks-per-node=5
#SBATCH --cpus-per-task=1
#SBATCH --gres=gpu:4
#SBATCH --time=00:30:00
#SBATCH --exclusive
#SBATCH --output=/scratch/mch/olifu/icon_hicar/validation/wind-climatology/hicar-wind-bridge-%j.out
#SBATCH --error=/scratch/mch/olifu/icon_hicar/validation/wind-climatology/hicar-wind-bridge-%j.err

set -euo pipefail

[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module purge || true
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module load nvhpc/24.5 cray-mpich-nvhpc/8.1.30 cuda/12.3.0-gcc \
  netcdf-c/4.9.2-nvhpc netcdf-fortran/4.6.1-nvhpc \
  hdf5/1.14.3-nvhpc fftw/3.3.10-gcc cmake/3.24.4-gcc \
  gmake/4.4.1-gcc

validation_root="${VALIDATION_ROOT:-${SCRATCH:?}/icon_hicar/validation/wind-climatology}"
source_root="${HICAR_SOURCE_ROOT:-${validation_root}/HICAR}"
build_root="${HICAR_BUILD_ROOT:-${source_root}/build_gpu_nccl}"
case_root="${SCRATCH}/icon_hicar/case_studies/alpine_bridge_200m"
swiss_root="${SCRATCH}/icon_hicar/case_studies/swiss_200m"
source_namelist="${swiss_root}/runs/integration_20100101_0030_dynamic_prec4/hicar.nml"
static_file="${case_root}/static/domain_static_alpine_bridge_200m.nc"
wrapper="${swiss_root}/scripts/gpu_rank_wrapper.sh"
run_root="${validation_root}/bridge-runs/${SLURM_JOB_ID}"

test -f "${source_namelist}" && test -f "${static_file}"
test -f "${static_file}.ready" && test -x "${wrapper}"
git -C "${source_root}" diff --quiet
source_commit=$(git -C "${source_root}" rev-parse HEAD)
if test -n "${HICAR_EXPECTED_COMMIT:-}"; then
  test "${source_commit}" = "${HICAR_EXPECTED_COMMIT}"
fi
mkdir -p "${run_root}/input" "${run_root}/output" "${run_root}/restart"
printf '%s\n' "${source_commit}" > "${run_root}/source_commit.txt"

nc_prefix=$(nc-config --prefix)
nf_prefix=$(nf-config --prefix)
fftw_prefix=$(pkg-config --variable=prefix fftw3)
mpi_prefix=$(dirname "$(dirname "$(command -v mpifort)")")
nvhpc_sdk_root=$(dirname "$(dirname "$(dirname "$(command -v nvc)")")")
nccl_root="${nvhpc_sdk_root}/comm_libs/12.4/nccl"
test -f "${nccl_root}/include/nccl.h"
test -f "${nccl_root}/lib/libnccl.so"

cmake -S "${source_root}" -B "${build_root}" \
  -DFC=mpifort -DMODE=release -DOPENACC=ON -DNCCL=ON \
  -DGPU_ARCH=cc80 \
  -DNETCDF_DIR="${nc_prefix}" \
  -DNETCDF_INCLUDES="${nc_prefix}/include;${nf_prefix}/include" \
  -DNETCDF_INCLUDES_F90="${nf_prefix}/include" \
  -DNETCDF_LIBRARIES_C="${nc_prefix}/lib/libnetcdf.so" \
  -DNETCDF_LIBRARIES_F90="${nf_prefix}/lib/libnetcdff.so" \
  -DFFTW_DIR="${fftw_prefix}" \
  -DMPI_DIR="${mpi_prefix}" \
  -DNCCL_INCLUDE_DIR="${nccl_root}/include" \
  -DNCCL_LIBRARY="${nccl_root}/lib/libnccl.so" \
  -DCMAKE_BUILD_RPATH="${nf_prefix}/lib;${nc_prefix}/lib;${nccl_root}/lib"
cmake --build "${build_root}" --target HICAR --parallel 8

forcing_list_source=$(awk -F"'" \
  '/^[[:space:]]*forcing_file_list[[:space:]]*=/{print $2; exit}' \
  "${source_namelist}")
test -s "${forcing_list_source}"
forcing_list="${run_root}/input/forcing_files.txt"
awk 'NF && $1 !~ /^#/ {gsub(/^"|"$/, "", $0); printf "\"%s\"\n", $0}' \
  "${forcing_list_source}" > "${forcing_list}"
while IFS= read -r forcing; do
  forcing=${forcing#\"}
  forcing=${forcing%\"}
  test -f "${forcing}" && test -f "${forcing}.ready"
done < "${forcing_list}"

# Full-level fields are qualification-only reference inputs; the production
# The production wind_climatology profile retains the nine compact native
# diagnostics; full-level fields below are qualification-only reference inputs.
sed \
  -e "/^[[:space:]]*restart_folder[[:space:]]*=/c\\  restart_folder = '${run_root}/restart/'" \
  -e "/^[[:space:]]*output_folder[[:space:]]*=/c\\  output_folder = '${run_root}/output/'" \
  -e "/^[[:space:]]*forcing_file_list[[:space:]]*=/c\\  forcing_file_list = '${forcing_list}'" \
  -e "/^[[:space:]]*init_conditions_file[[:space:]]*=/c\\  init_conditions_file = '${static_file}'" \
  -e "/^[[:space:]]*outputinterval[[:space:]]*=/c\\  outputinterval = 1800" \
  -e "/^[[:space:]]*output_vars[[:space:]]*=/c\\  output_vars = 'u10m', 'v10m', 'u_agl', 'v_agl', 'rho_agl', 'ustar', 'surface_roughness', 'sfc_Ri', 'hpbl', 'u', 'v', 'z', 'density'" \
  "${source_namelist}" | awk '
    /^[[:space:]]*terrain_smooth_(windowsize|cycles)[[:space:]]*=/ { next }
    { print }
    /^[[:space:]]*stretch_fac[[:space:]]*=/ {
      print "  terrain_smooth_windowsize = 5"
      print "  terrain_smooth_cycles = 10"
    }
  ' > "${run_root}/input/run.nml"

for asset in NoahmpTable.TBL rrtmgp_support mp_support; do
  cp -a "${source_root}/run/${asset}" "${run_root}/"
done

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
  "${wrapper}" "${build_root}/HICAR_gpu" "${run_root}/input/run.nml" \
  2>&1 | tee "${run_root}/model.out"

grep -q 'HICAR discretely adjoint wind projection enabled' "${run_root}/model.out"
grep -q 'HICAR SLEVE geometry gate:' "${run_root}/model.out"
grep -q 'HICAR exact Galerkin hierarchy ready:' "${run_root}/model.out"
grep -q 'HICAR adjoint conservation: relative_Bq=' "${run_root}/model.out"
grep -q 'Timing across all compute images:' "${run_root}/model.out"
awk -f "${source_root}/tools/check_adjoint_projection_log.awk" \
  "${run_root}/model.out"

shopt -s nullglob
outputs=("${run_root}"/output/*.nc)
(( ${#outputs[@]} > 0 ))
for path in "${outputs[@]}"; do
  test -s "${path}"
  printf 'OUTPUT %s %s bytes\n' "${path}" "$(stat -c %s "${path}")"
done
echo "PASS: 701 x 701 wind-profile bridge completed"
echo "SOURCE_COMMIT=${source_commit}"
echo "RUN_ROOT=${run_root}"
