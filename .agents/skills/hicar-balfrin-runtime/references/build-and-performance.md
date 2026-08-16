# Build and performance reference

## Build invariants

- Compile on `pp-short` or `pp-long`; GPU allocation is unnecessary.
- Use a fresh build directory per source commit and CPU/GPU/transport variant.
  Never retarget a CMake cache or toggle OpenACC/NCCL in place.
- Never run two builds concurrently against the same source clone. The tester
  mutates `tests/Test_Cases`; the canonical builder locks beside the source.
- Pass NetCDF, FFTW, MPI, CUDA, NCCL, and BLAS/LAPACK paths explicitly from the
  loaded module stack. CPU uses GCC/OpenBLAS; GPU uses NVHPC's libraries.
- GPU C/C++ compilers are `nvc`/`nvc++`; Fortran/MPI uses Cray `mpifort`.
- A compile is insufficient: require clean source identity, correct CMake
  cache, complete `ldd`, executable/tester SHA-256, and a topology-matched
  smoke.
- RTE-RRTMGP v1.9.3 requires both `KERNEL_MODE=accel` and
  `RTE_KERNEL_MODE=accel`; verify accelerated sources compiled. Its ice API is
  `get_min_diameter_ice()`/`get_max_diameter_ice()`.

## Canonical builder

Use `case_studies/swiss_200m/scripts/build_hicar_balfrin.sbatch`. It rejects a
dirty/wrongly pinned source, existing build directory, or concurrent build;
checks linkage; and writes `hicar_build_provenance.txt` plus its ready marker.

```bash
sbatch --export=ALL,\
HICAR_COORDINATOR_ROOT="$PWD",\
HICAR_SOURCE_ROOT="$PWD/HICAR",\
HICAR_BUILD_ROOT="$SCRATCH/icon_hicar/build/HICAR-<commit>-<variant>",\
HICAR_EXPECTED_COMMIT=<full-commit>,\
HICAR_BUILD_VARIANT=cpu \
case_studies/swiss_200m/scripts/build_hicar_balfrin.sbatch
```

Choose exactly one:

| Variant | Compiler/transport | Required runtime |
| --- | --- | --- |
| `HICAR_BUILD_VARIANT=cpu` | GCC, Cray MPICH, OpenBLAS, no OpenACC/NCCL | CPU reference/debug |
| `HICAR_BUILD_VARIANT=gpu-mpi` | NVHPC OpenACC, GPU-aware MPI, NCCL off | Four compute ranks, `MPICH_GPU_SUPPORT_ENABLED=1` |
| `HICAR_BUILD_VARIANT=gpu-nccl` | NVHPC OpenACC + NCCL | Four GPU ranks plus CPU I/O rank/node, `MPICH_GPU_SUPPORT_ENABLED=0` |

The script is the authoritative expanded CMake recipe; inspect it rather than
copying historical commands. For qualification record source commit/diff hash,
module list, configure command, executable/tester hashes, and `ldd`.

## Launch and topology

For multi-node NCCL, use five ranks/node: local ranks 0--3 each see one GPU;
local rank 4 is CPU-only I/O and sees no GPU. Keep MPICH GPU support disabled
uniformly. The maintained PCI-derived wrapper binds GPU ordinals 0/1/2/3 to
Balfrin NUMA domains 3/2/1/0; bind the I/O rank to a spare core on NUMA 0.

Before acceptance, run the exact node/rank/I/O/GPU-visibility layout. Check
every compute rank initializes OpenACC, I/O does not, NCCL initializes across
nodes, output closes successfully, and no device pointer reaches an MPI window.

## Performance and radiation

Use release builds for timing. A small 250 m CPU case measured about 18 s in
release versus 99 s debug; compare physics only within a build mode.

The completed 12-node CPU-RRTMG campaign spent a median 75.7% of HICAR time in
radiation. The legacy wrapper transferred the full domain and ran serial
`ncol=1` SW/LW columns; Nsight samples put longwave/McICA ahead of repeated
initialization. Exact campaign interpretation lives in
`memory/project-assessment.md`.

For radiation implementation work use the validated 495 x 495 x 80 one-node
crop: 61,256 cells/GPU versus 61,450 nationally, with RRTMGP radiation time
within 5%. It is not a whole-model proxy because timestep and communication
costs differ.

Qualified GPU RTE-RRTMGP v1.9.3 on production `0b9b0cb6` requires:

- both accelerated-kernel CMake selectors;
- Noah-MP persistent energy-workspace reset before transfer;
- NVHPC OpenACC selection of the sequential tropopause `minmaxloc` helper with
  explicit intents;
- compiler feedback retaining the outer vector(128) column loop while removing
  device `MINLOC`/`MAXLOC` inner loops and scalar-live-out warnings;
- one-node continuous/restart, two cold full-Swiss replicas, and full-Swiss
  continuous/restarted gates.

Those gates were bitwise identical at 13 outputs and 199 join/endpoint
variables. Full-domain radiation was 4.875--4.881 s per two hours. The 4.06x
model/3.56x wall estimate holds non-radiation work and 600 s cadence fixed; it
is not a measured 12-hour run. Cadence changes are separate sensitivities.
