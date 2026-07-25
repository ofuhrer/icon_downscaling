#!/bin/bash -l
# Run one published REA-L stream chunk and publish a validated restart boundary.
# Chunks are sequential within a chain and may be submitted in parallel across chains.
#SBATCH --job-name=hicar-stream
#SBATCH --partition=normal
#SBATCH --nodes=4
#SBATCH --ntasks-per-node=5
#SBATCH --cpus-per-task=1
#SBATCH --gres=gpu:4
#SBATCH --time=23:30:00
#SBATCH --exclusive

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module purge || true
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module load nvhpc/24.5 cray-mpich-nvhpc/8.1.30 cuda/12.3.0-gcc \
  netcdf-c/4.9.2-nvhpc netcdf-fortran/4.6.1-nvhpc \
  hdf5/1.14.3-nvhpc fftw/3.3.10-gcc

plan=${STREAM_PLAN:?Set STREAM_PLAN to a published chunk_plan.json}
root=${HICAR_MULTILEVEL_ROOT:-$SCRATCH/icon_hicar/HICAR-wind-galerkin-multilevel}
case_root=${HICAR_SWISS_CASE:-$SCRATCH/icon_hicar/case_studies/swiss_200m}
case_name=$(basename "$case_root")
build=${HICAR_MULTILEVEL_BUILD:-$root/build_gpu_nccl}
exe="$build/HICAR_gpu"
static_file=${HICAR_STATIC_FILE:-$case_root/static/domain_static_${case_name}.nc}
static_basename=$(basename "${static_file%.nc}")
renderer="$case_root/scripts/render_hicar_namelist.py"
validator=${HICAR_STREAM_VALIDATOR:-$SCRATCH/icon_hicar/case_studies/swiss_200m/streaming/validate_model_chunk.py}
wind_reducer=${HICAR_WIND_REDUCER:-$SCRATCH/icon_hicar/scripts/reduce_hicar_wind_climatology.py}
python=${HICAR_VALIDATION_PYTHON:-$SCRATCH/icon_hicar/venv_static/bin/python}
wrapper=${HICAR_GPU_WRAPPER:-$case_root/scripts/gpu_rank_wrapper.sh}

IFS=$'\t' read -r chunk_id chunk_root start end hours forcing_list < <(
  "$python" - "$plan" <<'PY'
import json
import sys
p = json.load(open(sys.argv[1]))
print(p["chunk_id"], p["chunk_root"], p["start"], p["end"], p["hours"], p["forcing_list"], sep="\t")
PY
)
start_hicar=${start/T/ }
end_hicar=${end/T/ }
start_file=${start/T/_}
start_file=${start_file//:/-}
end_file=${end/T/_}
end_file=${end_file//:/-}
run=${STREAM_RUN_DIR:-$case_root/runs/stream_${chunk_id}_${SLURM_JOB_ID:?}}
restart_dir=${STREAM_RESTART_DIR:-$run/restart}
forcing_publication="$chunk_root/forcing_publication.json"
restart_file="$restart_dir/${static_basename}_${end_file}.nc"
report="$run/model_chunk_completion.json"
output_profile=${STREAM_OUTPUT_PROFILE:-routine}
if test "$output_profile" = wind_climatology && test -z "${STREAM_OUTPUT_INTERVAL:-}"; then
  echo "wind_climatology requires an explicit STREAM_OUTPUT_INTERVAL sampling cadence" >&2
  exit 2
fi
output_interval=${STREAM_OUTPUT_INTERVAL:-3600}
wind_reduction_interval=${STREAM_WIND_REDUCTION_INTERVAL:-86400}
wind_reduction_dir="$run/wind_climatology"
wind_reduction_file="$wind_reduction_dir/wind_statistics_${chunk_id}.nc"
wind_reduction_report="$wind_reduction_dir/wind_statistics_${chunk_id}.json"

case "$output_profile" in
  routine|qualification|wind_climatology) ;;
  *) echo "unsupported STREAM_OUTPUT_PROFILE: $output_profile" >&2; exit 2 ;;
esac
for value_name in output_interval wind_reduction_interval; do
  value=${!value_name}
  case "$value" in
    ''|*[!0-9]*) echo "$value_name must be a positive integer" >&2; exit 2 ;;
  esac
  test "$value" -gt 0 || {
    echo "$value_name must be positive" >&2
    exit 2
  }
done
duration_seconds=$((hours * 3600))
test $((duration_seconds % output_interval)) -eq 0 || {
  echo "chunk duration must be divisible by STREAM_OUTPUT_INTERVAL" >&2
  exit 2
}
total_output_records=$((duration_seconds / output_interval))
restart_records=${STREAM_RESTART_INTERVAL_RECORDS:-$total_output_records}
case "$restart_records" in
  ''|*[!0-9]*) echo "STREAM_RESTART_INTERVAL_RECORDS must be a positive integer" >&2; exit 2 ;;
