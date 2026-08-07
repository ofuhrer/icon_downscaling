#!/bin/bash
# Submit a bounded, dependency-linked repeated-day experiment on Balfrin.

set -euo pipefail

root=${HICAR_REPEAT_ROOT:-/scratch/mch/olifu/icon_hicar/qualification/repeated-day-summer-windfix-b514/v2}
wind_fix_root=${HICAR_WIND_FIX_ROOT:-/scratch/mch/olifu/icon_hicar/qualification/wind-tendency-fix-b514/v1}
forcing_root=${HICAR_REPEAT_FORCING_ROOT:-/scratch/mch/olifu/icon_hicar/qualification/repeated-day-summer-b514/v1/forcing}
repo=${HICAR_REPEAT_MODEL_RUNTIME:-/scratch/mch/olifu/icon_hicar/qualification/wind-spinup-morrison-b514/runtime-v3}
execution_runtime=${HICAR_REPEAT_EXECUTION_RUNTIME:?Set HICAR_REPEAT_EXECUTION_RUNTIME}
python=${HICAR_REPEAT_PYTHON:-/scratch/mch/olifu/icon_hicar/venv_static/bin/python}
cycle_count=${HICAR_REPEAT_CYCLE_COUNT:-7}
source_root=${wind_fix_root}/HICAR
build=${wind_fix_root}/build-gpu-nccl
commit=86d6f1dd771d404a0a4a42f2b8868c14c8b97601
case_root=/scratch/mch/olifu/icon_hicar/qualification/wind-spinup-morrison-b514/campaign-v1/input
static=${case_root}/static/domain_static_alpine_bridge_200m_rea_l_20200701_0000.nc
plan=${forcing_root}/chunk_plan.json
runner=${repo}/case_studies/swiss_200m/scripts/run_rea_l_stream_chunk_balfrin.sbatch
clock_job=${execution_runtime}/case_studies/swiss_200m/wind_climatology/relabel_repeated_day_restart_balfrin.sbatch
compat_job=${execution_runtime}/case_studies/swiss_200m/wind_climatology/publish_repeated_day_restart_compatibility_balfrin.sbatch
static_base=domain_static_alpine_bridge_200m_rea_l_20200701_0000

case "$cycle_count" in
  ''|*[!0-9]*) echo "HICAR_REPEAT_CYCLE_COUNT must be an integer" >&2; exit 2 ;;
esac
test "$cycle_count" -ge 2 || { echo "at least two cycles are required" >&2; exit 2; }

for partition in preemptible pp-short; do
  partition_line=$(scontrol show partition "$partition" -o)
  case "$partition_line" in
    *"AllowGroups=ALL"*|*"AllowGroups=s83"*) ;;
    *) echo "$partition is not authorized for s83" >&2; exit 2 ;;
  esac
done

qualification=${wind_fix_root}/wind_tendency_fix_qualification.json
for path in "$qualification" "$qualification.ready" "$plan" "$plan.ready" \
            "$runner" "$clock_job" "$compat_job" "$static" "$static.ready"; do
  test -e "$path" || { echo "missing required publication: $path" >&2; exit 2; }
done
"$python" - "$qualification" "$commit" <<'PY'
import json
import sys
payload = json.load(open(sys.argv[1]))
if payload.get("status") != "PASS":
    raise SystemExit("corrected-wind qualification is not PASS")
if payload.get("source_commit") != sys.argv[2]:
    raise SystemExit("corrected-wind qualification has the wrong source commit")
PY

test ! -e "$root/submission_plan.json" || {
  echo "submission plan already exists: $root/submission_plan.json" >&2
  exit 2
}
mkdir -p "$root/logs" "$root/cycles"

common="ALL,REPO_ROOT=${repo},HICAR_MULTILEVEL_ROOT=${source_root},HICAR_MULTILEVEL_BUILD=${build},HICAR_RUNTIME_SUPPORT_DIR=${source_root}/run,HICAR_SWISS_CASE=${case_root},HICAR_STATIC_FILE=${static},HICAR_VALIDATION_PYTHON=${python},HICAR_EXPECTED_COMMIT=${commit},HICAR_PREEMPTION_HELPER=${repo}/orchestration/preemption.py,STREAM_PLAN=${plan},STREAM_OUTPUT_PROFILE=wind_climatology,STREAM_OUTPUT_INTERVAL=1800,STREAM_WIND_REDUCTION_INTERVAL=86400,STREAM_RESTART_INTERVAL_RECORDS=48,STREAM_REA_L_LAND_INITIALIZATION=0,STREAM_PREEMPTIBLE_ATTEMPT=1"

declare -a cycle_jobs clock_jobs compat_jobs
cycle_one=${root}/cycles/cycle-001
cycle_jobs[1]=$(sbatch --parsable \
  --partition=preemptible --job-name=repeat-day-001 \
  --nodes=2 --ntasks-per-node=5 --cpus-per-task=1 --gres=gpu:4 \
  --time=01:00:00 --exclusive --signal=B:USR1@120 \
  --output="${root}/logs/cycle-001-%j.out" \
  --error="${root}/logs/cycle-001-%j.err" \
  --export="${common},STREAM_ATTEMPT_ID=cycle-001-a001,STREAM_RUN_DIR=${cycle_one}/run,STREAM_RESTART_DIR=${cycle_one}/restart" \
  "$runner")

