#!/usr/bin/env bash
# One GPU compute rank plus one CPU-only I/O rank for tiny qualification domains.
set -euo pipefail

exe=${1:?missing HICAR executable}
nml=${2:?missing HICAR namelist}
local_id=${SLURM_LOCALID:?missing SLURM_LOCALID}
export MPICH_GPU_SUPPORT_ENABLED=0 MPICH_GPU_MANAGED_MEMORY_SUPPORT_ENABLED=0

case "$local_id" in
  0)
    export ACC_DEVICE_TYPE=nvidia ACC_DEVICE_NUM=0 CUDA_VISIBLE_DEVICES=0
    exec numactl --physcpubind=48 --membind=3 "$exe" "$nml"
    ;;
  1)
    export CUDA_VISIBLE_DEVICES=
    exec numactl --physcpubind=1 --membind=0 "$exe" "$nml"
    ;;
  *)
    echo "unexpected local rank for micro wrapper: $local_id" >&2
    exit 2
    ;;
esac
