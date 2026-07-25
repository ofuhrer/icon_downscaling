---
name: hicar-balfrin-runtime
description: Build, test, run, benchmark, or debug HICAR on MeteoSwiss Balfrin with CPU or NVHPC/OpenACC GPUs. Use for CMake/module setup, Slurm scripts, MPI/NCCL choice, rank-to-GPU binding, ready-file execution, runtime failures, halo tests, or selecting debug versus release executables.
---

# HICAR on Balfrin

Use `$balfrin-user-environment` for generic module and partition rules. Keep source under `$SCRATCH/icon_hicar/HICAR` or an isolated validation worktree; never run compute-heavy model work on login nodes.

## Current source

- Local clone: `/Users/fuhrer/Work/agentic/icon_hicar/HICAR`
- Remote: `git@github.com:ofuhrer/HICAR.git`
- Production-performance branch: `feature/icon_downscaling`
- National discretely-adjoint qualification branch:
  `feature/wind-galerkin-multilevel`; qualified commit
  `16bdb27b` (the numerical solver change is
  commit `cd3089c25dad909610055d6bf9e665ed66d7202d`).
- Retired solver-research and micro-optimization history is preserved under
  the coordinating workspace's `archives/` directory; it is not an active
  production branch.
- Validated transport baseline: `06ba6b54` (`Fix OpenACC halo exchanges`)

The branch uses legacy `use mpi` for compatibility with Balfrin `cray-mpich-nvhpc/8.1.30`, fixes NVHPC virtual dispatch during variable initialization, and has correct four-GPU halo exchange with both GPU-aware MPI and NCCL.

## Choose an executable

- CPU production/long run: `HICAR_release`.
- CPU diagnosis: `HICAR_debug`.
- Single-node four-A100 GPU default: OpenACC with `NCCL=OFF` and GPU-aware Cray MPICH.
- Use `NCCL=ON` only for comparison, multi-node experiments, or targeted NCCL work. On the measured single node, MPI was faster in every halo case.

## GPU runtime requirements

Load the site-consistent NVHPC/Cray MPICH/NetCDF stack. Set:

For a four-compute-rank GPU-aware-MPI job without an I/O server, use:

```bash
export MPICH_GPU_SUPPORT_ENABLED=1
export CUDA_VISIBLE_DEVICES="$SLURM_LOCALID"
```

Bind each local rank to its matching NUMA node on an exclusive GPU allocation. Do not use `CUDA_LAUNCH_BLOCKING` or `NVCOMPILER_ACC_SYNCHRONOUS` for performance runs; enable them only for debugging.

### Dedicated I/O rank on four GPUs (validated NCCL topology)

HICAR's I/O path is host-staged: compute ranks pack on device, wait, copy the
write buffers to host, then send them to host receive buffers on the I/O rank.
The I/O server therefore does **not** need a CUDA context or GPU allocation.
`driver.F90` guards OpenACC shutdown to compute ranks for the same reason.

For the `NCCL=ON` executable on a one-node, four-GPU allocation, launch five
ranks: four compute ranks and one I/O rank. Before `MPI_Init`, give each compute
rank exactly one visible GPU and hide all GPUs from the I/O rank:

```text
compute local ranks 0,1,2,3: CUDA_VISIBLE_DEVICES=0,1,2,3 respectively
I/O local rank 4:            CUDA_VISIBLE_DEVICES=
all five ranks:              MPICH_GPU_SUPPORT_ENABLED=0
                             MPICH_GPU_MANAGED_MEMORY_SUPPORT_ENABLED=0
```

Use an MPMD `srun --multi-prog` map or equivalent rank-local launcher so these
variables are set before the executable starts. Do **not** mix
`MPICH_GPU_SUPPORT_ENABLED=1` on compute ranks with `0` on a CUDA-hidden I/O
rank: on Balfrin's `cray-mpich-nvhpc/8.1.30`, that combination deadlocks in
`MPI_Init` before HICAR prints its startup banner. The NCCL executable owns the
device halo transport, so disabling MPICH GPU support is correct in this mode.

