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

Use `orchestration/rd_campaign.py` and 24-hour simulation segments by default.
Each model attempt:

- names `preemptible` explicitly and checks live group access;
- uses at most six hours wall time and two nodes for the current 200 m case;
- writes hourly output and a terminal restart;
- runs in a new attempt directory;
- creates `segment.complete` only after HICAR success and restart/output checks.

Pre-emption may kill a job before a large restart can be written. Recovery is
therefore to rerun that segment from the last completed predecessor restart,
not to trust a partial attempt. Independent seasonal chains may run in
parallel; a single chain remains serial. Input production uses no more than
two `pp-short` jobs.

## Runtime essentials

Link `NoahmpTable.TBL`, `rrtmg_support`, `rrtmgp_support`, and `mp_support` into the run
directory. Provide a valid-time runtime domain, continuous hourly hicarprep
forcing and sparse LBC lists, and an existing predecessor restart for every
continuation. Keep HICAR initialization/projection enabled once at the start
of each chain; restarts must restore the full model state.

Success requires HICAR's completion message, nonempty output, and the expected
terminal restart. For scientific assessment also inspect solver residuals,
field extrema, time continuity, and comparison metrics.
