#!/usr/bin/env bash
# Four GPU compute ranks plus one CPU-only I/O rank per Balfrin A100 node.
set -euo pipefail

exe=${1:?missing HICAR executable}
nml=${2:?missing HICAR namelist}
local_id=${SLURM_LOCALID:?missing SLURM_LOCALID}
export MPICH_GPU_SUPPORT_ENABLED=0 MPICH_GPU_MANAGED_MEMORY_SUPPORT_ENABLED=0

if (( local_id == 4 )); then
  export CUDA_VISIBLE_DEVICES=
  exec numactl --physcpubind=1 --membind=0 "$exe" "$nml"
fi

case "$local_id" in
  0) cpu=48; numa=3 ;;
  1) cpu=32; numa=2 ;;
  2) cpu=16; numa=1 ;;
  3) cpu=0;  numa=0 ;;
  *) echo "unexpected local rank: $local_id" >&2; exit 2 ;;
esac
export ACC_DEVICE_TYPE=nvidia ACC_DEVICE_NUM=0 CUDA_VISIBLE_DEVICES="$local_id"
exec numactl --physcpubind="$cpu" --membind="$numa" "$exe" "$nml"
