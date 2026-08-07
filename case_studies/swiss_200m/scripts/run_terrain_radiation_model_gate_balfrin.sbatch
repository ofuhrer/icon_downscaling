#!/bin/bash -l
# Coupled flat/blocked terrain-radiation component and restart gate.
#SBATCH --job-name=terrain-rad-gate
#SBATCH --partition=normal
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=2
#SBATCH --cpus-per-task=1
#SBATCH --gres=gpu:1
#SBATCH --time=01:00:00
#SBATCH --exclusive
#SBATCH --output=terrain_rad_gate_%j.out
#SBATCH --error=terrain_rad_gate_%j.err

set -euo pipefail

[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module purge || true
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module load nvhpc/24.5 cray-mpich-nvhpc/8.1.30 cuda/12.3.0-gcc \
  netcdf-c/4.9.2-nvhpc netcdf-fortran/4.6.1-nvhpc \
  hdf5/1.14.3-nvhpc fftw/3.3.10-gcc

coordinator=${HICAR_COORDINATOR_ROOT:?Set HICAR_COORDINATOR_ROOT}
source_root=${HICAR_TERRAIN_SOURCE_ROOT:?Set HICAR_TERRAIN_SOURCE_ROOT}
build_root=${HICAR_TERRAIN_BUILD_ROOT:?Set HICAR_TERRAIN_BUILD_ROOT}
expected_commit=${HICAR_TERRAIN_EXPECTED_COMMIT:?Set HICAR_TERRAIN_EXPECTED_COMMIT}
input_root=${HICAR_TERRAIN_INPUT_ROOT:?Set HICAR_TERRAIN_INPUT_ROOT}
flat_static=${HICAR_TERRAIN_FLAT_STATIC:?Set HICAR_TERRAIN_FLAT_STATIC}
blocked_static=${HICAR_TERRAIN_BLOCKED_STATIC:?Set HICAR_TERRAIN_BLOCKED_STATIC}
run_root=${HICAR_TERRAIN_RUN_ROOT:?Set HICAR_TERRAIN_RUN_ROOT}
python=${HICAR_VALIDATION_PYTHON:-${SCRATCH:?}/icon_hicar/venv_static/bin/python}
renderer=$coordinator/case_studies/swiss_200m/scripts/render_hicar_namelist.py
wrapper=${HICAR_GPU_WRAPPER:-$coordinator/case_studies/swiss_200m/scripts/gpu_micro_rank_wrapper.sh}
exe=$build_root/HICAR_gpu

for path in \
  "$python" "$renderer" "$wrapper" "$exe" \
  "$build_root/hicar_build_provenance.txt.ready" \
  "$input_root/execution_plan.json.ready" \
  "$flat_static" "${flat_static}.ready" \
  "$blocked_static" "${blocked_static}.ready"; do
  test -e "$path" || { echo "missing required input: $path" >&2; exit 2; }
done
test "$(git -C "$source_root" rev-parse HEAD)" = "$expected_commit"
test -z "$(git -C "$source_root" status --porcelain)"
test ! -e "$run_root"
mkdir -p "$run_root"

prepare_case() {
  local label=$1 static=$2 profile=$3 forcing_list=$4 start=$5 end=$6
  local restart_interval=${7:-0} restart_from=${8:-}
  local root=$run_root/$label
  mkdir -p "$root/output" "$root/restart"
  for asset in NoahmpTable.TBL rrtmgp_support mp_support; do
    ln -s "$source_root/run/$asset" "$root/$asset"
  done
  args=(
    "$python" "$renderer"
    --static-file "$static"
    --forcing-file-list "$forcing_list"
    --start-date "$start" --end-date "$end"
    --forcing-interval 1800
    --output-interval 300
    --radiation-update-interval 300
    --output-profile terrain_radiation_gate
    --terrain-radiation-profile "$profile"
    --output-dir "$root/output"
    --restart-dir "$root/restart"
    --restart-interval "$restart_interval"
    --output "$root/input.nml"
  )
  if [[ -n "$restart_from" ]]; then
    args+=(--restart-from "$restart_from")
  fi
  "${args[@]}"
}

continuous_list=$input_root/forcing_continuous.txt
first_list=$input_root/forcing_split_first.txt
second_list=$input_root/forcing_split_second.txt
for path in "$continuous_list" "$first_list" "$second_list"; do
  test -f "$path" && test -f "${path}.ready"
done

prepare_case flat_off "$flat_static" off "$continuous_list" \
  '2020-07-20 06:30:00' '2020-07-20 09:30:00'
prepare_case flat_direct "$flat_static" direct "$continuous_list" \
  '2020-07-20 06:30:00' '2020-07-20 09:30:00'
prepare_case flat_direct_diffuse "$flat_static" direct-diffuse "$continuous_list" \
  '2020-07-20 06:30:00' '2020-07-20 09:30:00'
prepare_case blocked_direct "$blocked_static" direct "$continuous_list" \
  '2020-07-20 06:30:00' '2020-07-20 09:30:00'
prepare_case blocked_direct_diffuse "$blocked_static" direct-diffuse "$continuous_list" \
  '2020-07-20 06:30:00' '2020-07-20 09:30:00'
prepare_case restart_first "$blocked_static" direct-diffuse "$first_list" \
  '2020-07-20 06:30:00' '2020-07-20 08:00:00' 18

export OMP_NUM_THREADS=1
export MPICH_GPU_SUPPORT_ENABLED=0
export MPICH_GPU_MANAGED_MEMORY_SUPPORT_ENABLED=0
export HICAR_WIND_ADJOINT_PROJECTION=1
export HICAR_WIND_RECYCLE_DIM=0
unset HICAR_WIND_MULTILEVEL
unset HICAR_WIND_OPERATOR_AUDIT
unset HICAR_WIND_OPERATOR_AUDIT_MAX_ITERS

run_case() {
  local label=$1
  (
    cd "$run_root/$label"
    srun --distribution=block:block --cpu-bind=none \
      "$wrapper" "$exe" "$run_root/$label/input.nml" 2>&1 | tee model.out
  )
  grep -q 'Simulation completed successfully!' "$run_root/$label/model.out"
  ! grep -q 'Accelerator Fatal Error' "$run_root/$label/model.out"
}

for label in flat_off flat_direct flat_direct_diffuse blocked_direct blocked_direct_diffuse restart_first; do
  run_case "$label"
done

shopt -s nullglob
restart_files=("$run_root/restart_first/restart/"*'_2020-07-20_08-00-00.nc')
shopt -u nullglob
test "${#restart_files[@]}" -eq 1
prepare_case restart_second "$blocked_static" direct-diffuse "$second_list" \
  '2020-07-20 08:00:00' '2020-07-20 09:30:00' 18 '2020-07-20 08:00:00'
ln "${restart_files[0]}" "$run_root/restart_second/restart/$(basename "${restart_files[0]}")"
run_case restart_second

"$python" - "$run_root" "$expected_commit" "$exe" "$input_root/execution_plan.json" <<'PY'
from hashlib import sha256
import json
from pathlib import Path
import sys

run_root = Path(sys.argv[1])
commit, executable, input_plan = sys.argv[2:]
def digest(path):
    h = sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()
cases = {}
for case in sorted(path.name for path in run_root.iterdir() if path.is_dir()):
    root = run_root / case
    cases[case] = {
        'namelist_sha256': digest(root / 'input.nml'),
        'output_files': [str(path) for path in sorted((root / 'output').glob('*.nc'))],
        'model_log_sha256': digest(root / 'model.out'),
    }
manifest = {
    'schema': 'hicar-terrain-radiation-run/v1',
    'status': 'MODEL_RUNS_COMPLETE_ASSESSMENT_PENDING',
    'source_commit': commit,
    'executable': {'path': executable, 'sha256': digest(executable)},
    'input_plan': {'path': input_plan, 'sha256': digest(input_plan)},
    'cases': cases,
    'promotion_limit': 'Synthetic causal/restart qualification only.',
}
partial = run_root / 'run_manifest.json.partial'
partial.write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
partial.replace(run_root / 'run_manifest.json')
(run_root / 'run_manifest.json.ready').touch()
PY

printf 'PASS run_root=%s\n' "$run_root"
