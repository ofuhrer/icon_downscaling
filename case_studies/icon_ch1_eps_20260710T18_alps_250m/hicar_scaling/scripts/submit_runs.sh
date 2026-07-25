#!/usr/bin/env bash
# Submit all generated repeat scripts only after binary and domain gates pass.
set -euo pipefail
ROOT=${SCALING_ROOT:?}; kind=${1:-all}; platform=${2:-all}
for script in $(find "$ROOT/runs" -name run.sbatch | sort); do
  id=$(basename "$(dirname "$(dirname "$script")")")
  [[ "$kind" == all || "$id" == "${kind}_"* ]] || continue
  [[ "$platform" == all || "$id" == *"_${platform}_"* ]] || continue
  sbatch "$script"
done
