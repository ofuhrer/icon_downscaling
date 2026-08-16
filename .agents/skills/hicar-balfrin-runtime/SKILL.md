---
name: hicar-balfrin-runtime
description: Build, test, run, benchmark, or debug HICAR on Balfrin with CPU or NVHPC/OpenACC GPUs.
---

# HICAR on Balfrin

Use with `balfrin-user-environment`.

## Build

Use `case_studies/swiss_200m/scripts/build_hicar_balfrin.sbatch`; detailed
invariants and variants are in `references/build-and-performance.md`. Use a
fresh release build for throughput and a separate debug build for diagnosis.
After source changes, run HICAR tests and a small topology-matched pilot before
scientific work. Reproduce normally before adding synchronization or GDB.

## Runtime

GPU production uses NVHPC/OpenACC, Cray MPICH, four compute GPUs plus one
CPU-only I/O rank per node, NCCL, and `MPICH_GPU_SUPPORT_ENABLED=0`. Populate
the ignored `HICAR/run` tree with checksum-identical `NoahmpTable.TBL`,
`rrtmg_support`, `rrtmgp_support`, and `mp_support`; preflight must fail before
submission if any runtime asset is absent.

Provide a valid-time domain, continuous hourly hicarprep regular forcing, and
an existing predecessor restart for continuation. Sparse LBC is experimental,
not part of the reference. Initialize/project once at chain start; restarts
restore the full state.

Ordinary restart comparison is fail-closed. A preregistered one-option causal
sensitivity may set `HICAR_RESTART_OVERRIDE_CHECK=1`, record the exact mismatch,
and change nothing else. `HICAR_ALLOW_MISSING_RESTART_DOMAIN_PROVENANCE=1` is
only for immutable `4a425677` campaign restarts missing the independently
verified 20 m attribute; production `0b9b0cb6` and later must not use it.

Success requires the completion message, nonempty expected-time output, and
terminal restart; scientific validation also checks residuals, extrema,
continuity, and comparison metrics. Never inspect a NetCDF file while HICAR is
writing it: use `model.out` for live progress and closed files for field checks.

## Segmented campaigns

Use `orchestration/rd_campaign.py`. Each attempt has a new directory and
creates `segment.complete` only after model and restart/output validation.
Recovery reruns from the last completed predecessor and never trusts a partial
attempt.

Only one persistent `--watch` controller may own a campaign root. The shared-
filesystem POSIX `controller.lock` is authoritative; PID/host mirrors may be
stale and login-node process namespaces are separate. Inspect the recorded
host, but use the kernel lock to determine ownership.

The completed national RRTMG campaign used 12 nodes, 60 ranks, 12-hour
segments on `normal`, `max_active_models=1`, a six-hour limit, and a fail-closed
50% partition-share guard after repeated external `preemptible` cancellation.
Treat that as historical provenance, not a default for a new experiment; first
requalify the smallest representative topology and physics.
