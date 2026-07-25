#!/bin/bash -l
# Run one event-authorized national 100 m capacity segment with memory sampling.
#SBATCH --job-name=hicar-100m-capacity
#SBATCH --partition=normal
#SBATCH --nodes=16
#SBATCH --ntasks-per-node=5
#SBATCH --cpus-per-task=1
#SBATCH --gres=gpu:4
#SBATCH --time=02:00:00
#SBATCH --exclusive

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module purge || true
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module load nvhpc/24.5 cray-mpich-nvhpc/8.1.30 cuda/12.3.0-gcc \
  netcdf-c/4.9.2-nvhpc netcdf-fortran/4.6.1-nvhpc \
  hdf5/1.14.3-nvhpc fftw/3.3.10-gcc

gate_plan=${CAPACITY_GATE_PLAN:?Set CAPACITY_GATE_PLAN to capacity_gate_plan.json}
segment_id=${CAPACITY_SEGMENT_ID:?Set CAPACITY_SEGMENT_ID}
repo_root=${REPO_ROOT:-$SCRATCH/icon_hicar}
case_root=${HICAR_SWISS_CASE:-$repo_root/case_studies/swiss_100m}
hicar_root=${HICAR_MULTILEVEL_ROOT:-$repo_root/HICAR-wind-galerkin-multilevel}
expected_hicar_commit=${HICAR_EXPECTED_COMMIT:?Set HICAR_EXPECTED_COMMIT}
build=${HICAR_MULTILEVEL_BUILD:-$hicar_root/build_gpu_nccl}
exe="$build/HICAR_gpu"
renderer="$case_root/scripts/render_hicar_namelist.py"
validator=${HICAR_STREAM_VALIDATOR:-$repo_root/case_studies/swiss_200m/streaming/validate_model_chunk.py}
python=${HICAR_VALIDATION_PYTHON:-$repo_root/venv_static/bin/python}
wrapper="$case_root/scripts/gpu_rank_wrapper.sh"

for path in "$gate_plan" "$gate_plan.ready" "$exe" "$renderer" "$validator" \
            "$python" "$wrapper"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done
test "${SLURM_JOB_NUM_NODES:?}" -eq 16 || {
  echo "the frozen capacity gate requires exactly 16 nodes" >&2
  exit 2
}

IFS=$'\t' read -r chunk_plan run memory_dir timing_report shared_restart \
  expected_restart start end restart_continuation static_file < <(
  "$python" - "$gate_plan" "$segment_id" <<'PY'
import json
import sys
plan = json.load(open(sys.argv[1]))
matches = [item for item in plan["segments"] if item["id"] == sys.argv[2]]
if len(matches) != 1:
    raise SystemExit("capacity segment id is absent or duplicated")
segment = matches[0]
print(
    segment["chunk_plan"],
    segment["run_dir"],
    segment["memory_dir"],
    segment["timing_report"],
    segment["shared_restart_dir"],
    segment["expected_restart_file"],
    segment["start"],
    segment["end"],
    "1" if segment["restart_continuation"] else "0",
    plan["static_file"],
    sep="\t",
)
PY
)
completion="$run/model_chunk_completion.json"
model_log="$run/model.out"
forcing_list=$(
  "$python" - "$chunk_plan" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1]))["forcing_list"])
PY
)
chunk_root=$(dirname "$chunk_plan")
forcing_publication="$chunk_root/forcing_publication.json"

for path in "$chunk_plan" "$chunk_plan.ready" "$forcing_list" \
            "$forcing_list.ready" "$forcing_publication" \
            "$forcing_publication.ready" "$static_file" "$static_file.ready"; do
  test -e "$path" || { echo "missing published input: $path" >&2; exit 2; }
done
"$python" - "$forcing_publication" <<'PY'
import json
import sys
if json.load(open(sys.argv[1])).get("status") != "PASS":
    raise SystemExit("forcing publication is not PASS")
PY

if test -f "$completion.ready"; then
  for path in "$timing_report" "$timing_report.ready"; do
    test -e "$path" || { echo "published completion lacks timing: $path" >&2; exit 2; }
  done
  echo "capacity segment already published: $completion"
  exit 0
