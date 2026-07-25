#!/usr/bin/env bash
# Persist peak RSS for one MPI rank.  /usr/bin/time observes the HICAR child,
# avoiding dependence on optional Slurm MaxRSS accounting.
set -euo pipefail
memory_dir=${1:?missing memory output directory}
shift
mkdir -p "$memory_dir"
rank=${SLURM_PROCID:-unknown}
host=$(hostname -s)
exec /usr/bin/time -v -o "$memory_dir/cpu_rank_${rank}_${host}.time" "$@"
