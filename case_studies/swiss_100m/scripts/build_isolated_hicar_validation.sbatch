#!/bin/bash -l
# Isolated GPU validation build; does not use the shared HICAR worktree.
#SBATCH --job-name=hicar_swiss_init_build
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --time=00:30:00
#SBATCH --output=/scratch/mch/olifu/icon_hicar/HICAR-swiss-init-validation/build_%j.out
#SBATCH --error=/scratch/mch/olifu/icon_hicar/HICAR-swiss-init-validation/build_%j.err

set -euo pipefail

[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
export USER_ENV_ROOT=/mch-environment/v8
module use "$USER_ENV_ROOT/modules"
module load nvhpc/24.5 cray-mpich-nvhpc/8.1.30 cuda/12.3.0-gcc \
  cmake/3.24.4-gcc gmake/4.4.1-gcc netcdf-c/4.9.2-nvhpc \
  netcdf-fortran/4.6.1-nvhpc hdf5/1.14.3-nvhpc fftw/3.3.10-gcc

ROOT=/scratch/mch/olifu/icon_hicar/HICAR-swiss-init-validation
NETCDF_C_PREFIX=$(nc-config --prefix)
NETCDF_F_PREFIX=$(nf-config --prefix)
export NCCL_DIR=$(dirname "$(dirname "$(ldd /scratch/mch/olifu/icon_hicar/HICAR-scaling/bin/HICAR_gpu | awk '/libnccl.so/{print $3; exit}')")")
[[ -f "$NCCL_DIR/include/nccl.h" && -f "$NCCL_DIR/lib/libnccl.so" ]]
if [[ ! -f "$ROOT/build_gpu_nccl/CMakeCache.txt" ]]; then
  cmake -S "$ROOT" -B "$ROOT/build_gpu_nccl" -DFC=mpifort -DMODE=release \
    -DGPU_ARCH=cc80 -DOPENACC=ON -DNCCL=ON \
    -DNETCDF_INCLUDES="$NETCDF_C_PREFIX/include;$NETCDF_F_PREFIX/include" \
    -DNETCDF_LIBRARIES_C="$NETCDF_C_PREFIX/lib/libnetcdf.so" \
    -DNETCDF_INCLUDES_F90="$NETCDF_F_PREFIX/include" \
    -DNETCDF_LIBRARIES_F90="$NETCDF_F_PREFIX/lib/libnetcdff.so"
fi
cmake --build "$ROOT/build_gpu_nccl" --target HICAR --parallel "$SLURM_CPUS_PER_TASK"
mkdir -p "$ROOT/bin"
cp -f "$ROOT/build_gpu_nccl/HICAR_gpu" "$ROOT/bin/HICAR_gpu"
sha256sum "$ROOT/bin/HICAR_gpu"
