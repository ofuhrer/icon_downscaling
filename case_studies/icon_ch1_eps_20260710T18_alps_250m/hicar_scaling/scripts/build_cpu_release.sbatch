#!/bin/bash
#SBATCH --job-name=hicar_scaling_cpu_build
#SBATCH --partition=pp-short
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --time=01:00:00
set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"; module purge || true; module use "$USER_ENV_ROOT/modules"
module load gcc/12.3.0 cray-mpich-gcc/8.1.30 cmake/3.24.4-gcc gmake/4.4.1-gcc netcdf-c/4.8.1-gcc netcdf-fortran/4.5.4-gcc fftw/3.3.10-gcc
ROOT=${SCALING_ROOT:?}; SRC=${HICAR_ROOT:-$SCRATCH/icon_hicar/HICAR-scaling}; BUILD="$SRC/build_scaling_cpu_release"
# Keep each toolchain's FetchContent cache independent of concurrent builds.
export HICAR_DEPS_SRC_CACHE="${HICAR_DEPS_SRC_CACHE:-$SRC/.hicar-deps-cache-cpu}"
mkdir -p "$ROOT/provenance"
git -C "$SRC" rev-parse HEAD > "$ROOT/provenance/cpu_git_sha.txt"
git -C "$SRC" diff --binary | sha256sum > "$ROOT/provenance/cpu_dirty_diff.sha256"
rm -rf "$BUILD"
nc_prefix=$(nc-config --prefix); nf_prefix=$(nf-config --prefix); fftw_prefix=$(pkg-config --variable=prefix fftw3); mpi_prefix=$(dirname "$(dirname "$(command -v mpifort)")")
cmake -S "$SRC" -B "$BUILD" -DMODE=release -DFC=mpifort -DOPENACC=OFF -DNCCL=OFF -DNETCDF_DIR="$nc_prefix" -DNETCDF_INCLUDES="$nc_prefix/include;$nf_prefix/include" -DNETCDF_INCLUDES_F90="$nf_prefix/include" -DNETCDF_LIBRARIES_C="$nc_prefix/lib/libnetcdf.so" -DNETCDF_LIBRARIES_F90="$nf_prefix/lib/libnetcdff.so" -DFFTW_DIR="$fftw_prefix" -DMPI_DIR="$mpi_prefix"
cmake --build "$BUILD" --target HICAR --parallel "${SLURM_CPUS_PER_TASK:-8}"
cmake --install "$BUILD"
ln -sf HICAR "$SRC/bin/HICAR_release"
sha256sum "$SRC/bin/HICAR_release" > "$ROOT/provenance/cpu_executable.sha256"
"$SRC/bin/HICAR_release" --help >/dev/null
echo "CPU build complete; submit validate_cpu_release.sbatch before benchmark runs."
