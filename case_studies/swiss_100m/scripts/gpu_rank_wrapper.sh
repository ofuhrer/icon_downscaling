#!/usr/bin/env bash
# Four GPU compute ranks plus one CPU-only I/O rank per Balfrin A100 node.
set -euo pipefail

exe=${1:?missing HICAR executable}
nml=${2:?missing HICAR namelist}
local_id=${SLURM_LOCALID:?missing SLURM_LOCALID}
rank=${SLURM_PROCID:-unknown}
memory_dir=${HICAR_MEMORY_DIR:-}
export MPICH_GPU_SUPPORT_ENABLED=0 MPICH_GPU_MANAGED_MEMORY_SUPPORT_ENABLED=0

run_monitored() {
  local gpu_index=${1}
  shift
  if [[ -z "$memory_dir" ]]; then
    exec "$@"
  fi

  mkdir -p "$memory_dir"
  "$@" &
  local child=$!
  local host
  host=$(hostname -s)
  local gpu_monitor=
  local node_monitor=

  if [[ "$gpu_index" != "none" ]]; then
    (
      peak_mib=0
      total_mib=0
      while kill -0 "$child" 2>/dev/null; do
        IFS=, read -r used total < <(
          nvidia-smi -i "$gpu_index" \
            --query-gpu=memory.used,memory.total \
            --format=csv,noheader,nounits 2>/dev/null | head -1 || true
        )
        used=${used//[[:space:]]/}
        total=${total//[[:space:]]/}
        [[ $used =~ ^[0-9]+$ ]] && (( used > peak_mib )) && peak_mib=$used
        [[ $total =~ ^[0-9]+$ ]] && total_mib=$total
        sleep 1
      done
      printf 'rank=%s\nhost=%s\ngpu_index=%s\npeak_gpu_memory_mib=%s\ntotal_gpu_memory_mib=%s\n' \
        "$rank" "$host" "$gpu_index" "$peak_mib" "$total_mib" \
        > "$memory_dir/gpu_rank_${rank}_${host}.txt"
    ) &
    gpu_monitor=$!
  fi

  if (( local_id == 0 )); then
    (
      total_kib=0
      minimum_available_kib=0
      while kill -0 "$child" 2>/dev/null; do
        total=$(
          awk '/^MemTotal:/ {print $2; exit}' /proc/meminfo
        )
        available=$(
          awk '/^MemAvailable:/ {print $2; exit}' /proc/meminfo
        )
        [[ $total =~ ^[0-9]+$ ]] && total_kib=$total
        if [[ $available =~ ^[0-9]+$ ]] && {
          (( minimum_available_kib == 0 )) || (( available < minimum_available_kib ))
        }; then
          minimum_available_kib=$available
        fi
        sleep 1
      done
      printf 'host=%s\ntotal_memory_kib=%s\nminimum_available_memory_kib=%s\n' \
        "$host" "$total_kib" "$minimum_available_kib" \
        > "$memory_dir/node_${host}.txt"
    ) &
    node_monitor=$!
  fi

  set +e
  wait "$child"
  status=$?
  set -e
  [[ -n "$gpu_monitor" ]] && wait "$gpu_monitor"
  [[ -n "$node_monitor" ]] && wait "$node_monitor"
  return "$status"
}

if (( local_id == 4 )); then
  export CUDA_VISIBLE_DEVICES=
  run_monitored none numactl --physcpubind=1 --membind=0 "$exe" "$nml"
  exit $?
fi

case "$local_id" in
  0) cpu=48; numa=3 ;;
  1) cpu=32; numa=2 ;;
  2) cpu=16; numa=1 ;;
  3) cpu=0;  numa=0 ;;
  *) echo "unexpected local rank: $local_id" >&2; exit 2 ;;
esac
export ACC_DEVICE_TYPE=nvidia ACC_DEVICE_NUM=0 CUDA_VISIBLE_DEVICES="$local_id"
run_monitored "$local_id" numactl --physcpubind="$cpu" --membind="$numa" "$exe" "$nml"
