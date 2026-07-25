#!/bin/bash -l
#SBATCH --job-name=hicar-bridge-ml
#SBATCH --partition=debug
#SBATCH --nodes=2
#SBATCH --ntasks-per-node=5
#SBATCH --cpus-per-task=1
#SBATCH --gres=gpu:4
#SBATCH --time=00:30:00
#SBATCH --exclusive
#SBATCH --output=/scratch/mch/olifu/icon_hicar/case_studies/alpine_bridge_200m/validation/multilevel_audit_%j.out
#SBATCH --error=/scratch/mch/olifu/icon_hicar/case_studies/alpine_bridge_200m/validation/multilevel_audit_%j.err

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module purge || true
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module load nvhpc/24.5 cray-mpich-nvhpc/8.1.30 cuda/12.3.0-gcc \
  netcdf-c/4.9.2-nvhpc netcdf-fortran/4.6.1-nvhpc hdf5/1.14.3-nvhpc fftw/3.3.10-gcc

root=${HICAR_MULTILEVEL_ROOT:-$SCRATCH/icon_hicar/HICAR-wind-galerkin-multilevel}
case_root=${HICAR_BRIDGE_CASE:-$SCRATCH/icon_hicar/case_studies/alpine_bridge_200m}
exe="$root/build_gpu_nccl/HICAR_gpu"
tester="$root/build_gpu_nccl/tests/HICAR-tester"
nml_src=${HICAR_BRIDGE_NML_SOURCE:-$SCRATCH/icon_hicar/case_studies/swiss_200m/runs/integration_20100101_0030_dynamic_prec4/hicar.nml}
static_file="$case_root/static/domain_static_alpine_bridge_200m.nc"
wrapper="$SCRATCH/icon_hicar/case_studies/swiss_200m/scripts/gpu_rank_wrapper.sh"
run="$case_root/runs/multilevel_audit_${SLURM_JOB_ID:?}"

test -x "$exe" && test -x "$tester" && test -f "$nml_src" && test -x "$wrapper"
test -f "$static_file" && test -f "$static_file.ready"
export OMP_NUM_THREADS=1
export MPICH_GPU_SUPPORT_ENABLED=0
export MPICH_GPU_MANAGED_MEMORY_SUPPORT_ENABLED=0

srun -N1 -n4 --ntasks-per-node=4 --gres=gpu:4 --distribution=block:block --cpu-bind=none \
  bash -c 'export ACC_DEVICE_TYPE=nvidia ACC_DEVICE_NUM=0 CUDA_VISIBLE_DEVICES=$SLURM_LOCALID; exec "$1" -v halo_exch' _ "$tester" \
  2>&1 | tee "$case_root/validation/multilevel_audit_${SLURM_JOB_ID}.halo.out"
grep -q 'PASSED' "$case_root/validation/multilevel_audit_${SLURM_JOB_ID}.halo.out"

mkdir -p "$run/output" "$run/restart"
forcing_list_src=$(awk -F"'" '/^[[:space:]]*forcing_file_list[[:space:]]*=/{print $2; exit}' "$nml_src")
forcing_list="$run/forcing_files.txt"
awk 'NF && $1 !~ /^#/ {gsub(/^"|"$/, "", $0); printf "\"%s\"\n", $0}' "$forcing_list_src" > "$forcing_list"
sed -e "/^[[:space:]]*restart_folder[[:space:]]*=/c\  restart_folder = '$run/restart/'" \
    -e "/^[[:space:]]*output_folder[[:space:]]*=/c\  output_folder = '$run/output/'" \
    -e "/^[[:space:]]*forcing_file_list[[:space:]]*=/c\  forcing_file_list = '$forcing_list'" \
    -e "/^[[:space:]]*init_conditions_file[[:space:]]*=/c\  init_conditions_file = '$static_file'" \
    "$nml_src" > "$run/input.nml"
for asset in NoahmpTable.TBL rrtmgp_support mp_support; do cp -a "$root/run/$asset" "$run/"; done
while IFS= read -r forcing; do
  forcing=${forcing#\"}; forcing=${forcing%\"}
  test -f "$forcing" && test -f "$forcing.ready"
done < "$forcing_list"

export HICAR_WIND_MULTILEVEL=1
export HICAR_WIND_OPERATOR_AUDIT=1
export HICAR_WIND_OPERATOR_AUDIT_FILE="$run/operator_audit.csv"
export HICAR_WIND_OPERATOR_AUDIT_MAX_ITERS=100
cd "$run"
set +e
srun --distribution=block:block --cpu-bind=none "$wrapper" "$exe" "$run/input.nml" 2>&1 | tee "$run/model.out"
model_rc=${PIPESTATUS[0]}
set -e

grep -q 'Projection operator equivalence: relative L2=' "$run/model.out"
grep -q 'HICAR terminal collective solve gate:' "$run/model.out"
grep -q 'HICAR exact Galerkin hierarchy ready:' "$run/model.out"
grep -q 'HICAR wind Krylov audit written to' "$run/model.out"
grep -q 'HICAR operator audit-only exit:' "$run/model.out"
test -s "$run/operator_audit.csv"
test "$model_rc" -ne 0
echo "Regional audit-only gate completed as designed (model rc=$model_rc)."
