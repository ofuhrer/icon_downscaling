#!/bin/bash
#SBATCH --job-name=hicar_acc_bld
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --time=01:00:00
#SBATCH --output=/scratch/mch/olifu/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_openacc_1h/logs/hicar_openacc_build_%j.out
#SBATCH --error=/scratch/mch/olifu/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_openacc_1h/logs/hicar_openacc_build_%j.err

set -euo pipefail

[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"
module purge || true
module use "$USER_ENV_ROOT/modules"
module load nvhpc/24.5 \
  cray-mpich-nvhpc/8.1.30 \
  cuda/12.3.0-gcc \
  cmake/3.24.4-gcc \
  gmake/4.4.1-gcc \
  netcdf-c/4.9.2-nvhpc \
  netcdf-fortran/4.6.1-nvhpc \
  hdf5/1.14.3-nvhpc \
  fftw/3.3.10-gcc

SRC="$SCRATCH/icon_hicar/HICAR"
BUILD="$SRC/build_balfrin_openacc_a100"

cd "$SRC"
echo "Building HICAR OpenACC A100 executable from:"
git status --short --branch || true
git rev-parse --short HEAD || true

rm -rf "$BUILD"
mkdir -p "$BUILD"

nc_prefix=$(nc-config --prefix)
nf_prefix=$(nf-config --prefix)
fftw_prefix=$(pkg-config --variable=prefix fftw3)
mpi_prefix=$(dirname "$(dirname "$(command -v mpifort)")")
cuda_target="$CUDA_HOME/targets/x86_64-linux"

cmake -S "$SRC" -B "$BUILD" \
  -DMODE=release \
  -DFC=mpifort \
  -DGPU_ARCH=cc80 \
  -DOPENACC=ON \
  -DNCCL=OFF \
  -DCMAKE_C_COMPILER=nvc \
  -DCMAKE_CXX_COMPILER=nvc++ \
  -DMPI_Fortran_COMPILER="$(command -v mpifort)" \
  -DNETCDF_DIR="$nc_prefix" \
  -DNETCDF_INCLUDES="$nc_prefix/include;$nf_prefix/include" \
  -DNETCDF_INCLUDES_F90="$nf_prefix/include" \
  -DNETCDF_LIBRARIES_C="$nc_prefix/lib/libnetcdf.so" \
  -DNETCDF_LIBRARIES_F90="$nf_prefix/lib/libnetcdff.so" \
  -DFFTW_DIR="$fftw_prefix" \
  -DFFTW_INCLUDES="$fftw_prefix/include" \
  -DFFTW_LIBRARIES="$fftw_prefix/lib/libfftw3.so" \
  -DMPI_DIR="$mpi_prefix" \
  -DCUDAToolkit_ROOT="$CUDA_HOME" \
  -DCUFFT_LIBRARY="$cuda_target/lib/libcufft.so"

cmake --build "$BUILD" --target HICAR --parallel "${SLURM_CPUS_PER_TASK:-8}"
cmake --install "$BUILD"

echo "Installed OpenACC executable:"
ls -l "$SRC/bin/HICAR_gpu"
"$SRC/bin/HICAR_gpu" --help >/dev/null

echo "cuFFT linkage check:"
ldd "$SRC/bin/HICAR_gpu" | grep -E "cufft|cuda|netcdf|netcdff|mpi|not found" || true
if ldd "$SRC/bin/HICAR_gpu" | grep -q "not found"; then
  exit 2
fi
if ! ldd "$SRC/bin/HICAR_gpu" | grep -q "libcufft"; then
  echo "HICAR_gpu did not link libcufft" >&2
  exit 3
fi
