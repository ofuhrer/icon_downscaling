#!/bin/bash -l
# Canonical clean HICAR build entry point for Balfrin.
#
# Required submission variables:
#   HICAR_SOURCE_ROOT    HICAR worktree
#   HICAR_BUILD_ROOT     new, non-existent build directory
#   HICAR_EXPECTED_COMMIT exact source commit
#   HICAR_EXPECTED_DIFF_SHA256 optional expected tracked source diff
#   HICAR_BUILD_VARIANT  cpu, gpu-mpi, or gpu-nccl
#
# Do not build two variants concurrently from one source clone. HICAR's
# HICAR-tester dependency populates tests/Test_Cases inside the source tree.
# The advisory lock below rejects that unsafe topology.
#
# Compile on pp-short/pp-long. GPU partitions are for running the resulting
# executable, not for compilation.
#SBATCH --job-name=hicar-build
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --time=01:00:00
#SBATCH --output=hicar_build_%j.out
#SBATCH --error=hicar_build_%j.err

set -euo pipefail

coordinator_root=${HICAR_COORDINATOR_ROOT:?Set HICAR_COORDINATOR_ROOT}
. "$coordinator_root/scripts/load_balfrin_site_config.sh"
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module purge || true
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

source_root=${HICAR_SOURCE_ROOT:?Set HICAR_SOURCE_ROOT}
build_root=${HICAR_BUILD_ROOT:?Set HICAR_BUILD_ROOT to a new directory}
expected_commit=${HICAR_EXPECTED_COMMIT:?Set HICAR_EXPECTED_COMMIT}
expected_diff=${HICAR_EXPECTED_DIFF_SHA256:-}
variant=${HICAR_BUILD_VARIANT:?Choose cpu, gpu-mpi, or gpu-nccl}
mode=${HICAR_BUILD_MODE:-release}

lock_dir="${source_root}.hicar-build-active"
if ! mkdir "$lock_dir"; then
  echo "another HICAR build is using source root: $source_root" >&2
  echo "use a separate clean source clone for each concurrent build variant" >&2
  test -f "$lock_dir/owner" && cat "$lock_dir/owner" >&2
  exit 3
fi
printf 'job_id=%s\nhost=%s\n' "${SLURM_JOB_ID:-unknown}" "$(hostname)" \
  >"$lock_dir/owner"
release_source_lock() {
  rm -f "$lock_dir/owner"
  rmdir "$lock_dir"
}
trap release_source_lock EXIT INT TERM

case "$variant" in
  cpu)
    module load gcc/12.3.0 cray-mpich-gcc/8.1.30 \
      cmake/3.24.4-gcc gmake/4.4.1-gcc \
      netcdf-c/4.8.1-gcc netcdf-fortran/4.5.4-gcc \
      fftw/3.3.10-gcc openblas/0.3.26-gcc
    ;;
  gpu-mpi|gpu-nccl)
    module load nvhpc/24.5 cray-mpich-nvhpc/8.1.30 \
      cuda/12.3.0-gcc cmake/3.24.4-gcc gmake/4.4.1-gcc \
      netcdf-c/4.9.2-nvhpc netcdf-fortran/4.6.1-nvhpc \
      hdf5/1.14.3-nvhpc fftw/3.3.10-gcc
    ;;
  *)
    echo "unsupported HICAR_BUILD_VARIANT: $variant" >&2
    exit 2
    ;;
esac

test -d "$source_root"
test "$(git -C "$source_root" rev-parse HEAD)" = "$expected_commit"
git -C "$source_root" diff --cached --quiet
actual_diff=$(git -C "$source_root" diff --binary | sha256sum | awk '{print $1}')
empty_diff=$(printf '' | sha256sum | awk '{print $1}')
if test -n "$expected_diff"; then
  test "$actual_diff" = "$expected_diff"
else
  test "$actual_diff" = "$empty_diff"
fi
test ! -e "$build_root"

nc_prefix=$(nc-config --prefix)
nf_prefix=$(nf-config --prefix)
fftw_prefix=$(pkg-config --variable=prefix fftw3)
mpi_prefix=$(dirname "$(dirname "$(command -v mpifort)")")
test -f "$nc_prefix/lib/libnetcdf.so"
test -f "$nf_prefix/lib/libnetcdff.so"
test -d "$fftw_prefix"

cmake_args=(
  -S "$source_root"
  -B "$build_root"
  -DFC=mpifort
  -DMODE="$mode"
  -DNETCDF_DIR="$nc_prefix"
  "-DNETCDF_INCLUDES=$nc_prefix/include;$nf_prefix/include"
  -DNETCDF_INCLUDES_F90="$nf_prefix/include"
  -DNETCDF_LIBRARIES_C="$nc_prefix/lib/libnetcdf.so"
  -DNETCDF_LIBRARIES_F90="$nf_prefix/lib/libnetcdff.so"
  -DFFTW_DIR="$fftw_prefix"
  -DMPI_DIR="$mpi_prefix"
)

