#!/usr/bin/env bash
# Profile one selected compute rank while preserving the production NCCL/NUMA
# topology used by gpu_rank_wrapper.sh.
set -euo pipefail

exe=${1:?missing HICAR executable}
shift
(( $# > 0 )) || { echo "missing HICAR arguments" >&2; exit 2; }
local_id=${SLURM_LOCALID:?missing SLURM_LOCALID}
profile_local_id=${HICAR_NSYS_LOCAL_RANK:-0}
trace_dir=${HICAR_NSYS_OUTPUT_DIR:?set HICAR_NSYS_OUTPUT_DIR}
export MPICH_GPU_SUPPORT_ENABLED=0 MPICH_GPU_MANAGED_MEMORY_SUPPORT_ENABLED=0

if (( local_id == 4 )); then
  export CUDA_VISIBLE_DEVICES=
  exec numactl --physcpubind=1 --membind=0 "$exe" "$@"
fi

case "$local_id" in
  0) cpu=48; numa=3 ;;
  1) cpu=32; numa=2 ;;
  2) cpu=16; numa=1 ;;
  3) cpu=0;  numa=0 ;;
  *) echo "unexpected local rank: $local_id" >&2; exit 2 ;;
esac
export ACC_DEVICE_TYPE=nvidia ACC_DEVICE_NUM=0 CUDA_VISIBLE_DEVICES="$local_id"

if (( local_id == profile_local_id )); then
  mkdir -p "$trace_dir"
  exec numactl --physcpubind="$cpu" --membind="$numa" \
    nsys profile \
      --trace=cuda,openacc,nvtx,osrt \
      --sample=none \
      --cpuctxsw=none \
      --stats=true \
      --force-overwrite=true \
      --output="$trace_dir/rank${SLURM_PROCID:?missing SLURM_PROCID}" \
      "$exe" "$@"
fi

exec numactl --physcpubind="$cpu" --membind="$numa" "$exe" "$@"
