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
- Short CPU/post-processing: `pp-short`.
- Longer CPU/post-processing: `pp-long`.
- Do not run CPU-only work on GPU nodes.
- Do not run compute-intensive work on login nodes.
- Do not use `balfrin-ln001`; it is reserved for operations.
- Balfrin normally does not require `--account`.

Before substantial work, inspect partition limits and current
cluster/operations state. Use exclusive allocation when exact NUMA/GPU binding
is required.

## SSH behavior

Use `ssh balfrin`. If a connection is refused or closes unexpectedly, retry up
to two times with a short pause. If passwordless access still fails, stop and
ask the user to restore the signed key; do not invent SSH workarounds.

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
