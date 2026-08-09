---
name: balfrin-user-environment
description: Initialize the MeteoSwiss user environment and choose safe Slurm execution settings on Balfrin or related CSCS nodes. Use for modules, compilers, Python, ecCodes, NetCDF, partitions, SSH access, scratch paths, or deciding where cluster work may run.
---

# Balfrin user environment

## Initialize modules

In every non-interactive SSH or Slurm shell:

```bash
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"
```

Then load only the modules required by the task. Current environment root is
normally `/mch-environment/v8`; verify live state instead of hard-coding it.

Useful families include Python 3.11, GCC 12.3, NVHPC 24.5, Cray MPICH 8.1.30,
CMake 3.24, gmake 4.4, ecCodes 2.36, NCO 5.0, NetCDF, HDF5, and FFTW.

## Project locations

```text
Local workspace: current coordinator checkout
Balfrin root:    $SCRATCH/icon_hicar
Durable root:    /store_new/mch/msopr/olifu/icon_downscaling
```

Use `/tmp` only for small transient payloads. Keep large data and builds in
scratch. Never store secrets in the workspace or scratch manifests.

Use `/store_new/mch/msopr/olifu` for project-owned, longer-term online
storage outside scratch. It is the authoritative namespace for this project;
do not rewrite durable locators to another storage prefix. Keep versioned
manifests below the durable project root and retain checksum-bound ready
markers when payloads move within that namespace.

## Slurm policy

- GPU debugging: `debug`.
- Short GPU development/CI/benchmarks: `short`.
- Longer GPU runs: `normal`.
- Cancellation-tolerant opportunistic GPU work: `preemptible`.
- Non-pre-empting priority-zero overflow: `lowprio`.
- Short CPU/post-processing: `pp-short`.
- Longer CPU/post-processing: `pp-long`.
- Submit only to reviewed partitions whose live `AllowGroups` contains
  `ALL` or exact group `s83`. Membership in `s83opr`, `s83disp`, or another
  supplemental group never authorizes a project submission.
- Do not run CPU-only work on GPU nodes.
- Do not run compute-intensive work on login nodes.
- Do not use `balfrin-ln001`; it is reserved for operations.
- Balfrin normally does not require `--account`.

Before substantial work, inspect partition limits and current
cluster/operations state. Use exclusive allocation when exact NUMA/GPU binding
is required.

Balfrin `preemptible` uses cancellation pre-emption: the job receives
`SIGTERM` and about 60 seconds of grace, then is killed. `lowprio` has
pre-emption disabled and may instead wait behind higher-priority work. A
global `JobRequeue=1` setting is not application resume. Submit pre-emptible
HICAR attempts with `--no-requeue`, immutable attempt directories, an
external reconciler, and a non-success exit unless a validator-published
completion marker already exists. Signal-time checkpoint publication is best
effort only; hard-kill recovery must use the last previously published,
checksum-bound restart.

Capacity limits are global, not per chain. Against the 46-node `preemptible`
partition, four-node
200 m attempts permit at most 11 concurrent independent chains; provisional
16-node 100 m attempts permit at most two. Keep campaign CPU work in one
global `pp-short` array, normally capped at eight active jobs rather than
multiplying producers by the number of model chains. The CPU nodes are
shared, and Balfrin does not enforce job memory limits: reserve at least four
CPUs per task as the memory-placement proxy. The measured campaign-worker
peak for the older small case was about 3.8 GB, but national 2061x1431
hicarprep workers reached 53--59 GB each. The current six-worker national cap
therefore represents roughly 360 GB before auxiliary jobs on the
256-CPU/456704-MB nodes. Treat `--mem` as documentation, not protection, and
recheck measured RSS before increasing that cap or co-locating other
memory-heavy work.
NetCDF assessment code must bound simultaneously open datasets: the wind
spin-up assessor exceeded 32 GB when it retained all 156 HDF5-backed files,
but stabilized near 2 GB with one candidate and one reference open at a time.
Eight CPUs are therefore a conservative shared-node reservation for that
bounded assessor; do not compensate for an unbounded file cache by requesting
more cores.
Retry publication-safe CPU failures up to three times with progressively
larger reservations of 4, 8, then 16 CPUs. Pre-emptible campaign plans must derive this slot
count from the node budget and per-attempt node count, use a shared
valid-time forcing cache, and interleave input with lifecycle tasks so idle
GPU slots are not caused by CPU-pipeline starvation.

