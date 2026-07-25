#!/bin/bash -l
# Build the committed HICAR wind-climatology candidate on the Balfrin stack.
#SBATCH --job-name=hicar-wind-build
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --time=00:30:00
#SBATCH --output=/scratch/mch/olifu/icon_hicar/validation/wind-climatology/build-%j.out
#SBATCH --error=/scratch/mch/olifu/icon_hicar/validation/wind-climatology/build-%j.err

set -euo pipefail

[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module purge || true
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module load nvhpc/24.5 cray-mpich-nvhpc/8.1.30 cuda/12.3.0-gcc \
  netcdf-c/4.9.2-nvhpc netcdf-fortran/4.6.1-nvhpc \
  hdf5/1.14.3-nvhpc fftw/3.3.10-gcc cmake/3.24.4-gcc \
  gmake/4.4.1-gcc

source_root=${HICAR_SOURCE_ROOT:-${SCRATCH:?}/icon_hicar/validation/wind-climatology/HICAR-national}
build_root=${HICAR_BUILD_ROOT:-${source_root}/build_gpu_nccl}

git -C "${source_root}" diff --quiet
source_commit=$(git -C "${source_root}" rev-parse HEAD)

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
cmake --build "${build_root}" --target HICAR --parallel "${SLURM_CPUS_PER_TASK}"

test -x "${build_root}/HICAR_gpu"
printf 'PASS source_commit=%s executable_sha256=%s\n' \
  "${source_commit}" \
  "$(sha256sum "${build_root}/HICAR_gpu" | awk '{print $1}')"
