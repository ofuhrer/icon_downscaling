#!/bin/bash -l
# Run one restartable R&D segment. The campaign driver retries interrupted
# segments; only segment.complete marks a scientifically usable result.
#SBATCH --job-name=hicar-segment
#SBATCH --partition=preemptible
#SBATCH --nodes=2
#SBATCH --ntasks-per-node=5
#SBATCH --cpus-per-task=1
#SBATCH --gres=gpu:4
#SBATCH --time=06:00:00
#SBATCH --exclusive
#SBATCH --signal=B:TERM@60

set -euo pipefail

repo_root=${REPO_ROOT:?}
. "$repo_root/scripts/load_balfrin_site_config.sh"
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module purge || true
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module load nvhpc/24.5 cray-mpich-nvhpc/8.1.30 cuda/12.3.0-gcc \
  netcdf-c/4.9.2-nvhpc netcdf-fortran/4.6.1-nvhpc \
  hdf5/1.14.3-nvhpc fftw/3.3.10-gcc

partition=$(scontrol show partition preemptible -o)
case "$partition" in
  *AllowGroups=ALL*|*AllowGroups=s83*) ;;
  *) echo "preemptible is not currently allowed for group s83" >&2; exit 2 ;;
esac

exe=${HICAR_EXE:?}
support=${HICAR_SUPPORT_DIR:?}
build_provenance=${HICAR_BUILD_PROVENANCE:?}
static=${HICAR_STATIC_FILE:?}
forcing_list=${FORCING_FILE_LIST:?}
lbc_list=${SPARSE_LBC_FILE_LIST:-}
start=${SEGMENT_START:?}
end=${SEGMENT_END:?}
model_end=${SEGMENT_MODEL_END:-$end}
run=${SEGMENT_RUN_DIR:?}
restart_input=${RESTART_INPUT:-}
profile=${OUTPUT_PROFILE:-evaluation}
output_interval=${OUTPUT_INTERVAL:-600}
radiation_update_interval=${HICAR_RADIATION_UPDATE_INTERVAL:-600.0}
radiation_scheme=${HICAR_RADIATION_SCHEME:-rrtmgp}
model_debug=${HICAR_MODEL_DEBUG:-0}
disable_sx=${HICAR_DISABLE_SX:-0}
alpha_const=${HICAR_ALPHA_CONST:-1.0}
allow_input_superset=${HICAR_ALLOW_INPUT_SUPERSET:-0}
acc_synchronous=${HICAR_ACC_SYNCHRONOUS:-0}
cfl_reduction=${HICAR_CFL_REDUCTION_FACTOR:-1.6}
renderer="$repo_root/case_studies/swiss_200m/scripts/render_hicar_namelist.py"
wrapper=${HICAR_RANK_WRAPPER:-"$repo_root/case_studies/swiss_200m/scripts/gpu_rank_wrapper.sh"}
python=${HICAR_PYTHON:-python3}

for path in "$exe" "$build_provenance" "$build_provenance.ready" "$static" \
  "$forcing_list" "$renderer" "$wrapper"; do
  test -e "$path" || { echo "missing input: $path" >&2; exit 2; }
done
if test -n "$lbc_list"; then
  test -e "$lbc_list" || { echo "missing input: $lbc_list" >&2; exit 2; }
fi

