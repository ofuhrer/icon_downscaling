#!/bin/bash -l
# Full Swiss 200 m gate: four compute GPUs and one CPU I/O rank per node.
#SBATCH --job-name=hicar_swiss_200m_capacity
#SBATCH --partition=normal
#SBATCH --nodes=4
#SBATCH --ntasks-per-node=5
#SBATCH --gres=gpu:4
#SBATCH --time=02:00:00
#SBATCH --output=%x_%j.out
#SBATCH --error=%x_%j.err

set -euo pipefail
: "${HICAR_GPU:?}"; : "${HICAR_RUNTIME_ROOT:?}"; : "${NML:?}"
[[ -x "$HICAR_GPU" && -f "$NML" ]]
for p in "$HICAR_RUNTIME_ROOT/run/NoahmpTable.TBL" "$HICAR_RUNTIME_ROOT/run/rrtmgp_support" "$HICAR_RUNTIME_ROOT/run/mp_support"; do [[ -e "$p" ]]; done
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
: "${USER_ENV_ROOT:?}"
module use "$USER_ENV_ROOT/modules"; module purge || true; module use "$USER_ENV_ROOT/modules"
module load nvhpc/24.5 cray-mpich-nvhpc/8.1.30 cuda/12.3.0-gcc netcdf-c/4.9.2-nvhpc netcdf-fortran/4.6.1-nvhpc hdf5/1.14.3-nvhpc fftw/3.3.10-gcc
export HICAR_IO_PER_NODE=1 HICAR_TASKS_PER_NODE=5 OMP_NUM_THREADS=1
export MPICH_GPU_SUPPORT_ENABLED=0 MPICH_GPU_MANAGED_MEMORY_SUPPORT_ENABLED=0
CASE="${CASE_ROOT:-${SLURM_SUBMIT_DIR:?}}"; CASE=$(CDPATH= cd -- "$CASE" && pwd)
WRAPPER="$CASE/scripts/gpu_rank_wrapper.sh"; [[ -x "$WRAPPER" ]]
RUN_DIR=$(CDPATH= cd -- "$(dirname -- "$NML")" && pwd)
rm -f "$RUN_DIR/NoahmpTable.TBL"; rm -rf "$RUN_DIR/rrtmgp_support" "$RUN_DIR/mp_support"
cp -f "$HICAR_RUNTIME_ROOT/run/NoahmpTable.TBL" "$RUN_DIR/"
cp -a "$HICAR_RUNTIME_ROOT/run/rrtmgp_support" "$RUN_DIR/"
cp -a "$HICAR_RUNTIME_ROOT/run/mp_support" "$RUN_DIR/"
STATIC_FILE=$(awk -F"'" '/^[[:space:]]*init_conditions_file[[:space:]]*=/{print $2; exit}' "$NML")
FORCING_LIST=$(awk -F"'" '/^[[:space:]]*forcing_file_list[[:space:]]*=/{print $2; exit}' "$NML")
[[ -n "$STATIC_FILE" && -f "$STATIC_FILE" && -n "$FORCING_LIST" && -f "$FORCING_LIST" ]]
READY_INPUTS="$RUN_DIR/published_inputs_${SLURM_JOB_ID}.txt"
{ printf '%s\n' "$STATIC_FILE"; awk 'NF && $1 !~ /^#/ {print $1}' "$FORCING_LIST"; } > "$READY_INPUTS"
[[ $(wc -l < "$READY_INPUTS") -ge 3 ]]
srun --distribution=block:block --cpu-bind=none bash -c '
  while IFS= read -r path; do [[ -f "$path" && -f "$path.ready" ]] || { echo "rank ${SLURM_PROCID}: unpublished $path" >&2; exit 2; }; done < "$1"
' _ "$READY_INPUTS"
rm -f "$READY_INPUTS"
cd "$RUN_DIR"
srun --distribution=block:block --cpu-bind=none "$WRAPPER" "$HICAR_GPU" "$NML"
