#!/bin/bash -l
#SBATCH --job-name=hicar-250m-ml
#SBATCH --partition=debug
#SBATCH --nodes=2
#SBATCH --ntasks-per-node=5
#SBATCH --cpus-per-task=1
#SBATCH --gres=gpu:4
#SBATCH --time=00:25:00
#SBATCH --exclusive
#SBATCH --output=/scratch/mch/olifu/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/validation/exact_multilevel_%j.out
#SBATCH --error=/scratch/mch/olifu/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/validation/exact_multilevel_%j.err

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module purge || true
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module load nvhpc/24.5 cray-mpich-nvhpc/8.1.30 cuda/12.3.0-gcc \
  netcdf-c/4.9.2-nvhpc netcdf-fortran/4.6.1-nvhpc hdf5/1.14.3-nvhpc fftw/3.3.10-gcc

root=${HICAR_MULTILEVEL_ROOT:-$SCRATCH/icon_hicar/HICAR-wind-galerkin-multilevel}
case_root="$SCRATCH/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m"
exe="$root/build_gpu_nccl/HICAR_gpu"
nml_src="$case_root/hicar_wind_design_sensitivity_24h/v2_auto1_n80_top12_s26/input/icon_hicar_250m_v2_auto1_n80_top12_s26.nml"
wrapper="$SCRATCH/icon_hicar/case_studies/swiss_200m/scripts/gpu_rank_wrapper.sh"
run="$case_root/validation/exact_multilevel_${SLURM_JOB_ID:?}"

test -x "$exe" && test -f "$nml_src" && test -x "$wrapper"
mkdir -p "$run/output" "$run/restart"
sed -e "/^[[:space:]]*restart_folder[[:space:]]*=/c\  restart_folder = '$run/restart/'" \
    -e "/^[[:space:]]*output_folder[[:space:]]*=/c\  output_folder = '$run/output/'" \
    "$nml_src" > "$run/input.nml"
for asset in NoahmpTable.TBL rrtmgp_support mp_support; do cp -a "$root/run/$asset" "$run/"; done

export OMP_NUM_THREADS=1
export MPICH_GPU_SUPPORT_ENABLED=0
export MPICH_GPU_MANAGED_MEMORY_SUPPORT_ENABLED=0
export HICAR_WIND_MULTILEVEL=1
export HICAR_WIND_RECYCLE_DIM=0

cd "$run"
srun --distribution=block:block --cpu-bind=none "$wrapper" "$exe" "$run/input.nml" \
  2>&1 | tee "$run/model.out"

grep -q 'Projection operator equivalence: relative L2=' "$run/model.out"
grep -q 'HICAR terminal collective solve gate:' "$run/model.out"
grep -q 'HICAR exact Galerkin hierarchy ready:' "$run/model.out"
grep -q 'HICAR native FGMRES+line:' "$run/model.out"
grep -q 'Timing across all compute images:' "$run/model.out"
test -n "$(find "$run/output" -name '*.nc' -print -quit)"
