# Build and performance reference

## Build invariants

- Compile on `pp-short` (or `pp-long` if a clean build cannot fit); compiling
  does not require a GPU allocation.
- Use a new build directory for every source worktree and for every
  CPU/GPU/transport variant. Never point an existing CMake cache at another
  source tree, and never toggle `OPENACC` or `NCCL` in place.
- Never run two builds concurrently against the same source clone, even when
  their build directories differ. The `HICAR-tester` dependency fetch writes
  `tests/Test_Cases` inside the source tree; concurrent CPU/GPU builds can
  remove that directory underneath one another. Use one clean source clone
  per simultaneous build variant. The canonical builder takes an atomic
  shared-filesystem directory lock beside the source root and rejects this
  unsafe topology. If a job is killed without running its shell trap, verify
  the recorded owner job is no longer active before removing the stale lock
  directory.
- Resolve NetCDF, FFTW, MPI, CUDA, and NCCL paths from the loaded module stack
  and pass them explicitly. HICAR's custom CMake discovery is not reliable
  enough to infer a mixed module environment.
- For the GCC CPU build, load OpenBLAS and pass its shared library as both
  `BLAS_LIBRARIES` and `LAPACK_LIBRARIES`. Without this, configure can fail at
  `Could NOT find BLAS` even though the rest of the stack is valid. NVHPC GPU
  builds use the compiler suite's bundled BLAS/LAPACK libraries; do not replace
  them with OpenBLAS merely to mirror the CPU recipe.
- `FC=mpifort` is HICAR's CMake option. Do not export `CC=mpicc` or
  `CXX=mpicxx` for the NVHPC GPU build: the validated GPU configuration uses
  `nvc` and `nvc++` for C/C++ and Cray `mpifort` for Fortran/MPI.
- Treat a completed compile as insufficient. Check the executable's dynamic
  linkage, require no `not found` entries, record its SHA-256, and run a small
  topology-matched smoke case before scientific work.

If reusing an incremental build, first require:

```bash
test "$(grep '^CMAKE_HOME_DIRECTORY:INTERNAL=' "$BUILD/CMakeCache.txt" |
  cut -d= -f2-)" = "$SRC"
git -C "$SRC" diff --quiet
git -C "$SRC" diff --cached --quiet
```

For release or qualification evidence, prefer a fresh build directory and
record the clean source commit, configure command, module list, executable
SHA-256, and `ldd` output.

## Canonical Balfrin entry point

Use
`case_studies/swiss_200m/scripts/build_hicar_balfrin.sbatch` for new builds.
It implements the three recipes below, refuses a dirty or wrongly pinned
source, refuses an existing build directory or a concurrent build using the
same source clone, checks dynamic linkage, and writes
`hicar_build_provenance.txt` beside the executable.

Submit it on the CPU build partition with exact paths and source identity:

```bash
sbatch --export=ALL,\
HICAR_COORDINATOR_ROOT="$PWD",\
HICAR_SOURCE_ROOT="$PWD/HICAR",\
HICAR_BUILD_ROOT="$SCRATCH/icon_hicar/build/HICAR-qualified-cpu-release",\
HICAR_EXPECTED_COMMIT=<full-40-character-commit>,\
HICAR_BUILD_VARIANT=cpu \
case_studies/swiss_200m/scripts/build_hicar_balfrin.sbatch
```

Choose exactly one of:

- `HICAR_BUILD_VARIANT=cpu` for the GCC/Cray-MPICH release executable.
- `HICAR_BUILD_VARIANT=gpu-mpi` for a single-node A100 executable using
  GPU-aware Cray MPICH (`NCCL=OFF`).
- `HICAR_BUILD_VARIANT=gpu-nccl` for the national multi-node topology
  (`NCCL=ON`, CPU-only I/O ranks, MPICH GPU support disabled).