fi
mkdir -p "$run/output" "$shared_restart" "$memory_dir"
test -z "$(find "$memory_dir" -mindepth 1 -maxdepth 1 -print -quit)" || {
  echo "unpublished memory samples already exist: $memory_dir" >&2
  exit 2
}
shopt -s nullglob
existing_output=("$run/output"/*.nc)
shopt -u nullglob
test "${#existing_output[@]}" -eq 0 || {
  echo "unpublished model output already exists: ${existing_output[0]}" >&2
  exit 2
}
test ! -e "$expected_restart" || {
  echo "unpublished target restart already exists: $expected_restart" >&2
  exit 2
}

{
  git -C "$hicar_root" status --porcelain --untracked-files=no
  git -C "$hicar_root" ls-files --others --exclude-standard -- \
    src cmake external tools CMakeLists.txt CMakePresets.json
} > "$run/source_tree_status.txt"
test ! -s "$run/source_tree_status.txt" || {
  echo "HICAR tracked tree or untracked build inputs are not clean" >&2
  cat "$run/source_tree_status.txt" >&2
  exit 2
}
source_commit=$(git -C "$hicar_root" rev-parse HEAD)
test "$source_commit" = "$expected_hicar_commit" || {
  echo "HICAR source commit $source_commit does not match the frozen capacity-gate commit $expected_hicar_commit" >&2
  exit 2
}
printf '%s\n' "$source_commit" > "$run/source_commit.txt"
sha256sum "$exe" > "$run/executable.sha256"
cp "$gate_plan" "$run/capacity_gate_plan.json"
cp "$chunk_plan" "$run/chunk_plan.json"
cp "$forcing_publication" "$run/forcing_publication.json"

start_hicar=${start/T/ }
end_hicar=${end/T/ }
restart_args=()
validator_args=()
if test "$restart_continuation" = 1; then
  start_file=${start/T/_}
  start_file=${start_file//:/-}
  previous_restart="$shared_restart/$(basename "${static_file%.nc}")_${start_file}.nc"
  test -s "$previous_restart" || {
    echo "continuation restart is missing: $previous_restart" >&2
    exit 2
  }
  restart_args=(--restart-from "$start_hicar")
  validator_args=(--restart-continuation)
fi

render_start=$(date +%s.%N)
"$python" "$renderer" \
  --static-file "$static_file" \
  --forcing-file-list "$forcing_list" \
  --forcing-plan "$chunk_plan" \
  --start-date "$start_hicar" \
  --end-date "$end_hicar" \
  --output-interval 3600 \
  --output-profile routine \
  --nz 80 \
  --wind-solver-iterations 2500 \
  --restart-interval 2 \
  "${restart_args[@]}" \
  --output-dir "$run/output" \
  --restart-dir "$shared_restart" \
  --output "$run/input.nml"
render_end=$(date +%s.%N)

for asset in NoahmpTable.TBL rrtmgp_support mp_support; do
  if ! test -e "$run/$asset"; then
    ln -s "$hicar_root/run/$asset" "$run/$asset"
  fi
done

export OMP_NUM_THREADS=1
export MPICH_GPU_SUPPORT_ENABLED=0
export MPICH_GPU_MANAGED_MEMORY_SUPPORT_ENABLED=0
export HICAR_WIND_ADJOINT_PROJECTION=1
export HICAR_WIND_RECYCLE_DIM=0
export HICAR_MEMORY_DIR="$memory_dir"
unset HICAR_WIND_MULTILEVEL
unset HICAR_WIND_OPERATOR_AUDIT
unset HICAR_WIND_OPERATOR_AUDIT_MAX_ITERS

restart_first_file="$run/restart_first_observed_epoch.txt"
watcher=
cleanup_watcher() {
  if test -n "${watcher:-}" && kill -0 "$watcher" 2>/dev/null; then
    kill "$watcher" 2>/dev/null || true
    wait "$watcher" 2>/dev/null || true
  fi
}
trap cleanup_watcher EXIT
(
  while ! test -e "$expected_restart"; do
    sleep 1
  done
  date +%s.%N > "$restart_first_file"
) &
watcher=$!

model_start=$(date +%s.%N)
cd "$run"
srun --distribution=block:block --cpu-bind=none "$wrapper" "$exe" "$run/input.nml" \
  2>&1 | tee "$model_log"
model_end=$(date +%s.%N)
wait "$watcher"
watcher=

grep -q 'HICAR discretely adjoint wind projection enabled' "$model_log"
grep -q 'HICAR SLEVE geometry gate:' "$model_log"
grep -q 'Simulation completed successfully!' "$model_log"
grep -q 'Timing across all compute images:' "$model_log"
if test "$restart_continuation" = 1; then
  grep -q 'Reading restart data' "$model_log"
fi

shopt -s nullglob
output_files=("$run/output"/*.nc)
shopt -u nullglob
test "${#output_files[@]}" -gt 0 || {
  echo "model produced no output file" >&2
  exit 1
}
test -s "$expected_restart"
validation_start=$(date +%s.%N)
"$python" "$validator" \
  --plan "$chunk_plan" \
  --run-dir "$run" \
  --static-file "$static_file" \
  --output-file "${output_files[@]}" \
  --restart-file "$expected_restart" \
  --model-log "$model_log" \
  --source-commit-file "$run/source_commit.txt" \
  --source-tree-status-file "$run/source_tree_status.txt" \
  --executable "$exe" \
  --executable-digest-file "$run/executable.sha256" \
  --forcing-publication "$forcing_publication" \
  --archived-plan "$run/chunk_plan.json" \
  --archived-forcing-publication "$run/forcing_publication.json" \
  --output-profile routine \
  --output-interval-seconds 3600 \
  "${validator_args[@]}" \
  --report "$completion"
validation_end=$(date +%s.%N)

"$python" - "$timing_report" "$render_start" "$render_end" "$model_start" \
  "$model_end" "$validation_start" "$validation_end" "$restart_first_file" \
  "$expected_restart" "$completion" <<'PY'
import json
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile

(
    report,
    render_start,
    render_end,
    model_start,
    model_end,
    validation_start,
    validation_end,
    restart_first_file,
    restart_file,
    completion_file,
) = sys.argv[1:]
restart_first = float(Path(restart_first_file).read_text().strip())
completion = json.load(open(completion_file))
payload = {
    "schema_version": 1,
    "status": "PASS",
    "render_wall_seconds": float(render_end) - float(render_start),
    "model_wall_seconds": float(model_end) - float(model_start),
    "validation_wall_seconds": float(validation_end) - float(validation_start),
    "restart_first_observed_epoch": restart_first,
    "restart_write_wall_upper_bound_seconds": float(model_end) - restart_first,
    "restart_size_bytes": Path(restart_file).stat().st_size,
    "output_size_bytes": completion["output"]["size_bytes"],
}
path = Path(report)
path.parent.mkdir(parents=True, exist_ok=True)
with NamedTemporaryFile(
    mode="w", encoding="utf-8", dir=path.parent, delete=False
) as stream:
    json.dump(payload, stream, indent=2, sort_keys=True)
    stream.write("\n")
    temporary = Path(stream.name)
os.replace(temporary, path)
Path(f"{path}.ready").touch()
PY
echo "PASS: national 100 m capacity segment published at $completion"