Job `4833066` validated this topology with a five-minute model case: 13 s wall
time, 4.55 s model initialization, a 2x2 compute decomposition, and one output
NetCDF file. Its four compute ranks initialized OpenACC; the I/O rank did not.

For `NCCL=OFF`, GPU-aware MPI remains the preferred measured four-compute-rank
transport. A CPU-only dedicated-I/O topology with that transport is not yet
validated; keep MPI GPU-support settings uniform and validate a small case
before using it.

### Balfrin NUMA and CPU pinning (measured)

The tested A100 nodes have one 128-logical-CPU AMD EPYC socket divided into
four NUMA domains; they are not two-socket nodes. Each NUMA domain contains
32 logical CPUs (16 physical cores plus SMT):

| GPU | GPU-local NUMA domain | CPU affinity |
|---|---:|---|
| GPU 0 | 3 | `48-63,112-127` |
| GPU 1 | 2 | `32-47,96-111` |
| GPU 2 | 1 | `16-31,80-95` |
| GPU 3 | 0 | `0-15,64-79` |

For the validated one-thread-per-rank configuration, pin local compute ranks
to a CPU in their GPU's NUMA domain: local rank 0/GPU 0 to CPU 48, rank 1/GPU
1 to CPU 32, rank 2/GPU 2 to CPU 16, and rank 3/GPU 3 to CPU 0. Pin the local
I/O rank 4 to CPU 1 on NUMA 0; it shares that NUMA domain with GPU 3 but does
not need GPU locality. Use `numactl --physcpubind=<cpu> --membind=<numa>` (or
an equivalent Slurm CPU mask) before launching HICAR.

For more CPU threads per compute rank, preserve this one-compute-rank-per-NUMA
layout rather than assigning two compute ranks per socket. Reserve a few CPUs
in NUMA 0 for the I/O rank and divide the remaining CPUs within each compute
rank's local NUMA domain. Use `--hint=nomultithread` when physical-core-only
placement is desired; size `--cpus-per-task` so five equal Slurm tasks still
fit on the node, or use a wrapper with unequal CPU masks.

### Multi-node launch layout (validated NCCL topology)

Request five tasks and four GPUs per node, and force block rank distribution:

```bash
#SBATCH --ntasks-per-node=5
#SBATCH --gres=gpu:4
srun --distribution=block:block --cpu-bind=none \
  rank_pin_wrapper.sh "$HICAR_GPU" "$NML"
```

The wrapper must derive the GPU's NUMA node from its PCI address, set
`CUDA_VISIBLE_DEVICES=$SLURM_LOCALID` for local IDs 0-3, hide CUDA for local ID
4, apply the local CPU/memory binding, and then `exec` HICAR. With block
distribution, HICAR assigns global ranks `4, 9, 14, ...` as the per-node I/O
servers and all other ranks as compute ranks. Job `4833095` validated two
nodes: 10 total ranks, 8 compute ranks in a 2x4 decomposition, two CPU-only
I/O ranks, multi-node NCCL initialization, and a successful five-minute model
run.

For the current Switzerland-wide 200 m domain, use at least four nodes (16
compute GPUs plus four CPU-only I/O ranks) in a 4x4 compute decomposition.
The two-node 4x2 decomposition overflows a signed `MPI_REAL` halo-message
count during later physics. Treat a solver status of 0 and a relative
physical-wind residual of `<=1e-5` as an additional release gate.

The qualified six-hour Switzerland 200 m run used four `normal` nodes, 16
compute GPUs, and four CPU-only I/O ranks. Measured batch wall time was 1,411
s (1,360 s reported by HICAR), peak task RSS was 33.62 GiB, and two full
engineering-state records occupied 20.24 GiB. Use 45 minutes, 40 GiB peak
task RSS, and 30 GiB output as the current six-hour 200 m acceptance budgets.
For an initial 100 m capacity allocation, estimate 16 nodes/64 GPUs, a
two-hour request, and about 81 GiB for two full records; these remain planning
figures until the capacity gate passes. The actual national 100 m 80-level
SLEVE geometry already passes its independent Jacobian/thickness/spacing
screen.

