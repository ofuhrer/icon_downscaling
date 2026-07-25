#!/bin/bash -l
# Run a restart continuation while preserving the source chain's restart directory.
#SBATCH --job-name=hicar-restart-overlap
#SBATCH --partition=normal
#SBATCH --nodes=4
#SBATCH --ntasks-per-node=5
#SBATCH --cpus-per-task=1
#SBATCH --gres=gpu:4
#SBATCH --time=02:30:00
#SBATCH --exclusive

set -euo pipefail

runner=${HICAR_STREAM_RUNNER:-$SCRATCH/icon_hicar/case_studies/swiss_200m/scripts/run_rea_l_stream_chunk_balfrin.sbatch}
source_checkpoint=${STREAM_RESTART_SOURCE_CHECKPOINT:?Set STREAM_RESTART_SOURCE_CHECKPOINT}
restart_dir=${STREAM_RESTART_DIR:?Set STREAM_RESTART_DIR}
restart_from=${STREAM_RESTART_FROM:?Set STREAM_RESTART_FROM}

test -f "$runner"
test -s "$source_checkpoint"
mkdir -p "$restart_dir"
target="$restart_dir/$(basename "$source_checkpoint")"
if test -e "$target"; then
  test "$source_checkpoint" -ef "$target" || {
    echo "existing overlap input is not the source checkpoint: $target" >&2
    exit 2
  }
else
  ln "$source_checkpoint" "$target"
fi
test "$source_checkpoint" -ef "$target"

export STREAM_RESTART_DIR="$restart_dir"
export STREAM_RESTART_FROM="$restart_from"
exec bash "$runner"
