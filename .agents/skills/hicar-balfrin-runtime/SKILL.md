---
name: hicar-balfrin-runtime
description: Build, test, run, benchmark, or debug HICAR on MeteoSwiss Balfrin with CPU or NVHPC/OpenACC GPUs.
---

# HICAR on Balfrin

Use this skill with `balfrin-user-environment`.

## Build and debug

- Use release builds for campaign throughput and debug builds for diagnosis.
- The maintained build entry point is
  `case_studies/swiss_200m/scripts/build_hicar_balfrin.sbatch`.
- GPU campaigns use NVHPC/OpenACC, Cray MPICH, four GPUs per node, five ranks
  per node, and the maintained rank wrapper. Set
  `MPICH_GPU_SUPPORT_ENABLED=0`; HICAR's transport handles device exchange.
- Run HICAR unit tests and a two-hour target-stack pilot after source changes.
  Use synchronization or GDB only after a normal launch reproduces a fault.

Detailed compiler commands and measured scaling are in
`references/build-and-performance.md`; runtime failures are in
`references/debugging.md`.

## Segmented campaigns

Use `orchestration/rd_campaign.py`. The selected national 200 m setup uses
12-hour simulation segments on the exact 12-node topology qualified by the
daylight smoke; do not change that decomposition merely to pack more chains.
Run only one persistent `--watch` controller per campaign root. Watch mode
holds the shared-filesystem POSIX lock `controller.lock`; that kernel lock is
the ownership authority across login nodes and is released automatically when
the owner dies. `controller.pid` and `controller.host` are operator-friendly
mirrors only and can be stale after an ungraceful exit. A second watcher must
fail without submitting or reconciling anything; one-shot invocations remain
non-exclusive.
Each model attempt:

- names its configured partition explicitly, validates live exact-group access,
  and checks the actual Slurm partition inside the job;
- uses at most six hours wall time and 12 nodes for the selected national
  200 m case;
- writes 600 s evaluation output and a terminal restart;
- runs in a new attempt directory;
- creates `segment.complete` only after HICAR success and restart/output checks.

Four consecutive national attempts on `preemptible` were externally displaced
after 22--71 minutes without model failures. The selected campaign therefore
uses `normal`, `max_active_models=1`, and a fail-closed
`model_max_partition_fraction=0.5`. On the live 44--46-node partition this is
one 12-node/60-rank segment at a time (26--27%); never release a second model
job concurrently. Recovery still reruns a failed segment from its last
completed predecessor restart and never trusts a partial attempt. A single
seasonal chain remains serial. National input production uses the bounded
configuration and exclusive CPU nodes because each eight-worker record has
measured peak memory near 240 GB.

## Runtime essentials

Link `NoahmpTable.TBL`, `rrtmg_support`, `rrtmgp_support`, and `mp_support` into the run
directory. Provide a valid-time runtime domain, continuous hourly hicarprep
regular forcing, and an existing predecessor restart for every continuation.
Sparse LBC is optional experimental input, not part of the selected reference.
Keep HICAR initialization/projection enabled once at the start of each chain;
restarts must restore the full model state.

Success requires HICAR's completion message, nonempty output, and the expected
terminal restart. For scientific assessment also inspect solver residuals,
field extrema, time continuity, and comparison metrics.

Do not infer the latest simulated timestamp by opening a NetCDF output file
that HICAR is still writing. The external reader can observe only an older
flushed prefix even while `model.out` has advanced by several hours. Use the
model log for live progress, and use a closed/rotated output file or the
terminal segment validator for field-level finiteness and extrema.
