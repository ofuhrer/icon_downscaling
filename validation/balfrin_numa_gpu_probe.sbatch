#!/bin/bash
#SBATCH --job-name=hicar_numa_gpu_probe
#SBATCH --partition=debug
#SBATCH --nodes=2
#SBATCH --ntasks=10
#SBATCH --ntasks-per-node=5
#SBATCH --cpus-per-task=1
#SBATCH --gres=gpu:4
#SBATCH --exclusive
#SBATCH --time=00:05:00
#SBATCH --output=/scratch/mch/olifu/icon_hicar/validation/gpu_io_cpu_smoke/logs/%x_%j.out
#SBATCH --error=/scratch/mch/olifu/icon_hicar/validation/gpu_io_cpu_smoke/logs/%x_%j.err

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"
module purge || true
module use "$USER_ENV_ROOT/modules"
module load nvhpc/24.5 cray-mpich-nvhpc/8.1.30 cuda/12.3.0-gcc >/dev/null

ROOT="$SCRATCH/icon_hicar/validation/gpu_io_cpu_smoke"
PROBE="$ROOT/bin/mpi_openacc_probe"
WRAPPER="$ROOT/input/balfrin_rank_pin_wrapper.sh"
test -x "$PROBE"
test -x "$WRAPPER"

echo '--- topology per node ---'
srun --nodes=2 --ntasks-per-node=1 --distribution=block:block bash -lc '
  echo "host=$(hostname -s)"
  lscpu | grep -E "Socket\(s\)|NUMA node\(s\)|CPU\(s\)"
  numactl -H
  nvidia-smi topo -m
  for d in /sys/bus/pci/devices/*; do
    test -r "$d/numa_node" || continue
    test -r "$d/vendor" || continue
    test "$(cat "$d/vendor")" = 0x10de || continue
    printf "gpu_pci=%s numa=%s\n" "$(basename "$d")" "$(cat "$d/numa_node")"
  done
'

echo '--- rank affinity and OpenACC probe ---'
srun --distribution=block:block --cpu-bind=none "$WRAPPER" "$PROBE"