For multi-gigabyte forcing/LBC migrations on the parallel filesystem, avoid
Python `shutil.copyfile`'s Linux fast-copy path. It reproducibly produced a
same-sized but checksum-different copy for one 1.46 GB sparse-LBC file while
a buffered read/write copy was byte-identical and the source checksum and
metadata remained stable. Copy through a bounded user-space buffer, flush and
fsync, then require source/copy SHA-256 equality before modifying or publishing
the copy.

For long, expensive, shared, or pre-emptible campaigns, use a checksum-bound
runtime, a bounded controller, `make balfrin-preflight CHECK_FDB=1`, and the
published Python environment so failures are recoverable and data lifecycle is
safe. For a small exploratory job, a recorded source commit, executable path,
module stack, configuration delta, and case are normally sufficient. In both
modes, wrappers must load `config/balfrin.env` through
`scripts/load_balfrin_site_config.sh`, large data stays in scratch, and outputs
or restarts must not be deleted while a live job or unique scientific result
depends on them.

## SSH behavior

Use `ssh balfrin`. If a connection is refused or closes unexpectedly, wait
one to two minutes and retry. Stop only if that retry also fails, then ask the
user to restore passwordless access; do not invent SSH workarounds.

## Operational discipline

- Put module initialization inside scripts and Slurm jobs, not only
  interactive shell startup.
- `sbatch` executes a spool copy of the submitted script, so `$0` does not
  identify the checkout inside a job. Never derive repository state from
  `$0` in a Slurm wrapper. Export and require the appropriate immutable
  identity: `REPO_ROOT` for campaign jobs, `HICAR_RUNTIME_RELEASE` for Python
  bootstrap, or `HICAR_COORDINATOR_ROOT` for the canonical builder.
- Verify tool versions and paths in the actual job environment.
- Prefer project scripts and reproducible build directories over ad hoc
  login-node commands.
- Use atomic output writes, resumable manifests, and validation markers for
  long workflows.
- Query current Confluence/Rovo guidance when behavior depends on operational
  Balfrin configuration.

## End-of-investigation cleanup

Treat cleanup as a deliberate, recoverable operation, not an ad hoc recursive
delete. The full archival workflow is appropriate for unique or costly
evidence; lightweight experiments need only preserve the evidence that changes
future scientific decisions:

1. List every active/held job with `squeue` and inspect dependencies with
   `scontrol show job`. Cancel obsolete held or dependency-impossible jobs
   before removing their scripts or working directories.
2. Prove every local-only HICAR commit is reachable from an intentionally
   named remote ref or a checksum-verified Git bundle under the durable root.
   Preserve dirty work as a binary patch and verify its SHA-256 and apply
   check before restoring the checkout.
3. Archive the selected scientific history, configuration, logs, and
   canonical reports with `scripts/archive_recovery_plan.py`. Use an explicit
   source-controlled plan, publish through a partial name, hash the source and
   copy, create data/report ready markers last, and independently read back
   the manifest.
4. Retain only inputs whose regeneration is expensive and the smallest
   trajectory needed for the next bounded diagnosis. Build trees, duplicate
   source clones, superseded restart payloads, raw failed experiments, and
   transient logs are regenerable scratch.
5. Dry-run exact top-level targets. Resolve paths under
   `$SCRATCH/icon_hicar`, reject anything outside that root, and never use an
   unresolved glob or the scratch root itself as a deletion target. Remove
   experiment clones and build directories only when no live job references
   them.
6. Re-list jobs, retained paths, Git refs, and durable manifests after
   deletion. Record counts and retained exceptions in a compact cleanup record;
   update `memory/project-assessment.md` only if the cleanup changes current
   capabilities, priorities, or blockers.

For a failed seasonal experiment, preserve the diagnostic evidence needed for
future decisions before deleting large restart payloads. A passed
checkpoint-inventory report is sufficient to record what existed; it is not a
reason to retain every restart when no useful continuation is planned.
