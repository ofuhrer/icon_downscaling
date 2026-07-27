---
name: balfrin-user-environment
description: Initialize the MeteoSwiss user environment and choose safe Slurm execution settings on Balfrin or related CSCS nodes. Use for modules, compilers, Python, ecCodes, NetCDF, fieldextra dependencies, partitions, SSH access, scratch paths, or deciding where cluster work may run.
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
Local workspace: /Users/fuhrer/Work/agentic/icon_hicar
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

Capacity limits are global, not per chain. Against 44 GPU nodes, four-node
200 m attempts permit at most 11 concurrent independent chains; provisional
16-node 100 m attempts permit at most two. Keep `pp-short` work in one global
array capped at two active jobs rather than multiplying producers by the
number of model chains.

Run campaigns only from a checksum-bound immutable runtime release and a
separately published, read-only Python environment tied uniquely to that
release. Run `make balfrin-preflight CHECK_FDB=1` from a clean checkout before
building a first campaign; the ready report must confirm the production HICAR
pin, shared tools, `preemptible` partition, FDB view, scratch, and
`/store_new` access. Keep
compression and journaled retirement ahead of further prefetch; cap the
number of completed-but-unretired segments per chain. Retire forcing/raw
output only after compression and solver publications pass, and retire a
restart only after its adjacent successor passes. Preserve periodic and final
checkpoints. The controlled SIGTERM/SIGKILL probe under `orchestration/`
qualifies scheduler/controller recovery only; its report must remain
non-promoting and cannot qualify HICAR science.

## SSH behavior

Use `ssh balfrin`. If a connection is refused or closes unexpectedly, wait
one to two minutes and retry. Stop only if that retry also fails, then ask the
user to restore passwordless access; do not invent SSH workarounds.

## Operational discipline

- Put module initialization inside scripts and Slurm jobs, not only
  interactive shell startup.
- Verify tool versions and paths in the actual job environment.
- Prefer project scripts and reproducible build directories over ad hoc
  login-node commands.
- Use atomic output writes, resumable manifests, and validation markers for
  long workflows.
- Query current Confluence/Rovo guidance when behavior depends on operational
  Balfrin configuration.

## End-of-investigation cleanup

Treat cleanup as a publication workflow, not an ad hoc recursive delete:

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
   deletion. Record counts and retained exceptions in `memory/project-state.md`
   or a compact cleanup record, not a command transcript.

For a failed seasonal gate, preserve its diagnostic history before deleting
large restart payloads. A passed checkpoint-inventory report is sufficient to
record what existed; it is not a reason to retain every restart when no
continuation is authorized.
