#!/bin/bash
#SBATCH --job-name=hicar_scaling_gpu_smoke
#SBATCH --partition=debug
#SBATCH --nodes=2
#SBATCH --ntasks-per-node=3
#SBATCH --cpus-per-task=1
#SBATCH --gres=gpu:3
#SBATCH --time=00:25:00
#SBATCH --exclusive
set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"; module purge || true; module use "$USER_ENV_ROOT/modules"
module load python/3.11.7 nvhpc/24.5 cray-mpich-nvhpc/8.1.30 cuda/12.3.0-gcc netcdf-c/4.9.2-nvhpc netcdf-fortran/4.6.1-nvhpc hdf5/1.14.3-nvhpc fftw/3.3.10-gcc
ROOT=${SCALING_ROOT:?}; HICAR_ROOT=${HICAR_ROOT:-$SCRATCH/icon_hicar/HICAR-scaling}
NML="$SCRATCH/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_openacc_1h/input/icon_hicar_250m_openacc_1h.nml"
test -x "$HICAR_ROOT/bin/HICAR_gpu"; test -f "$NML"
# NCCL owns the device halo transport in this executable.  Disable Cray MPICH's
# eager CUDA probing at MPI_Init; otherwise every rank can create a default-GPU
# context before HICAR applies its rank-local OpenACC device selection.
export HICAR_IO_PER_NODE=1 OMP_NUM_THREADS=1 MPICH_GPU_SUPPORT_ENABLED=0 MPICH_GPU_MANAGED_MEMORY_SUPPORT_ENABLED=0
rm -rf "$ROOT/provenance/gpu_smoke_output" "$ROOT/provenance/gpu_smoke_restart"
mkdir -p "$ROOT/provenance/gpu_smoke_output" "$ROOT/provenance/gpu_smoke_restart"
sed -e "s#hicar_openacc_1h/restart_gpu/#hicar_scaling/provenance/gpu_smoke_restart/#" -e "s#hicar_openacc_1h/gpu_output/#hicar_scaling/provenance/gpu_smoke_output/#" "$NML" > "$ROOT/provenance/gpu_smoke.nml"
nvidia-smi -L > "$ROOT/provenance/gpu_smoke_devices.txt"
# Capture Slurm's unmodified per-task GPU visibility before the MPMD launcher
# changes any OpenACC settings.  This is essential when diagnosing cgroup maps.
srun -n 6 --ntasks-per-node=3 bash -c 'echo "rank=${SLURM_PROCID} local=${SLURM_LOCALID} cvd=${CUDA_VISIBLE_DEVICES:-unset}"; nvidia-smi -L' > "$ROOT/provenance/gpu_smoke_task_gpu_visibility.txt"
srun -n 6 --ntasks-per-node=3 nvaccelinfo -v > "$ROOT/provenance/gpu_smoke_cuda_context_probe.txt" 2>&1
# Two compute ranks plus one I/O rank run on each node.  Every rank receives a
# distinct GPU; HICAR's rank-local device selection then agrees with OpenACC.
cat > "$ROOT/provenance/gpu_smoke.conf" <<EOF
0-1,3-4 /bin/bash -c 'export ACC_DEVICE_TYPE=nvidia ACC_DEVICE_NUM=\${SLURM_LOCALID} CUDA_VISIBLE_DEVICES=0,1,2; exec "$HICAR_ROOT/bin/HICAR_gpu" "$ROOT/provenance/gpu_smoke.nml"'
2,5 /bin/bash -c 'export ACC_DEVICE_TYPE=nvidia ACC_DEVICE_NUM=2 CUDA_VISIBLE_DEVICES=0,1,2; exec "$HICAR_ROOT/bin/HICAR_gpu" "$ROOT/provenance/gpu_smoke.nml"'
EOF
srun -u --cpu-bind=cores --multi-prog "$ROOT/provenance/gpu_smoke.conf" | tee "$ROOT/provenance/gpu_smoke.out"
grep -q 'Timing across all compute images:' "$ROOT/provenance/gpu_smoke.out"
test -n "$(find "$ROOT/provenance/gpu_smoke_output" -name '*.nc' -print -quit)"
touch "$ROOT/provenance/gpu_READY"
