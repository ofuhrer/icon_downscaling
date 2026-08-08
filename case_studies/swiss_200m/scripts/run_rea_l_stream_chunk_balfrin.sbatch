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
static=${HICAR_STATIC_FILE:?}
forcing_list=${FORCING_FILE_LIST:?}
lbc_list=${SPARSE_LBC_FILE_LIST:?}
start=${SEGMENT_START:?}
end=${SEGMENT_END:?}
run=${SEGMENT_RUN_DIR:?}
restart_input=${RESTART_INPUT:-}
profile=${OUTPUT_PROFILE:-evaluation}
output_interval=${OUTPUT_INTERVAL:-3600}
model_debug=${HICAR_MODEL_DEBUG:-0}
cfl_reduction=${HICAR_CFL_REDUCTION_FACTOR:-1.6}
renderer="$repo_root/case_studies/swiss_200m/scripts/render_hicar_namelist.py"
wrapper="$repo_root/case_studies/swiss_200m/scripts/gpu_rank_wrapper.sh"
python=${HICAR_PYTHON:-python3}

for path in "$exe" "$static" "$forcing_list" "$lbc_list" "$renderer" "$wrapper"; do
  test -e "$path" || { echo "missing input: $path" >&2; exit 2; }
done

mkdir -p "$run/output" "$run/restart"
base=$(basename "${static%.nc}")
end_file=${end/T/_}; end_file=${end_file//:/-}
restart="$run/restart/${base}_${end_file}.nc"
complete="$run/segment.complete"
test ! -e "$complete" || exit 0

restart_args=()
if test -n "$restart_input"; then
  test -s "$restart_input" || { echo "missing restart input: $restart_input" >&2; exit 2; }
  start_file=${start/T/_}; start_file=${start_file//:/-}
  staged="$run/restart/${base}_${start_file}.nc"
  test -e "$staged" || ln -s "$(realpath "$restart_input")" "$staged"
  restart_args=(--restart-from "${start/T/ }")
fi

duration_seconds=$("$python" - "$start" "$end" <<'PY'
from datetime import datetime
import sys
start, end = (datetime.fromisoformat(value) for value in sys.argv[1:])
print(int((end - start).total_seconds()))
PY
)
test "$duration_seconds" -gt 0 || { echo "invalid segment duration" >&2; exit 2; }
test $((duration_seconds % output_interval)) -eq 0 || {
  echo "segment duration must be divisible by output interval" >&2; exit 2;
}
restart_records=$((duration_seconds / output_interval))
debug_args=()
if [[ "$model_debug" == 1 ]]; then
  debug_args=(--model-debug)
fi

"$python" "$renderer" \
  --static-file "$static" \
  --forcing-file-list "$forcing_list" \
  --sparse-lbc-file-list "$lbc_list" \
  --start-date "${start/T/ }" \
  --end-date "${end/T/ }" \
  --output-profile "$profile" \
  --cfl-reduction-factor "$cfl_reduction" \
  "${debug_args[@]}" \
  --output-interval "$output_interval" \
  --restart-interval "$restart_records" \
  "${restart_args[@]}" \
  --output-dir "$run/output" \
  --restart-dir "$run/restart" \
  --output "$run/input.nml"

for asset in NoahmpTable.TBL rrtmgp_support mp_support; do
  test -e "$support/$asset" || { echo "missing HICAR support asset: $asset" >&2; exit 2; }
  test -e "$run/$asset" || ln -s "$support/$asset" "$run/$asset"
done

export OMP_NUM_THREADS=1
export MPICH_GPU_SUPPORT_ENABLED=0
export MPICH_GPU_MANAGED_MEMORY_SUPPORT_ENABLED=0
export HICAR_WIND_ADJOINT_PROJECTION=1
export HICAR_WIND_RECYCLE_DIM=0

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
  --output-dir "$run/output" --restart "$restart" --start "$start" --end "$end" \
  --output-interval "$output_interval" --forcing-list "$forcing_list" \
  --boundary-list "$lbc_list" >"$run/segment_validation.json"

"$python" - "$run/segment.json" "$start" "$end" "$static" "$exe" "$restart" <<'PY'
import json, pathlib, subprocess, sys
report, start, end, static, exe, restart = sys.argv[1:]
root = pathlib.Path(exe).resolve()
commit = "unknown"
for parent in (root, *root.parents):
    if (parent / ".git").exists():
        commit = subprocess.run(
            ["git", "-C", str(parent), "rev-parse", "HEAD"],
            check=True, text=True, stdout=subprocess.PIPE,
        ).stdout.strip()
        break
validation = json.loads((pathlib.Path(report).parent / "segment_validation.json").read_text())
validation.update({
    "start": start, "end": end, "hicar_commit": commit,
    "static": static, "executable": exe, "restart": restart,
})
pathlib.Path(report).write_text(json.dumps(validation, indent=2, sort_keys=True) + "\n")
PY
touch "$complete"
