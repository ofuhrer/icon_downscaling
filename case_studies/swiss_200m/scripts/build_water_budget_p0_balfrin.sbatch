#!/bin/bash -l
# Build the isolated output-diagnostic-only water-budget child on Balfrin.
#SBATCH --job-name=hicar-water-budget-build
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --time=00:30:00
#SBATCH --output=/scratch/mch/olifu/icon_hicar/case_studies/swiss_200m/validation/water_budget_build_%j.out
#SBATCH --error=/scratch/mch/olifu/icon_hicar/case_studies/swiss_200m/validation/water_budget_build_%j.err

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module purge || true
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module load nvhpc/24.5 cray-mpich-nvhpc/8.1.30 cuda/12.3.0-gcc \
  cmake/3.24.4-gcc gmake/4.4.1-gcc netcdf-c/4.9.2-nvhpc \
  netcdf-fortran/4.6.1-nvhpc hdf5/1.14.3-nvhpc fftw/3.3.10-gcc

root=${HICAR_WATER_BUDGET_ROOT:-$SCRATCH/icon_hicar/HICAR-water-budget-p0}
build=${HICAR_WATER_BUDGET_BUILD:-$root/build_gpu_nccl_water_budget_p0}
expected_commit=${HICAR_WATER_BUDGET_EXPECTED_COMMIT:?Set HICAR_WATER_BUDGET_EXPECTED_COMMIT}

test "$(git -C "$root" rev-parse HEAD)" = "$expected_commit"
test -z "$(git -C "$root" status --porcelain --untracked-files=no)"
test "$(git -C "$root" rev-parse HEAD^)" = \
  "2ea31109801a2477a946840693934318f8d50c95"

nc_prefix=$(nc-config --prefix)
nf_prefix=$(nf-config --prefix)
fftw_prefix=$(pkg-config --variable=prefix fftw3)
mpi_prefix=$(dirname "$(dirname "$(command -v mpifort)")")
nvhpc_sdk_root=$(dirname "$(dirname "$(dirname "$(command -v nvc)")")")
nccl_root="${nvhpc_sdk_root}/comm_libs/12.4/nccl"
test -f "$nc_prefix/lib/libnetcdf.so"
test -f "$nf_prefix/lib/libnetcdff.so"
test -f "$nccl_root/include/nccl.h"
test -f "$nccl_root/lib/libnccl.so"
test ! -e "$build"

cmake -S "$root" -B "$build" \
  -DFC=mpifort -DMODE=release -DGPU_ARCH=cc80 -DOPENACC=ON -DNCCL=ON \
  -DNETCDF_DIR="$nc_prefix" \
  -DNETCDF_INCLUDES="$nc_prefix/include;$nf_prefix/include" \
  -DNETCDF_INCLUDES_F90="$nf_prefix/include" \
  -DNETCDF_LIBRARIES_C="$nc_prefix/lib/libnetcdf.so" \
  -DNETCDF_LIBRARIES_F90="$nf_prefix/lib/libnetcdff.so" \
  -DFFTW_DIR="$fftw_prefix" \
  -DMPI_DIR="$mpi_prefix" \
  -DNCCL_INCLUDE_DIR="$nccl_root/include" \
  -DNCCL_LIBRARY="$nccl_root/lib/libnccl.so" \
  -DCMAKE_BUILD_RPATH="$nf_prefix/lib;$nc_prefix/lib;$nccl_root/lib"
cmake --build "$build" --target HICAR HICAR-tester \
  --parallel "$SLURM_CPUS_PER_TASK"

test "$(git -C "$root" rev-parse HEAD)" = "$expected_commit"
test -z "$(git -C "$root" status --porcelain --untracked-files=no)"
sha256sum "$build/HICAR_gpu" "$build/tests/HICAR-tester"
