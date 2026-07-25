# Build and performance reference

## CPU modules and configure template

```bash
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"
module load gcc/12.3.0 cray-mpich-gcc/8.1.30 cmake/3.24.4-gcc \
  netcdf-c/4.8.1-gcc netcdf-fortran/4.5.4-gcc fftw/3.3.10-gcc

cmake -S . -B build_cpu -DFC=mpifort -DMODE=release -DOPENACC=OFF -DNCCL=OFF
cmake --build build_cpu --target HICAR HICAR-tester --parallel 8
```

## GPU modules and configure template

```bash
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"
module load nvhpc/24.5 cray-mpich-nvhpc/8.1.30 cuda/12.3.0-gcc \
  cmake/3.24.4-gcc gmake/4.4.1-gcc \
  netcdf-c/4.9.2-nvhpc netcdf-fortran/4.6.1-nvhpc \
  hdf5/1.14.3-nvhpc fftw/3.3.10-gcc

cmake -S . -B build_gpu_mpi -DFC=mpifort -DMODE=release \
  -DGPU_ARCH=cc80 -DOPENACC=ON -DNCCL=OFF
cmake --build build_gpu_mpi --target HICAR HICAR-tester --parallel 8
```

For NCCL comparison, use a separate build directory and `-DNCCL=ON`.

## Four-GPU compute plus CPU-only I/O topology

Job `4833066` validated a full five-rank, five-minute NCCL smoke case on one
four-A100 node. Ranks 0-3 each had one visible GPU; rank 4 was HICAR's I/O
server with `CUDA_VISIBLE_DEVICES` empty. The job completed in 13 s, reported
4.55 s HICAR initialization, formed a 2x2 compute decomposition, and wrote a
NetCDF output file.

Use `MPICH_GPU_SUPPORT_ENABLED=0` and
`MPICH_GPU_MANAGED_MEMORY_SUPPORT_ENABLED=0` on **every** rank in this NCCL
configuration. A minimal probe (job `4833062`) established that enabling these
settings only on the four compute ranks while disabling them on the hidden-GPU
I/O rank stalls all ranks inside `MPI_Init`. This happens before model
initialization and creates no usable CUDA context. NCCL provides the device
halo transport in this topology.

## Transport correctness

Balfrin job `4832692` passed all four-rank/four-A100 halo cases for both transports: batch, 3-D variable, 2-D variable, staggered U/V, corners, and `dqdt`.

## Single-node performance

Balfrin job `4832736` used five alternating-order trials and 500 steady-state exchanges per case. Medians:

| Case | GPU-aware MPI | NCCL |
|---|---:|---:|
| 3-D batch | 130.74 us | 228.87 us |
| 2-D batch | 41.56 us | 168.92 us |
| primary 3-D + 2-D pair | 171.12 us | 397.46 us |
| equal-weight sum of 17 cases | 5.04 ms | 7.12 ms |

Use `NCCL=OFF` for the current single-node four-A100 workflow. Multi-node performance and sustained full-model GPU runtime remain unvalidated.

## NUMA placement and multi-node smoke

Jobs `4833089`/`4833093` measured two Balfrin A100 nodes (`nid001037` and
`nid001040`). Both have one 128-logical-CPU EPYC socket with four NUMA domains,
not two CPU sockets. GPU locality is reversed relative to the CUDA ordinal:
GPU 0→NUMA 3, GPU 1→NUMA 2, GPU 2→NUMA 1, GPU 3→NUMA 0. All GPU pairs expose
NV4 links.

Job `4833093` used a PCI-derived rank wrapper to bind the four local compute
ranks to CPU/NUMA pairs `(48,3)`, `(32,2)`, `(16,1)`, and `(0,0)` respectively;
the I/O rank used `(1,0)`. Every compute rank saw exactly one GPU and
initialized OpenACC; the I/O rank saw none and skipped OpenACC.

Job `4833095` used the same wrapper for a two-node full HICAR smoke. It ran 10
ranks (five per node), created eight compute ranks in a 2x4 decomposition plus
two I/O servers, initialized multi-node NCCL, wrote output, and completed in
13 s. It validates topology and correctness only; it is not a multi-node
performance measurement.

## CPU reference

The frozen 250 m one-hour CPU release run completed in about 18 seconds versus about 99 seconds for debug on the tested setup. Release differed slightly from debug because of aggressive optimization but remained finite and physically comparable. Use debug for diagnosis, not throughput estimates.
