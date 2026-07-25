#!/bin/bash -l
#SBATCH --job-name=hicar-ch-build
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --time=00:30:00
#SBATCH --output=/scratch/mch/olifu/icon_hicar/case_studies/swiss_200m/validation/exact_multilevel_build_%j.out
#SBATCH --error=/scratch/mch/olifu/icon_hicar/case_studies/swiss_200m/validation/exact_multilevel_build_%j.err

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module purge || true
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module load nvhpc/24.5 cray-mpich-nvhpc/8.1.30 cuda/12.3.0-gcc \
  cmake/3.24.4-gcc gmake/4.4.1-gcc netcdf-c/4.9.2-nvhpc \
  netcdf-fortran/4.6.1-nvhpc hdf5/1.14.3-nvhpc fftw/3.3.10-gcc

root=${HICAR_MULTILEVEL_ROOT:-$SCRATCH/icon_hicar/HICAR-wind-galerkin-multilevel}
build="$root/build_gpu_nccl"
git -C "$root" fetch origin feature/wind-galerkin-multilevel
git -C "$root" checkout feature/wind-galerkin-multilevel
git -C "$root" merge --ff-only origin/feature/wind-galerkin-multilevel
# Reconfigure even for an incremental build so the embedded version metadata
# follows the audited commit rather than the commit that created the cache.
cmake -S "$root" -B "$build"
cmake --build "$build" --target HICAR HICAR-tester --parallel "$SLURM_CPUS_PER_TASK"
git -C "$root" rev-parse HEAD
sha256sum "$build/HICAR_gpu" "$build/tests/HICAR-tester"
