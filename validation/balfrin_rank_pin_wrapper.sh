#!/bin/bash
set -euo pipefail

local_id=${SLURM_LOCALID:?SLURM_LOCALID is required}
host=$(hostname -s)

first_cpu_on_numa() {
  lscpu -p=CPU,NODE | awk -F, -v wanted="$1" '$1 !~ /^#/ && $2 == wanted { print $1; exit }'
}

second_cpu_on_numa() {
  lscpu -p=CPU,NODE | awk -F, -v wanted="$1" '$1 !~ /^#/ && $2 == wanted { count++; if (count == 2) { print $1; exit } }'
}

if (( local_id < 4 )); then
  gpu=$local_id
  bus_id=$(nvidia-smi --id="$gpu" --query-gpu=pci.bus_id --format=csv,noheader | tr -d '[:space:]')
  bus_id=$(printf '0000:%s' "${bus_id#00000000:}" | tr '[:upper:]' '[:lower:]')
  numa_node=$(cat "/sys/bus/pci/devices/${bus_id}/numa_node")
  cpu=$(first_cpu_on_numa "$numa_node")
  export CUDA_VISIBLE_DEVICES=$gpu
  export MPICH_GPU_SUPPORT_ENABLED=0
  export MPICH_GPU_MANAGED_MEMORY_SUPPORT_ENABLED=0
  role=compute
else
  gpu=none
  numa_node=0
  cpu=$(second_cpu_on_numa "$numa_node")
  export CUDA_VISIBLE_DEVICES=
  export MPICH_GPU_SUPPORT_ENABLED=0
  export MPICH_GPU_MANAGED_MEMORY_SUPPORT_ENABLED=0
  role=io
fi

printf 'host=%s global=%s local=%s role=%s gpu=%s gpu_numa=%s cpu=%s cvd=%s\n' \
  "$host" "${SLURM_PROCID:?}" "$local_id" "$role" "$gpu" "$numa_node" "$cpu" \
  "${CUDA_VISIBLE_DEVICES:-<empty>}"
exec numactl --physcpubind="$cpu" --membind="$numa_node" bash -c '
  taskset -pc $$
  numactl --show
  exec "$@"
' -- "$@"