if [[ "$variant" == cpu ]]; then
  openblas_root=${OPENBLAS_ROOT:?OpenBLAS module did not set OPENBLAS_ROOT}
  test -f "$openblas_root/lib/libopenblas.so"
  cmake_args+=(
    -DOPENACC=OFF
    -DNCCL=OFF
    -DBLAS_LIBRARIES="$openblas_root/lib/libopenblas.so"
    -DLAPACK_LIBRARIES="$openblas_root/lib/libopenblas.so"
    "-DCMAKE_BUILD_RPATH=$nf_prefix/lib;$nc_prefix/lib;$openblas_root/lib"
  )
  executable="$build_root/HICAR"
else
  cuda_target="$CUDA_HOME/targets/x86_64-linux"
  test -f "$cuda_target/lib/libcufft.so"
  cmake_args+=(
    -DOPENACC=ON
    -DGPU_ARCH=cc80
    -DCMAKE_C_COMPILER=nvc
    -DCMAKE_CXX_COMPILER=nvc++
    -DMPI_Fortran_COMPILER="$(command -v mpifort)"
    -DFFTW_INCLUDES="$fftw_prefix/include"
    -DFFTW_LIBRARIES="$fftw_prefix/lib/libfftw3.so"
    -DCUDAToolkit_ROOT="$CUDA_HOME"
    -DCUFFT_LIBRARY="$cuda_target/lib/libcufft.so"
    "-DCMAKE_BUILD_RPATH=$nf_prefix/lib;$nc_prefix/lib"
  )
  executable="$build_root/HICAR_gpu"

  if [[ "$variant" == gpu-mpi ]]; then
    cmake_args+=(-DNCCL=OFF)
  else
    nvhpc_sdk_root=$(dirname "$(dirname "$(dirname "$(command -v nvc)")")")
    nccl_prefix="$nvhpc_sdk_root/comm_libs/12.4/nccl"
    test -f "$nccl_prefix/include/nccl.h"
    test -f "$nccl_prefix/lib/libnccl.so"
    cmake_args+=(
      -DNCCL=ON
      -DNCCL_INCLUDE_DIR="$nccl_prefix/include"
      -DNCCL_LIBRARY="$nccl_prefix/lib/libnccl.so"
      "-DCMAKE_BUILD_RPATH=$nf_prefix/lib;$nc_prefix/lib;$nccl_prefix/lib"
      "-DCMAKE_INSTALL_RPATH=$nf_prefix/lib;$nc_prefix/lib;$nccl_prefix/lib"
    )
  fi
fi

cmake "${cmake_args[@]}"
cmake --build "$build_root" --target HICAR HICAR-tester \
  --parallel "${SLURM_CPUS_PER_TASK:-8}"

test -x "$executable"
tester="$build_root/tests/HICAR-tester"
test -x "$tester"
! ldd "$executable" | grep -q "not found"
! ldd "$tester" | grep -q "not found"
if [[ "$variant" == cpu ]]; then
  ! ldd "$executable" | grep -q "libnccl"
else
  ldd "$executable" | grep -q "libcufft"
  if [[ "$variant" == gpu-nccl ]]; then
    ldd "$executable" | grep -q "libnccl"
    test "$(grep '^NCCL:BOOL=' "$build_root/CMakeCache.txt")" = \
      "NCCL:BOOL=ON"
  else
    ! ldd "$executable" | grep -q "libnccl"
  fi
fi

provenance="$build_root/hicar_build_provenance.txt"
provenance_partial="$provenance.partial.${SLURM_JOB_ID:-$$}"
rm -f "$provenance.ready"
{
  printf 'source_commit=%s\n' "$expected_commit"
  printf 'source_diff_sha256=%s\n' "$actual_diff"
  printf 'variant=%s\n' "$variant"
  printf 'mode=%s\n' "$mode"
  printf 'build_script=%s\n' "$(realpath "$0")"
  sha256sum "$(realpath "$0")"
  printf 'executable=%s\n' "$executable"
  sha256sum "$executable"
  printf 'tester=%s\n' "$tester"
  sha256sum "$tester"
  printf '%s\n' '--- modules ---'
  module -t list 2>&1
  printf '%s\n' '--- linkage ---'
  ldd "$executable"
  printf '%s\n' '--- cmake command ---'
  printf '%q ' cmake "${cmake_args[@]}"
  printf '\n'
} > "$provenance_partial"
mv "$provenance_partial" "$provenance"
touch "$provenance.ready"

printf 'PASS variant=%s commit=%s executable=%s provenance=%s\n' \
  "$variant" "$expected_commit" "$executable" "$provenance"