For the Switzerland-wide 200 m `wind_climatology` profile, commit
`2999c9bd` passed two restart-linked one-hour segments on the same four-node
layout. Cold-start and continuation batch walls were 9:29 and 10:05
(`417.54 s` and `486.96 s` inside HICAR). Three cold-start records occupied
1.111 GB, two new continuation records 750.8 MB, and each 136-variable
rolling restart 42.37 GB. Compact hourly sufficient-statistic products were
about 677 MB each; their exact four-sample merge was 682.7 MB. Treat these as
engineering throughput/storage measurements, not observational or
climatological qualification.

For REA-L climate production, use seven-day restart segments. The earlier
short-run 200 m fit was 8.8 hours per segment, but the completed national
72-hour summer qualification measured 16,421.08 model seconds and updates the
provisional long-run envelope to about 10.64 hours per seven days on four
nodes. Capacity scaling therefore puts the provisional 100 m base near 21.29
hours on 16 nodes, with a planning range of 15.96--31.93 hours. Live Balfrin
inspection shows `normal` has a 24-hour maximum; the 200 m case fits
comfortably, while 100 m has insufficient margin and can exceed the limit.
Revise this envelope with the winter and live 100 m measurements. Keep seven
days only if the live 100 m benchmark leaves operational wall-time margin;
otherwise shorten the segment and recompute the restart/output/storage
envelope.
Do not quote an unconstrained all-years-parallel calendar: twenty independent
chains would require 80 nodes at 200 m or 320 nodes at 100 m, beyond the
46-node `normal` partition. The summer-calibrated theoretical
exclusive-capacity lower bounds are eleven 200 m chains in two waves
(37.0--69.4 days) and two 100 m chains in ten waves (347--694 days).
Queueing, failures, maintenance, shared
production demand, and archive transfer make real elapsed time longer.
Set `restartinterval` to the number of hourly output intervals in the segment
and validate a restart at the exact end. Consecutive segments share a restart
directory and run sequentially; parallelize across independent chains only
after land/snow initialization is qualified. HICAR's default
`frames_per_outfile=24` creates daily output files, so segment validators must
accept a monotonic collection rather than assume one file. HICAR output is
not NetCDF-deflated; compress daily files on `pp-short`, verify lossless
logical hashes, and publish compressed copies before retiring raw output.
The national 200 m restart is 42.37 GB (39.46 GiB). Keep two rolling
boundaries and explicitly selected periodic checkpoints; after the successor
segment passes, verify both completion manifests and hashes before removing
the superseded restart. Never retain every seven-day boundary.

For a two-dimensional-only routine history profile, require commit
`16bdb27b` or later. `cef7e3d6` stops restart-only three-dimensional state
from forcing the static 80-level `z` field into every history file, and
`16bdb27b` permits a valid zero-count 3-D output buffer. Validate that routine
files contain the requested 2-D diagnostics plus lat/lon/time and do not
contain `z`.

## Validation sequence

1. Configure and build `HICAR` and `HICAR-tester` explicitly.
2. Run `HICAR-tester -v halo_exch` on four ranks with the production GPU mapping.
3. Run a 5-minute debug/readiness smoke case.
4. Run a 1-hour release case and inspect timers plus physical output.
5. Compare CPU/GPU or transport variants with identical inputs, placement, warm-up, and synchronization boundaries.
6. Scale duration/domain only after correctness and physicality pass.

## Ready-file execution