esac
test "$restart_records" -gt 0 || {
  echo "STREAM_RESTART_INTERVAL_RECORDS must be positive" >&2
  exit 2
}
test "$restart_records" -le "$total_output_records" || {
  echo "STREAM_RESTART_INTERVAL_RECORDS exceeds the chunk output-record count" >&2
  exit 2
}
test $((total_output_records % restart_records)) -eq 0 || {
  echo "chunk output-record count must be divisible by STREAM_RESTART_INTERVAL_RECORDS" >&2
  exit 2
}
if test "$output_profile" = wind_climatology; then
  test -e "$wind_reducer" || {
    echo "missing required wind reducer: $wind_reducer" >&2
    exit 2
  }
  test $((wind_reduction_interval % output_interval)) -eq 0 || {
    echo "STREAM_WIND_REDUCTION_INTERVAL must be a multiple of STREAM_OUTPUT_INTERVAL" >&2
    exit 2
  }
  test $((duration_seconds % wind_reduction_interval)) -eq 0 || {
    echo "chunk duration must be divisible by STREAM_WIND_REDUCTION_INTERVAL" >&2
    exit 2
  }
fi

publish_wind_reduction() {
  test "$output_profile" = wind_climatology || return 0
  mkdir -p "$wind_reduction_dir"
  if test -f "$wind_reduction_file.ready"; then
    "$python" - "$wind_reduction_report" "$wind_reduction_file" <<'PY'
import hashlib
import json
import pathlib
import sys
report = json.load(open(sys.argv[1]))
target = pathlib.Path(sys.argv[2])
if report.get("status") != "PASS":
    raise SystemExit("published wind reduction is not PASS")
hasher = hashlib.sha256()
with target.open("rb") as stream:
    for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
        hasher.update(block)
digest = hasher.hexdigest()
if report.get("output_sha256") != digest:
    raise SystemExit("published wind reduction checksum changed")
PY
    echo "wind reduction already published: $wind_reduction_report"
    return 0
  fi
  if test -e "$wind_reduction_file" || test -e "$wind_reduction_report"; then
    echo "unpublished wind reduction exists; inspect $wind_reduction_dir" >&2
    return 2
  fi
  shopt -s nullglob
  local source_files=("$run/output"/"${static_basename}"_*.nc)
  shopt -u nullglob
  test "${#source_files[@]}" -gt 0 || {
    echo "no wind source output found under $run/output" >&2
    return 1
  }
  "$python" "$wind_reducer" \
    --input "${source_files[@]}" \
    --output "$wind_reduction_file" \
    --interval-seconds "$wind_reduction_interval" \
    --start-time "$start" \
    --report "$wind_reduction_report"
  test -f "$wind_reduction_file.ready"
  echo "wind reduction published: $wind_reduction_report"
}

for path in "$exe" "$static_file" "$static_file.ready" "$plan" "$plan.ready" \
            "$forcing_list" "$forcing_list.ready" "$forcing_publication" \
            "$forcing_publication.ready" "$renderer" "$validator" "$python" "$wrapper"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done
validator_help=$("$python" "$validator" --help)
required_validator_options=(
  --plan
  --run-dir
  --static-file
  --output-file
  --restart-file
  --model-log
  --source-commit-file
  --source-tree-status-file
  --executable
  --executable-digest-file
  --forcing-publication
  --archived-plan
  --archived-forcing-publication
  --output-profile
  --output-interval-seconds
  --restart-continuation
  --report
)
for option in "${required_validator_options[@]}"; do
  grep -Fq -- "$option" <<<"$validator_help" || {
    echo "model validator does not support runner option $option: $validator" >&2
    exit 2
  }
done
"$python" - "$forcing_publication" <<'PY'
import json
import sys
if json.load(open(sys.argv[1])).get("status") != "PASS":
    raise SystemExit("forcing publication is not PASS")
PY
if test -f "$report.ready"; then
  "$python" - "$report" "$output_profile" "$output_interval" <<'PY'
import json
import sys
report = json.load(open(sys.argv[1]))
if report.get("status") != "PASS":
    raise SystemExit("published model completion is not PASS")
if report.get("output_profile") != sys.argv[2]:
    raise SystemExit("published model completion has a different output profile")
if report.get("output_interval_seconds") != int(sys.argv[3]):
    raise SystemExit("published model completion has a different output interval")
PY
  publish_wind_reduction
  echo "model chunk already published: $report"
  exit 0
