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
non-exclusive. Balfrin login-node process namespaces are separate: `ps` or
`pgrep` on whichever node the `balfrin` alias selected cannot establish that a
controller recorded on another login node has died. Read `controller.host`
first and inspect the PID on that exact host; use the shared-filesystem lock as
the final ownership authority.

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

`HICAR/run` is ignored and is therefore absent from a fresh recursive Git
clone even when the coordinator and HICAR submodule are otherwise complete.
Populate that support tree from the checksum-identical pinned build/source
before launching. The campaign controller must fail its runtime-asset preflight
before `sbatch` if the executable, `NoahmpTable.TBL`, or any of the three
support directories is absent; do not spend model retries discovering this in
the batch wrapper.

Ordinary continuation must keep restart configuration comparison fail-closed.
For a predeclared causal sensitivity that intentionally changes one namelist
option across a validated checkpoint, set `HICAR_RESTART_OVERRIDE_CHECK=1`,
record the exact mismatch from HICAR's restart report, and change nothing else.
Do not edit the checkpoint. Builds predating restart-domain provenance may use
`HICAR_ALLOW_MISSING_RESTART_DOMAIN_PROVENANCE=1`; this accepts only a missing
`domain.height_lowest_level=20` attribute, never a wrong/non-finite value or
any other mismatch. The retained post-campaign baseline `5bee3c92` serializes
`domain.auto_level`, `height_lowest_level`, `model_top_height` and
`stretch_fac`; do not enable the compatibility exception for outputs written
by that commit or later. It remains only for immutable `4a425677` campaign
restarts whose 20 m geometry is independently bound by the rendered namelist
and static-domain checks.

Success requires HICAR's completion message, nonempty output, and the expected
terminal restart. For scientific assessment also inspect solver residuals,
field extrema, time continuity, and comparison metrics.

Do not infer the latest simulated timestamp by opening a NetCDF output file
that HICAR is still writing. The external reader can observe only an older
flushed prefix even while `model.out` has advanced by several hours. Use the
model log for live progress, and use a closed/rotated output file or the
terminal segment validator for field-level finiteness and extrema.