- Set `wait_for_ready_file=.True.` independently in `&domain` and `&forcing` when producers may still be writing.
- List all planned forcing paths before launch.
- Quote every pathname in a forcing-list manifest (for example,
  `"/scratch/.../rea_l_hicar_20100101_0000.nc"`): HICAR reads the list with
  formatted list-directed input, for which an unquoted slash-containing path
  can be parsed as an invalid filename. Verify both the quoted manifest and
  the corresponding unquoted paths used by shell preflight checks.
- Use `<data-file>.ready`; do not use obsolete `.done` markers.
- A derived data payload and its checksum-bearing JSON report are separate
  publications. Validate and atomically rename both, create the data ready
  marker, then create the report ready marker last; retirement consumers must
  require both.
- Before launching HICAR, have the stream runner invoke the selected model
  validator's `--help` and require every CLI option it will use, including the
  production-provenance and restart-continuation options. A stale validator
  must fail during preflight, not after the model has completed.
- Immutable runtime-script snapshots may deliberately use mode `0444`.
  Wrapper jobs must invoke a pinned shell script through its interpreter
  (for example, `exec bash "$runner"`) rather than requiring its executable
  bit. Cover the read-only invocation path with a behavior test; a string-only
  script check does not prevent a pre-model exit 126.
- Set a finite timeout and fail loudly on missing producers.
- Forcing must include the first record at or after `end_time` for interpolation; when `end_time` is exactly a forcing timestamp, that terminal record is sufficient and no additional record is required.
- When HICAR restart-continuation timestamps have a sub-second encoding
  offset, never prepend an exact boundary and then test raw gap equality.
  With an explicit interval start, infer the intended cadence, require every
  raw timestamp to be within one second of it, construct the exact canonical
  axis, and only then form interval groups. Keep strict regularity checks when
  no explicit boundary is available.

## Debugging order

1. Confirm branch/commit, executable, module stack, and input manifest.
2. Confirm Slurm rank count, GPU visibility, NUMA mapping, and `MPICH_GPU_SUPPORT_ENABLED`.
3. Reproduce with the smallest relevant unit test.
4. Distinguish initialization, transport, unpack, and numerical failures with rank-stamped checkpoints.
5. Use debug synchronization and GDB only after the production mapping reproduces the issue.
6. Re-run the full local CPU and four-GPU halo suites after a fix.

For a delayed `CUDA_ERROR_LAUNCH_FAILED`, do not accept a short smoke as the
final gate. First localize the last entered physics routine and the failing
rank/GPU, check whether the node was drained or immediately reused, and
separate evidence for a deterministic kernel defect from a transient device
failure. Treat dynamically private runtime-sized vertical arrays inside an
OpenACC column kernel as a high-risk pattern: when the calculation is
level-local, use a scalar `!$acc routine seq` over a collapsed 3-D loop and a
separate bounded column reduction. Preserve the column API for other callers
and test scalar/column equivalence.

Validate such a repair in this order: local full tests, target-stack build,
four-GPU unit/halo suite, production-physics smoke, parent-versus-fix output
comparison, hard regional bridge, then a national run that exceeds the
original failure time. Pin the expected source commit. For a long recovery
run, write explicitly selected periodic checkpoints whose interval divides
the output-record count; retain only enough boundaries to recover from a late
failure and validate the exact-end restart before retirement. Treat an
intermediate checkpoint as closed only after the model advances beyond its
timestamp. Validate it on `pp-short`, not the login node: require its exact
canonical time (allowing at most one second of NetCDF encoding offset), the
pinned source revision, positive `dt_seconds`, the qualified horizontal and
80/81-level dimensions, essential atmospheric and land-state variables, and
a whole-file checksum. The project wrapper
`validate_restart_checkpoint_balfrin.sbatch` publishes this bounded inventory
with a hash-bearing ready marker. Its schema/header/checksum pass is a
recoverability inventory, not a full finite-value scan of every restart
variable.

Read [references/build-and-performance.md](references/build-and-performance.md) for module sets, CMake templates, and measured transport results.