Set a distinct `HICAR_BUILD_ROOT` for every source commit and variant. The
templates below are the expanded commands implemented by the builder and are
retained for audit/debugging; do not copy an older case-study build script as
a new production recipe.

## CPU release build

```bash
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module purge || true
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module load gcc/12.3.0 cray-mpich-gcc/8.1.30 \
  cmake/3.24.4-gcc gmake/4.4.1-gcc \
  netcdf-c/4.8.1-gcc netcdf-fortran/4.5.4-gcc fftw/3.3.10-gcc \
  openblas/0.3.26-gcc

SRC=${HICAR_SOURCE_ROOT:?}
BUILD=${HICAR_BUILD_ROOT:?use a new CPU build directory}
test ! -e "$BUILD"
nc_prefix=$(nc-config --prefix)
nf_prefix=$(nf-config --prefix)
fftw_prefix=$(pkg-config --variable=prefix fftw3)
mpi_prefix=$(dirname "$(dirname "$(command -v mpifort)")")
openblas_root=${OPENBLAS_ROOT:?}
test -f "$nc_prefix/lib/libnetcdf.so"
test -f "$nf_prefix/lib/libnetcdff.so"
test -f "$openblas_root/lib/libopenblas.so"

cmake -S "$SRC" -B "$BUILD" \
  -DFC=mpifort -DMODE=release -DOPENACC=OFF -DNCCL=OFF \
  -DNETCDF_DIR="$nc_prefix" \
  -DNETCDF_INCLUDES="$nc_prefix/include;$nf_prefix/include" \
  -DNETCDF_INCLUDES_F90="$nf_prefix/include" \
  -DNETCDF_LIBRARIES_C="$nc_prefix/lib/libnetcdf.so" \
  -DNETCDF_LIBRARIES_F90="$nf_prefix/lib/libnetcdff.so" \
  -DFFTW_DIR="$fftw_prefix" \
  -DMPI_DIR="$mpi_prefix" \
  -DBLAS_LIBRARIES="$openblas_root/lib/libopenblas.so" \
  -DLAPACK_LIBRARIES="$openblas_root/lib/libopenblas.so" \
  -DCMAKE_BUILD_RPATH="$nf_prefix/lib;$nc_prefix/lib;$openblas_root/lib"
cmake --build "$BUILD" --target HICAR HICAR-tester \
  --parallel "${SLURM_CPUS_PER_TASK:-8}"

test -x "$BUILD/HICAR"
! ldd "$BUILD/HICAR" | grep -q 'not found'
sha256sum "$BUILD/HICAR"
```

Use the same recipe with `MODE=debug` and a distinct directory for diagnosis.
Do not use a debug executable for throughput estimates.

## A100 OpenACC build: single-node GPU-aware MPI

This variant is for four compute ranks without a CPU-only I/O server. It uses
GPU-aware Cray MPICH and therefore has `NCCL=OFF`.

