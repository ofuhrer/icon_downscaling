---
name: balfrin-user-environment
description: Initialize MeteoSwiss Balfrin and choose safe modules, partitions, storage, SSH, and cleanup behavior.
---

# Balfrin user environment

## Shell and locations

Every non-interactive SSH/Slurm shell must initialize modules:

```bash
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
```

Load only required modules and verify versions inside the job. Wrappers load
`config/balfrin.env` through `scripts/load_balfrin_site_config.sh`.

```text
Scratch: /scratch/mch/olifu/icon_hicar ($SCRATCH/icon_hicar)
Durable: /store_new/mch/msopr/olifu/icon_downscaling
```

Large builds/data belong in scratch; `/tmp` is for small transient files.
Project-owned durable data must remain below `/store_new/mch/msopr/olifu`.
Never store secrets.

## Slurm policy

| Work | Partition |
| --- | --- |
| GPU debug / short development | `debug` / `short` |
| Longer GPU | `normal` |
| Cancellation-tolerant GPU | `preemptible` |
| Non-pre-empting overflow | `lowprio` |
| CPU/post-processing | `pp-short` / `pp-long` |

Before every programmatic submission, name one partition and require live
`AllowGroups=ALL` or exact group `s83`; supplemental groups never authorize
submission. Never compute on login nodes or `balfrin-ln001`, and never use GPU
nodes for CPU-only work. Balfrin normally needs no `--account`.

`preemptible` cancels with about 60 s grace; Slurm requeue is not application
resume. Use `--no-requeue`, immutable attempt directories, a bounded external
controller, and the last checksum-published completed restart. Signal-time
publication is best effort. Use exclusive nodes for exact NUMA/GPU binding.

National hicarprep is memory-bound: measured eight-worker records reached
about 240 GB, so use an exclusive 456 GB CPU node and do not infer protection
from `--mem`. Bound NetCDF readers to one candidate/reference pair. Retry
publication-safe CPU failures at most three times with 4, 8, then 16 CPUs.

For multi-GB forcing copies, avoid Python's Linux fast-copy path: use a bounded
user-space buffer, flush/fsync, and require source/copy SHA-256 equality before
publication.

## Operations

- Use `ssh balfrin`. Retry a transient refusal once after one to two minutes;
  then ask the user to restore passwordless access.
- `sbatch` runs a spool copy, so `$0` is not the checkout. Require an explicit
  `REPO_ROOT`, `HICAR_RUNTIME_RELEASE`, or `HICAR_COORDINATOR_ROOT` as relevant.
- Prefer project scripts. Query Confluence/Rovo when behavior depends on live
  operational configuration.
- Small experiments need source/executable/module/config/case provenance.
  Long, expensive, shared, or preemptible campaigns additionally need a
  bounded controller, checksum-bound runtime, and preflight validation.
- Do not delete output/restarts while a live job or unique result depends on
  them. Publish shared artifacts atomically and create ready markers last.

## Cleanup

1. Audit jobs, dependencies, controllers, Git reachability, and retained
   evidence; cancel only obsolete jobs.
2. Preserve unique source in a remote ref or verified Git bundle and preserve
   only evidence that changes future decisions.
3. Retain expensive inputs only when a named next experiment needs them;
   builds, clones, failed runs, duplicate restarts, and transient logs are
   scratch.
4. Dry-run explicit targets below `$SCRATCH/icon_hicar`; never delete the root,
   use unresolved globs, or remove anything referenced by a live job.
5. Delete through `pp-short`/`pp-long`, then re-audit jobs, paths, refs, and
   durable manifests. Update project assessment only if capability changes.