mkdir -p "$run/output" "$run/restart"
base=$(basename "${static%.nc}")
end_file=${end/T/_}; end_file=${end_file//:/-}
restart="$run/restart/${base}_${end_file}.nc"
complete="$run/segment.complete"
test ! -e "$complete" || exit 0

restart_args=()
validation_restart_args=()
if test -n "$restart_input"; then
  test -s "$restart_input" || { echo "missing restart input: $restart_input" >&2; exit 2; }
  start_file=${start/T/_}; start_file=${start_file//:/-}
  staged="$run/restart/${base}_${start_file}.nc"
  test -e "$staged" || ln -s "$(realpath "$restart_input")" "$staged"
  restart_args=(--restart-from "${start/T/ }")
  validation_restart_args=(--continued)
fi

read -r checkpoint_seconds integration_seconds < <("$python" - "$start" "$end" "$model_end" <<'PY'
from datetime import datetime
import sys
start, checkpoint, model_end = (datetime.fromisoformat(value) for value in sys.argv[1:])
print(int((checkpoint - start).total_seconds()), int((model_end - start).total_seconds()))
PY
)
test "$checkpoint_seconds" -gt 0 || { echo "invalid checkpoint duration" >&2; exit 2; }
test "$integration_seconds" -ge "$checkpoint_seconds" || { echo "invalid model end" >&2; exit 2; }
test $((checkpoint_seconds % output_interval)) -eq 0 || {
  echo "checkpoint duration must be divisible by output interval" >&2; exit 2;
}
test $((integration_seconds % output_interval)) -eq 0 || {
  echo "segment duration must be divisible by output interval" >&2; exit 2;
}
restart_records=$((checkpoint_seconds / output_interval))
debug_args=()
if [[ "$model_debug" == 1 ]]; then
  debug_args=(--model-debug)
fi
sx_args=()
if [[ "$disable_sx" == 1 ]]; then
  sx_args=(--disable-sx)
fi
input_validation_args=()
if [[ "$allow_input_superset" == 1 ]]; then
  input_validation_args=(--allow-input-superset)
fi
sparse_lbc_args=()
boundary_validation_args=()
if test -n "$lbc_list"; then
  sparse_lbc_args=(--sparse-lbc-file-list "$lbc_list")
  boundary_validation_args=(--boundary-list "$lbc_list")
fi

"$python" "$renderer" \
  --static-file "$static" \
  --forcing-file-list "$forcing_list" \
  "${sparse_lbc_args[@]}" \
  --start-date "${start/T/ }" \
  --end-date "${model_end/T/ }" \
  --output-profile "$profile" \
  --cfl-reduction-factor "$cfl_reduction" \
  --alpha-const "$alpha_const" \
  "${debug_args[@]}" \
  "${sx_args[@]}" \
  --output-interval "$output_interval" \
  --radiation-scheme "$radiation_scheme" \
  --radiation-update-interval "$radiation_update_interval" \
  --restart-interval "$restart_records" \
  "${restart_args[@]}" \
  --output-dir "$run/output" \
  --restart-dir "$run/restart" \
  --output "$run/input.nml"

for asset in NoahmpTable.TBL rrtmg_support rrtmgp_support mp_support; do
  test -e "$support/$asset" || { echo "missing HICAR support asset: $asset" >&2; exit 2; }
  test -e "$run/$asset" || ln -s "$support/$asset" "$run/$asset"
done

export OMP_NUM_THREADS=1
export MPICH_GPU_SUPPORT_ENABLED=0
export MPICH_GPU_MANAGED_MEMORY_SUPPORT_ENABLED=0
export HICAR_WIND_ADJOINT_PROJECTION=1
export HICAR_WIND_RECYCLE_DIM=0
if [[ "$acc_synchronous" == 1 ]]; then
  export NVCOMPILER_ACC_SYNCHRONOUS=1
else
  unset NVCOMPILER_ACC_SYNCHRONOUS
fi

child=
terminate() {
  test -z "$child" || kill -TERM "$child" 2>/dev/null || true
  wait "$child" 2>/dev/null || true
  exit 143
}
trap terminate TERM INT

cd "$run"
srun --distribution=block:block --cpu-bind=none "$wrapper" "$exe" "$run/input.nml" \
  >"$run/model.out" 2>&1 &
child=$!
wait "$child"
child=

grep -q 'Simulation completed successfully!' "$run/model.out"
grep -q 'HICAR discretely adjoint wind projection enabled' "$run/model.out"
test -s "$restart" || { echo "missing terminal restart: $restart" >&2; exit 1; }
"$python" "$repo_root/case_studies/swiss_200m/validation/validate_segment.py" \
  --output-dir "$run/output" --restart "$restart" --start "$start" --end "$model_end" \
  --restart-time "$end" \
  --output-interval "$output_interval" --forcing-list "$forcing_list" \
  --radiation-scheme "$radiation_scheme" \
  --radiation-update-interval "$radiation_update_interval" \
  --alpha-const "$alpha_const" \
  "${boundary_validation_args[@]}" "${validation_restart_args[@]}" \
  "${input_validation_args[@]}" \
  >"$run/segment_validation.json"

"$python" - "$run/segment.json" "$start" "$end" "$model_end" "$static" \
  "$exe" "$restart" "$build_provenance" <<'PY'
import json, pathlib, re, sys
report, start, end, model_end, static, exe, restart, provenance = sys.argv[1:]
build = dict(
    line.split("=", 1)
    for line in pathlib.Path(provenance).read_text().splitlines()
    if "=" in line and not line.startswith("---")
)
commit = build.get("source_commit", "")
if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
    raise ValueError(f"invalid source_commit in HICAR build provenance: {commit!r}")
validation = json.loads((pathlib.Path(report).parent / "segment_validation.json").read_text())
validation.update({
    "start": start, "end": end, "model_end": model_end, "hicar_commit": commit,
    "static": static, "executable": exe, "restart": restart,
    "hicar_build_provenance": provenance,
})
pathlib.Path(report).write_text(json.dumps(validation, indent=2, sort_keys=True) + "\n")
PY
touch "$complete"