```bash
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module purge || true
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module load nvhpc/24.5 cray-mpich-nvhpc/8.1.30 cuda/12.3.0-gcc \
  cmake/3.24.4-gcc gmake/4.4.1-gcc \
  netcdf-c/4.9.2-nvhpc netcdf-fortran/4.6.1-nvhpc \
  hdf5/1.14.3-nvhpc fftw/3.3.10-gcc

SRC=${HICAR_SOURCE_ROOT:?}
BUILD=${HICAR_BUILD_ROOT:?use a new GPU-MPI build directory}
test ! -e "$BUILD"
nc_prefix=$(nc-config --prefix)
nf_prefix=$(nf-config --prefix)
fftw_prefix=$(pkg-config --variable=prefix fftw3)
mpi_prefix=$(dirname "$(dirname "$(command -v mpifort)")")
cuda_target="$CUDA_HOME/targets/x86_64-linux"

cmake -S "$SRC" -B "$BUILD" \
  -DFC=mpifort -DMODE=release -DOPENACC=ON -DNCCL=OFF \
  -DGPU_ARCH=cc80 \
  -DCMAKE_C_COMPILER=nvc -DCMAKE_CXX_COMPILER=nvc++ \
  -DMPI_Fortran_COMPILER="$(command -v mpifort)" \
  -DNETCDF_DIR="$nc_prefix" \
  -DNETCDF_INCLUDES="$nc_prefix/include;$nf_prefix/include" \
  -DNETCDF_INCLUDES_F90="$nf_prefix/include" \
  -DNETCDF_LIBRARIES_C="$nc_prefix/lib/libnetcdf.so" \
  -DNETCDF_LIBRARIES_F90="$nf_prefix/lib/libnetcdff.so" \
  -DFFTW_DIR="$fftw_prefix" \
  -DFFTW_INCLUDES="$fftw_prefix/include" \
  -DFFTW_LIBRARIES="$fftw_prefix/lib/libfftw3.so" \
  -DMPI_DIR="$mpi_prefix" \
  -DCUDAToolkit_ROOT="$CUDA_HOME" \
  -DCUFFT_LIBRARY="$cuda_target/lib/libcufft.so" \
  -DCMAKE_BUILD_RPATH="$nf_prefix/lib;$nc_prefix/lib"
cmake --build "$BUILD" --target HICAR HICAR-tester \
  --parallel "${SLURM_CPUS_PER_TASK:-8}"

test -x "$BUILD/HICAR_gpu"
ldd "$BUILD/HICAR_gpu" | grep -q libcufft
! ldd "$BUILD/HICAR_gpu" | grep -q libnccl
! ldd "$BUILD/HICAR_gpu" | grep -q 'not found'
sha256sum "$BUILD/HICAR_gpu"
```

At runtime set `MPICH_GPU_SUPPORT_ENABLED=1` uniformly on all four ranks.

## A100 OpenACC build: multi-node NCCL production topology

Switzerland-wide multi-node runs use four compute ranks plus one CPU-only I/O
rank per node. They require an `NCCL=ON` executable and a launcher that sets
`MPICH_GPU_SUPPORT_ENABLED=0` on every rank. Do not use the preceding
`NCCL=OFF` executable with that launcher: device-backed MPI windows can fail
during initialization.

Use the same NVHPC module stack as above, then:

```bash
SRC=${HICAR_SOURCE_ROOT:?}
BUILD=${HICAR_BUILD_ROOT:?use a new GPU-NCCL build directory}
test ! -e "$BUILD"
nc_prefix=$(nc-config --prefix)
nf_prefix=$(nf-config --prefix)
fftw_prefix=$(pkg-config --variable=prefix fftw3)
mpi_prefix=$(dirname "$(dirname "$(command -v mpifort)")")
cuda_target="$CUDA_HOME/targets/x86_64-linux"
nvhpc_sdk_root=$(dirname "$(dirname "$(dirname "$(command -v nvc)")")")
nccl_prefix="$nvhpc_sdk_root/comm_libs/12.4/nccl"
test -f "$nccl_prefix/include/nccl.h"
test -f "$nccl_prefix/lib/libnccl.so"

cmake -S "$SRC" -B "$BUILD" \
  -DFC=mpifort -DMODE=release -DOPENACC=ON -DNCCL=ON \
  -DGPU_ARCH=cc80 \
  -DCMAKE_C_COMPILER=nvc -DCMAKE_CXX_COMPILER=nvc++ \
  -DMPI_Fortran_COMPILER="$(command -v mpifort)" \
  -DNETCDF_DIR="$nc_prefix" \
  -DNETCDF_INCLUDES="$nc_prefix/include;$nf_prefix/include" \
  -DNETCDF_INCLUDES_F90="$nf_prefix/include" \
  -DNETCDF_LIBRARIES_C="$nc_prefix/lib/libnetcdf.so" \
  -DNETCDF_LIBRARIES_F90="$nf_prefix/lib/libnetcdff.so" \
  -DFFTW_DIR="$fftw_prefix" \
  -DFFTW_INCLUDES="$fftw_prefix/include" \
  -DFFTW_LIBRARIES="$fftw_prefix/lib/libfftw3.so" \
  -DMPI_DIR="$mpi_prefix" \
  -DCUDAToolkit_ROOT="$CUDA_HOME" \
  -DCUFFT_LIBRARY="$cuda_target/lib/libcufft.so" \
  -DNCCL_INCLUDE_DIR="$nccl_prefix/include" \
  -DNCCL_LIBRARY="$nccl_prefix/lib/libnccl.so" \
  -DCMAKE_BUILD_RPATH="$nf_prefix/lib;$nc_prefix/lib;$nccl_prefix/lib" \
  -DCMAKE_INSTALL_RPATH="$nf_prefix/lib;$nc_prefix/lib;$nccl_prefix/lib"
cmake --build "$BUILD" --target HICAR HICAR-tester \
  --parallel "${SLURM_CPUS_PER_TASK:-8}"

test -x "$BUILD/HICAR_gpu"
ldd "$BUILD/HICAR_gpu" | grep -q libcufft
ldd "$BUILD/HICAR_gpu" | grep -q libnccl
! ldd "$BUILD/HICAR_gpu" | grep -q 'not found'
test "$(grep '^NCCL:BOOL=' "$BUILD/CMakeCache.txt")" = "NCCL:BOOL=ON"
sha256sum "$BUILD/HICAR_gpu"
```

