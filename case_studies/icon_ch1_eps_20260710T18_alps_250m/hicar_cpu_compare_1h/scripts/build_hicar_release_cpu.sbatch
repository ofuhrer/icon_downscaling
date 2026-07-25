#!/bin/bash
#SBATCH --job-name=hicar_cpu_rel
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --time=01:00:00
#SBATCH --output=/scratch/mch/olifu/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_cpu_compare_1h/logs/hicar_cpu_rel_build_%j.out
#SBATCH --error=/scratch/mch/olifu/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_cpu_compare_1h/logs/hicar_cpu_rel_build_%j.err

set -euo pipefail

[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"
module purge || true
module use "$USER_ENV_ROOT/modules"
module load gcc/12.3.0 \
  cray-mpich-gcc/8.1.30 \
  cmake/3.24.4-gcc \
  gmake/4.4.1-gcc \
  netcdf-c/4.8.1-gcc \
  netcdf-fortran/4.5.4-gcc \
  fftw/3.3.10-gcc

SRC="$SCRATCH/icon_hicar/HICAR"
BUILD="$SRC/build_balfrin_cpu_release"

cd "$SRC"
echo "Building HICAR release CPU executable from:"
git status --short --branch || true
git rev-parse --short HEAD || true

rm -rf "$BUILD"
mkdir -p "$BUILD"

nc_prefix=$(nc-config --prefix)
nf_prefix=$(nf-config --prefix)
fftw_prefix=$(pkg-config --variable=prefix fftw3)
mpi_prefix=$(dirname "$(dirname "$(command -v mpifort)")")

cmake -S "$SRC" -B "$BUILD" \
  -DMODE=release \
  -DFC=mpifort \
  -DOPENACC=OFF \
  -DNCCL=OFF \
  -DNETCDF_DIR="$nc_prefix" \
  -DNETCDF_INCLUDES="$nc_prefix/include;$nf_prefix/include" \
  -DNETCDF_INCLUDES_F90="$nf_prefix/include" \
  -DNETCDF_LIBRARIES_C="$nc_prefix/lib/libnetcdf.so" \
  -DNETCDF_LIBRARIES_F90="$nf_prefix/lib/libnetcdff.so" \
  -DFFTW_DIR="$fftw_prefix" \
  -DMPI_DIR="$mpi_prefix"

cmake --build "$BUILD" --target HICAR --parallel "${SLURM_CPUS_PER_TASK:-8}"
cmake --install "$BUILD"

echo "Installed release executable:"
ls -l "$SRC/bin/HICAR"
"$SRC/bin/HICAR" --help >/dev/null
