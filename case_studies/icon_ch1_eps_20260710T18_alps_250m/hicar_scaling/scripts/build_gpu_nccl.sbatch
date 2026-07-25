#!/bin/bash
#SBATCH --job-name=hicar_scaling_gpu_build
#SBATCH --partition=short
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --time=01:00:00
set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"; module purge || true; module use "$USER_ENV_ROOT/modules"
module load nvhpc/24.5 cray-mpich-nvhpc/8.1.30 cuda/12.3.0-gcc cmake/3.24.4-gcc gmake/4.4.1-gcc netcdf-c/4.9.2-nvhpc netcdf-fortran/4.6.1-nvhpc hdf5/1.14.3-nvhpc fftw/3.3.10-gcc
ROOT=${SCALING_ROOT:?}; SRC=${HICAR_ROOT:-$SCRATCH/icon_hicar/HICAR-scaling}; BUILD="$SRC/build_scaling_gpu_nccl"
# Keep each toolchain's FetchContent cache independent.  Concurrent CPU/GPU
# reconfigures otherwise race while refreshing the same Snowpack clone.
export HICAR_DEPS_SRC_CACHE="${HICAR_DEPS_SRC_CACHE:-$SRC/.hicar-deps-cache-gpu}"
mkdir -p "$ROOT/provenance"
git -C "$SRC" rev-parse HEAD > "$ROOT/provenance/gpu_git_sha.txt"
git -C "$SRC" diff --binary | sha256sum > "$ROOT/provenance/gpu_dirty_diff.sha256"
# Keep the default clean rebuild for release provenance.  Screening jobs can
# set HICAR_INCREMENTAL_BUILD=1 to retain the configured build and recompile
# only changed sources.
if [[ ${HICAR_INCREMENTAL_BUILD:-0} != 1 ]]; then
  rm -rf "$BUILD"
fi
nc_prefix=$(nc-config --prefix); nf_prefix=$(nf-config --prefix); fftw_prefix=$(pkg-config --variable=prefix fftw3); mpi_prefix=$(dirname "$(dirname "$(command -v mpifort)")"); cuda_target="$CUDA_HOME/targets/x86_64-linux"
nvhpc_root=$(dirname "$(dirname "$(dirname "$(command -v nvfortran)")")")
export NCCL_DIR=$(dirname "$(dirname "$(find "$nvhpc_root/comm_libs" -name libnccl.so -print -quit)")")
test -f "$NCCL_DIR/include/nccl.h" && test -f "$NCCL_DIR/lib/libnccl.so"
export LD_LIBRARY_PATH="$nf_prefix/lib:$nc_prefix/lib:$NCCL_DIR/lib:${LD_LIBRARY_PATH:-}"
cmake -S "$SRC" -B "$BUILD" -DMODE=release -DFC=mpifort -DOPENACC=ON -DNCCL=ON -DGPU_ARCH=cc80 -DCMAKE_C_COMPILER=nvc -DCMAKE_CXX_COMPILER=nvc++ -DNETCDF_DIR="$nc_prefix" -DNETCDF_INCLUDES="$nc_prefix/include;$nf_prefix/include" -DNETCDF_INCLUDES_F90="$nf_prefix/include" -DNETCDF_LIBRARIES_C="$nc_prefix/lib/libnetcdf.so" -DNETCDF_LIBRARIES_F90="$nf_prefix/lib/libnetcdff.so" -DFFTW_DIR="$fftw_prefix" -DFFTW_INCLUDES="$fftw_prefix/include" -DFFTW_LIBRARIES="$fftw_prefix/lib/libfftw3.so" -DMPI_DIR="$mpi_prefix" -DCUDAToolkit_ROOT="$CUDA_HOME" -DCUFFT_LIBRARY="$cuda_target/lib/libcufft.so" -DCMAKE_BUILD_RPATH="$nf_prefix/lib;$nc_prefix/lib;$NCCL_DIR/lib" -DCMAKE_INSTALL_RPATH="$nf_prefix/lib;$nc_prefix/lib;$NCCL_DIR/lib"
cmake --build "$BUILD" --target HICAR --parallel "${SLURM_CPUS_PER_TASK:-8}"
cmake --install "$BUILD"
test -x "$SRC/bin/HICAR_gpu"
ldd "$SRC/bin/HICAR_gpu" | tee "$ROOT/provenance/gpu_ldd.txt"
ldd "$SRC/bin/HICAR_gpu" | grep -q libcufft
ldd "$SRC/bin/HICAR_gpu" | grep -q nccl
! ldd "$SRC/bin/HICAR_gpu" | grep -q 'not found'
sha256sum "$SRC/bin/HICAR_gpu" > "$ROOT/provenance/gpu_executable.sha256"
"$SRC/bin/HICAR_gpu" --help >/dev/null
echo "GPU build passed link checks; submit validate_gpu_nccl.sbatch before benchmark runs."