Before accepting any GPU build, run a small case using the same number of
nodes, rank/GPU visibility, I/O-rank layout, and MPICH GPU-support setting as
production. A successful `--help` call does not exercise device transport.

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

Use `NCCL=OFF` for the single-node four-A100 GPU-aware-MPI workflow. Use
`NCCL=ON` for the validated Switzerland-wide multi-node topology with four
compute ranks and one CPU-only I/O rank per node, uniform
`MPICH_GPU_SUPPORT_ENABLED=0`, and rank-local GPU visibility. The canonical
builder publishes `hicar_build_provenance.txt.ready`; do not launch a campaign
from an unreported build directory.

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

## National 200 m runtime profile

Winter Sx/TPI-off campaign segment job `5098113` provides the first measured
full-domain performance profile for the selected scientific stack. It advanced
12 simulated hours on 12 A100 nodes with 48 compute ranks plus 12 CPU-only I/O
ranks, RRTMG and output both at 600 s cadence. HICAR completed the integration
in 4491.221 s, or 9.62 simulated hours per wall-clock hour. The enclosing batch
lasted 01:18:28, or 9.18 simulated hours per wall-clock hour; its nonzero final
status came only from the subsequently corrected missing legacy restart-
provenance check and does not invalidate the model timing.

| Timed component | Mean seconds | Share of HICAR total |
|---|---:|---:|
| RRTMG radiation | 3413.264 | 76.0% |
| Advection | 658.906 | 14.7% |
| Halo wait | 135.371 | 3.0% |
| Microphysics | 102.172 | 2.3% |
| PBL | 48.807 | 1.1% |
| Output | 33.613 | 0.75% |
| LSM | 19.969 | 0.44% |
| Input | 18.671 | 0.42% |
| Wind balance | 7.076 | 0.16% |

Radiation, not forcing I/O or the variational wind solve, is therefore the
primary runtime target. Do not alter the locked 600 s radiation cadence during
the four-event scientific comparison. If the selected physics is retained
after evaluation, profile and optimize the RRTMG implementation first; input
streaming and forcing generation remain throughput concerns for long
production, but they are not the dominant cost inside HICAR.

## CPU reference

The frozen 250 m one-hour CPU release run completed in about 18 seconds versus about 99 seconds for debug on the tested setup. Release differed slightly from debug because of aggressive optimization but remained finite and physically comparable. Use debug for diagnosis, not throughput estimates.
