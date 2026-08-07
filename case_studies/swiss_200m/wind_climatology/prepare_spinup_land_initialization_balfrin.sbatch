#!/bin/bash -l
# Publish one time-matched REA-L land initialization for a spin-up member.
# Submit as an array with one task per experiment-manifest run and a %2 cap.
#SBATCH --job-name=wind-spinup-land
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --time=00:45:00
#SBATCH --no-requeue

set -euo pipefail

experiment=${WIND_SPINUP_EXPERIMENT:?Set WIND_SPINUP_EXPERIMENT}
static_template=${WIND_SPINUP_STATIC_TEMPLATE:?Set WIND_SPINUP_STATIC_TEMPLATE}
land_root=${WIND_SPINUP_LAND_ROOT:?Set WIND_SPINUP_LAND_ROOT}
static_root=${WIND_SPINUP_STATIC_ROOT:?Set WIND_SPINUP_STATIC_ROOT}
producer=${WIND_SPINUP_LAND_PRODUCER:?Set WIND_SPINUP_LAND_PRODUCER}
builder=${WIND_SPINUP_LAND_BUILDER:?Set WIND_SPINUP_LAND_BUILDER}
python=${WIND_SPINUP_PYTHON:?Set WIND_SPINUP_PYTHON}
repo_root=${REPO_ROOT:?Set REPO_ROOT to the immutable runtime release}
task_id=${SLURM_ARRAY_TASK_ID:?Submit this script as an array}

for path in "$experiment" "$experiment.ready" "$static_template" \
  "$static_template.ready" "$producer" "$builder" "$python"; do
  test -e "$path" || {
    echo "missing wind spin-up land input: $path" >&2
    exit 2
  }
done

IFS=$'\t' read -r run_id valid_time static_file < <(
  "$python" - "$experiment" "$task_id" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1]))
index = int(sys.argv[2])
runs = payload["runs"]
if not 0 <= index < len(runs):
    raise SystemExit(f"array index {index} outside 0..{len(runs) - 1}")
run = runs[index]
print(run["run_id"], run["integration_start"], run["static_file"], sep="\t")
PY
)

IFS=$'\t' read -r valid_date valid_clock cycle_date step_hours < <(
  "$python" - "$valid_time" <<'PY'
from datetime import datetime
import sys

valid = datetime.fromisoformat(sys.argv[1])
print(
    valid.strftime("%Y%m%d"),
    valid.strftime("%H%M"),
    valid.strftime("%Y%m%d"),
    valid.hour,
    sep="\t",
)
PY
)

land_file="$land_root/rea_l_land_state_${valid_date}_${valid_clock}.nc"
land_manifest="$land_root/rea_l_land_state_${valid_date}_${valid_clock}.manifest.json"
static_manifest="$static_root/${run_id}.land_initialization.json"
mkdir -p "$land_root" "$static_root"
test "$static_file" = \
  "$static_root/domain_static_alpine_bridge_200m_rea_l_${valid_date}_${valid_clock}.nc"

if ! test -f "$land_file.ready"; then
  REPO_ROOT="$repo_root" \
  REA_L_LAND_DATE="$valid_date" \
  REA_L_LAND_TIME="$valid_clock" \
  REA_L_LAND_CYCLE_DATE="$cycle_date" \
  REA_L_LAND_CYCLE_TIME=0000 \
  REA_L_LAND_STEP_HOURS="$step_hours" \
  REA_L_LAND_OUTPUT_DIR="$land_root" \
    bash "$producer"
fi
for path in "$land_file" "$land_file.ready" "$land_manifest" \
  "$land_manifest.ready"; do
  test -e "$path" || {
    echo "land-state producer did not publish $path" >&2
    exit 1
  }
done

if ! test -f "$static_file.ready"; then
  "$python" "$builder" \
    --static-template "$static_template" \
    --land-state "$land_file" \
    --output "$static_file" \
    --manifest "$static_manifest" \
    --valid-time "$valid_time"
fi
for path in "$static_file" "$static_file.ready" "$static_manifest" \
  "$static_manifest.ready"; do
  test -e "$path" || {
    echo "land initialization did not publish $path" >&2
    exit 1
  }
done

echo "PASS run=$run_id valid_time=$valid_time static=$static_file"