fi
mkdir -p "$run/output" "$restart_dir"
shopt -s nullglob
existing_output=("$run/output"/"${static_basename}"_*.nc)
shopt -u nullglob
if ((${#existing_output[@]})); then
  echo "unpublished model output already exists; use a new STREAM_RUN_DIR or inspect it: ${existing_output[0]}" >&2
  exit 2
fi
if test -e "$restart_file"; then
  echo "unpublished target restart already exists; inspect it before retrying: $restart_file" >&2
  exit 2
fi
{
  git -C "$root" status --porcelain --untracked-files=no
  git -C "$root" ls-files --others --exclude-standard -- \
    src cmake external tools CMakeLists.txt CMakePresets.json
} > "$run/source_tree_status.txt"
test ! -s "$run/source_tree_status.txt" || {
  echo "HICAR tracked tree or untracked build inputs are not clean" >&2
  cat "$run/source_tree_status.txt" >&2
  exit 2
}
source_commit=$(git -C "$root" rev-parse HEAD)
if test -n "${HICAR_EXPECTED_COMMIT:-}" &&
   test "$source_commit" != "$HICAR_EXPECTED_COMMIT"; then
  echo "HICAR source commit $source_commit does not match HICAR_EXPECTED_COMMIT=$HICAR_EXPECTED_COMMIT" >&2
  exit 2
fi
printf '%s\n' "$source_commit" > "$run/source_commit.txt"
sha256sum "$exe" > "$run/executable.sha256"
cp "$plan" "$run/chunk_plan.json"
cp "$forcing_publication" "$run/forcing_publication.json"

restart_args=()
validator_restart_args=()
land_initialization_args=()
case "${STREAM_REA_L_LAND_INITIALIZATION:-0}" in
  1|true|TRUE) land_initialization_args=(--rea-l-land-initialization) ;;
  0|false|FALSE) ;;
  *)
    echo "STREAM_REA_L_LAND_INITIALIZATION must be true/false or 1/0" >&2
    exit 2
    ;;
esac
if test -n "${STREAM_RESTART_FROM:-}"; then
  test "$STREAM_RESTART_FROM" = "$start" || {
    echo "STREAM_RESTART_FROM must equal plan start: $start" >&2
    exit 2
  }
  previous_file="$restart_dir/${static_basename}_${start_file}.nc"
  test -s "$previous_file" || { echo "missing previous restart: $previous_file" >&2; exit 2; }
  restart_args=(--restart-from "$start_hicar")
  validator_restart_args=(--restart-continuation)
fi

"$python" "$renderer" \
  --static-file "$static_file" \
  --forcing-file-list "$forcing_list" \
  --forcing-plan "$plan" \
  --start-date "$start_hicar" \
  --end-date "$end_hicar" \
  --output-interval "$output_interval" \
  --output-profile "$output_profile" \
  "${land_initialization_args[@]}" \
  --nz 80 \
  --wind-solver-iterations 2500 \
  --restart-interval "$restart_records" \
  "${restart_args[@]}" \
  --output-dir "$run/output" \
  --restart-dir "$restart_dir" \
  --output "$run/input.nml"

grep -Eq '^[[:space:]]*restartinterval[[:space:]]*=[[:space:]]*'"$restart_records" "$run/input.nml"
grep -Eq '^[[:space:]]*outputinterval[[:space:]]*=[[:space:]]*'"$output_interval" "$run/input.nml"
grep -q "'u10m'" "$run/input.nml"
grep -q "'v10m'" "$run/input.nml"
for asset in NoahmpTable.TBL rrtmgp_support mp_support; do
  if ! test -e "$run/$asset"; then
    ln -s "$root/run/$asset" "$run/$asset"
  fi
done

export OMP_NUM_THREADS=1
export MPICH_GPU_SUPPORT_ENABLED=0
export MPICH_GPU_MANAGED_MEMORY_SUPPORT_ENABLED=0
export HICAR_WIND_ADJOINT_PROJECTION=1
export HICAR_WIND_RECYCLE_DIM=0
unset HICAR_WIND_MULTILEVEL
unset HICAR_WIND_OPERATOR_AUDIT
unset HICAR_WIND_OPERATOR_AUDIT_MAX_ITERS

cd "$run"
srun --distribution=block:block --cpu-bind=none "$wrapper" "$exe" "$run/input.nml" \
  2>&1 | tee "$run/model.out"

grep -q 'HICAR discretely adjoint wind projection enabled' "$run/model.out"
grep -q 'HICAR SLEVE geometry gate:' "$run/model.out"
grep -q 'Simulation completed successfully!' "$run/model.out"
grep -q 'Timing across all compute images:' "$run/model.out"
output_files=("$run/output"/"${static_basename}"_*.nc)
test -s "${output_files[0]}" || { echo "no model output files found" >&2; exit 1; }
"$python" "$validator" \
  --plan "$plan" \
  --run-dir "$run" \
  --static-file "$static_file" \
  --output-file "${output_files[@]}" \
  --restart-file "$restart_file" \
  --model-log "$run/model.out" \
  --source-commit-file "$run/source_commit.txt" \
  --source-tree-status-file "$run/source_tree_status.txt" \
  --executable "$exe" \
  --executable-digest-file "$run/executable.sha256" \
  --forcing-publication "$forcing_publication" \
  --archived-plan "$run/chunk_plan.json" \
  --archived-forcing-publication "$run/forcing_publication.json" \
  --output-profile "$output_profile" \
  --output-interval-seconds "$output_interval" \
  "${validator_restart_args[@]}" \
  --report "$report"
publish_wind_reduction
echo "stream model chunk published: $report"
