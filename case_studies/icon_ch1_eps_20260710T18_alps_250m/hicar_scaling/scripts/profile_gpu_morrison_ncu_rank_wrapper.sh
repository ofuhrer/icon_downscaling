#!/usr/bin/env bash
# One A100 compute rank under Nsight Compute and one CUDA-hidden I/O rank.
set -euo pipefail

exe=${1:?missing HICAR executable}
nml=${2:?missing namelist}
profile_dir=${3:?missing profile directory}
local_id=${SLURM_LOCALID:?missing SLURM_LOCALID}
kernel=${HICAR_NCU_KERNEL:-module_mp_morr_two_moment_gpu_mp_morr_two_moment_gpu_3265}

export MPICH_GPU_SUPPORT_ENABLED=0 MPICH_GPU_MANAGED_MEMORY_SUPPORT_ENABLED=0
if (( local_id == 1 )); then
  export CUDA_VISIBLE_DEVICES=
  exec numactl --physcpubind=1 --membind=0 "$exe" "$nml"
fi

[[ $local_id == 0 ]] || { echo "unexpected local rank: $local_id" >&2; exit 2; }
export ACC_DEVICE_TYPE=nvidia ACC_DEVICE_NUM=0 CUDA_VISIBLE_DEVICES=0
exec numactl --physcpubind=48 --membind=3 \
  ncu --force-overwrite --target-processes all --kernel-name-base function \
  --kernel-name "$kernel" --launch-count 1 \
  --set full --export "$profile_dir/hicar_morrison" "$exe" "$nml"
