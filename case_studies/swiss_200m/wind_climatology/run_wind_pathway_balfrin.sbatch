#!/bin/bash -l
#SBATCH --job-name=wind-pathway
#SBATCH --partition=normal
#SBATCH --nodes=2
#SBATCH --ntasks-per-node=5
#SBATCH --cpus-per-task=1
#SBATCH --gres=gpu:4
#SBATCH --time=01:00:00
#SBATCH --exclusive

set -euo pipefail
: "${HICAR_EXPERIMENT_PLAN:?set HICAR_EXPERIMENT_PLAN}"
: "${HICAR_ANALYSIS_RUNTIME:?set HICAR_ANALYSIS_RUNTIME}"
: "${HICAR_ANALYSIS_PYTHON:?set HICAR_ANALYSIS_PYTHON}"
index=${HICAR_PATHWAY_INDEX:-${SLURM_ARRAY_TASK_ID:?}}

IFS=$'\t' read -r run_id model_plan run_dir source_restart static_file start sx density < <(
  "$HICAR_ANALYSIS_PYTHON" - "$HICAR_EXPERIMENT_PLAN" "$index" <<'PY'
import json
import sys
row = json.load(open(sys.argv[1]))["runs"][int(sys.argv[2])]
print(
    row["run_id"], row["model_plan"], row["run_dir"], row["restart"],
    row["static_file"], row["start"], row["sx"], row["advect_density"],
    sep="\t",
)
PY
)

start_file=${start/T/_}
start_file=${start_file//:/-}
static_basename=$(basename "${static_file%.nc}")
restart_dir="$run_dir/restart"
staged_restart="$restart_dir/${static_basename}_${start_file}.nc"
mkdir -p "$restart_dir"
if test -e "$staged_restart"; then
  test "$(realpath "$staged_restart")" = "$(realpath "$source_restart")"
else
  ln -s "$(realpath "$source_restart")" "$staged_restart"
fi

export REPO_ROOT="$HICAR_ANALYSIS_RUNTIME"
export STREAM_PLAN="$model_plan"
export STREAM_RUN_DIR="$run_dir"
export STREAM_RESTART_DIR="$restart_dir"
export STREAM_RESTART_FROM="$start"
export STREAM_OUTPUT_PROFILE=wind_climatology
export STREAM_OUTPUT_INTERVAL=1800
export STREAM_WIND_REDUCTION_INTERVAL=3600
export STREAM_RESTART_INTERVAL_RECORDS=2
export STREAM_RESTART_OVERRIDE_CHECK=1
export STREAM_WIND_SX="$sx"
export STREAM_ADVECT_DENSITY="$density"
export HICAR_STATIC_FILE="$static_file"

echo "wind pathway replay: $run_id"
exec bash \
  "$HICAR_ANALYSIS_RUNTIME/case_studies/swiss_200m/scripts/run_rea_l_stream_chunk_balfrin.sbatch"
