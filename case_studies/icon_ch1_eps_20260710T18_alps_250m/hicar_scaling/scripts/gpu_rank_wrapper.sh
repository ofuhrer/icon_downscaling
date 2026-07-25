#!/usr/bin/env bash
# One compute rank per visible A100; the final local rank is a CPU-only I/O
# server.  NCCL owns device halo transport, so disable MPICH GPU support on
# every rank before MPI_Init.
set -euo pipefail
exe=${1:?missing HICAR executable}; nml=${2:?missing namelist}
local_id=${SLURM_LOCALID:?missing SLURM_LOCALID}
local_tasks=${HICAR_TASKS_PER_NODE:-5}
memory_dir=${HICAR_MEMORY_DIR:-}
export MPICH_GPU_SUPPORT_ENABLED=0 MPICH_GPU_MANAGED_MEMORY_SUPPORT_ENABLED=0

run_monitored() {
  local rank=${SLURM_PROCID:-unknown} host
  host=$(hostname -s)
  if [[ -z "$memory_dir" ]]; then
    exec "$@"
  fi
  mkdir -p "$memory_dir"
  "$@" &
  local pid=$! peak_mib=0 used monitor_status
  if [[ -n ${CUDA_VISIBLE_DEVICES:-} ]]; then
    (
      while kill -0 "$pid" 2>/dev/null; do
        used=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -1 || true)
        [[ $used =~ ^[0-9]+$ ]] && (( used > peak_mib )) && peak_mib=$used
        sleep 1
      done
      printf 'rank=%s\nhost=%s\npeak_gpu_memory_mib=%s\n' "$rank" "$host" "$peak_mib" > "$memory_dir/gpu_rank_${rank}_${host}.txt"
    ) &
    monitor_status=$!
  fi
  set +e
  wait "$pid"; local status=$?
  set -e
  [[ -n ${monitor_status:-} ]] && wait "$monitor_status"
  return "$status"
}
if (( local_id == local_tasks - 1 )); then
  export CUDA_VISIBLE_DEVICES=
  run_monitored numactl --physcpubind=1 --membind=0 "$exe" "$nml"
  exit $?
fi
case "$local_id" in
  0) cpu=48; numa=3 ;;
  1) cpu=32; numa=2 ;;
  2) cpu=16; numa=1 ;;
  3) cpu=0;  numa=0 ;;
  *) echo "unexpected compute local rank: $local_id" >&2; exit 2 ;;
esac
export ACC_DEVICE_TYPE=nvidia ACC_DEVICE_NUM=0 CUDA_VISIBLE_DEVICES=$local_id
run_monitored numactl --physcpubind="$cpu" --membind="$numa" "$exe" "$nml"