for ((cycle=2; cycle<=cycle_count; cycle++)); do
  previous=$((cycle - 1))
  label=$(printf '%03d' "$cycle")
  previous_label=$(printf '%03d' "$previous")
  cycle_dir=${root}/cycles/cycle-${label}
  previous_dir=${root}/cycles/cycle-${previous_label}
  source_restart=${previous_dir}/restart/${static_base}_2020-07-02_01-00-00.nc
  source_report=${previous_dir}/run/model_chunk_completion.json
  target_restart=${cycle_dir}/input/restart_2020-07-01_01-00-00.nc
  transform_report=${cycle_dir}/input/restart_clock_transform.json
  compatibility_report=${cycle_dir}/input/restart_input_publication.json

  clock_jobs[$cycle]=$(sbatch --parsable \
    --dependency="afterok:${cycle_jobs[$previous]}" \
    --partition=pp-short --job-name="repeat-clock-${label}" \
    --nodes=1 --ntasks=1 --cpus-per-task=8 --time=01:00:00 \
    --output="${root}/logs/clock-${label}-%j.out" \
    --error="${root}/logs/clock-${label}-%j.err" \
    --export="ALL,HICAR_ANALYSIS_RUNTIME=${execution_runtime},HICAR_ANALYSIS_PYTHON=${python},HICAR_REPEAT_SOURCE_RESTART=${source_restart},HICAR_REPEAT_SOURCE_REPORT=${source_report},HICAR_REPEAT_TARGET_RESTART=${target_restart},HICAR_REPEAT_TARGET_TIME=2020-07-01T01:00:00,HICAR_REPEAT_SOURCE_COMMIT=${commit},HICAR_REPEAT_REPORT=${transform_report}" \
    "$clock_job")

  compat_jobs[$cycle]=$(sbatch --parsable \
    --dependency="afterok:${clock_jobs[$cycle]}" \
    --partition=pp-short --job-name="repeat-compat-${label}" \
    --nodes=1 --ntasks=1 --cpus-per-task=8 --mem=4G --time=01:00:00 \
    --output="${root}/logs/compat-${label}-%j.out" \
    --error="${root}/logs/compat-${label}-%j.err" \
    --export="ALL,HICAR_REPEAT_RUNTIME_DIR=${execution_runtime}/case_studies/swiss_200m/wind_climatology,HICAR_REPEAT_PYTHON=${python},HICAR_REPEAT_TRANSFORM_REPORT=${transform_report},HICAR_REPEAT_RESTART_INPUT_FILE=${target_restart},HICAR_REPEAT_COMPATIBILITY_REPORT=${compatibility_report}" \
    "$compat_job")

  cycle_jobs[$cycle]=$(sbatch --parsable \
    --dependency="afterok:${compat_jobs[$cycle]}" \
    --partition=preemptible --job-name="repeat-day-${label}" \
    --nodes=2 --ntasks-per-node=5 --cpus-per-task=1 --gres=gpu:4 \
    --time=01:00:00 --exclusive --signal=B:USR1@120 \
    --output="${root}/logs/cycle-${label}-%j.out" \
    --error="${root}/logs/cycle-${label}-%j.err" \
    --export="${common},STREAM_ATTEMPT_ID=cycle-${label}-a001,STREAM_RUN_DIR=${cycle_dir}/run,STREAM_RESTART_DIR=${cycle_dir}/restart,STREAM_RESTART_FROM=2020-07-01T01:00:00,STREAM_RESTART_INPUT_FILE=${target_restart},STREAM_RESTART_INPUT_REPORT=${compatibility_report}" \
    "$runner")
done

job_rows=""
for ((cycle=1; cycle<=cycle_count; cycle++)); do
  label=$(printf '%03d' "$cycle")
  job_rows+="${cycle}:${cycle_jobs[$cycle]}:${clock_jobs[$cycle]:-}:${compat_jobs[$cycle]:-};"
done
"$python" - "$root" "$commit" "$qualification" "$execution_runtime" "$job_rows" <<'PY'
import hashlib
import json
import os
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
rows = []
for encoded in sys.argv[5].split(";"):
    if not encoded:
        continue
    cycle, model, clock, compatibility = encoded.split(":")
    rows.append({
        "cycle": int(cycle),
        "model_job_id": model,
        "clock_job_id": clock or None,
        "compatibility_job_id": compatibility or None,
    })
payload = {
    "schema_version": 1,
    "status": "SUBMITTED",
    "purpose": "corrected-wind repeated-day equilibration experiment",
    "source_commit": sys.argv[2],
    "wind_fix_qualification": sys.argv[3],
    "execution_runtime": sys.argv[4],
    "cycles": rows,
}
output = root / "submission_plan.json"
temporary = root / ".submission_plan.json.tmp"
temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
os.replace(temporary, output)
digest = hashlib.sha256(output.read_bytes()).hexdigest()
(root / "submission_plan.json.ready").write_text(digest + "\n")
print(json.dumps(payload, indent=2, sort_keys=True))
PY
