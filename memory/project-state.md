# ICON-to-HICAR project state

## Objective

Build a reproducible, scientifically defensible workflow to downscale the
20-year ICON REA-L-CH1 reanalysis from about 1 km to 200 m over Switzerland
with HICAR, with 100 m as a later application-specific option. Production must
stream forcing and compact products without retaining a 20-year raw-output
cache.

## Current verdict

**Stop at the completed V29 72-hour summer gate. The project is not ready for
a month, annual cycle, 20-year 200 m production, or 100 m science.**

HICAR commit `5da4b1980497f20468e6e4b5b4c4a584849c3454` was tested as a
scientifically new baseline because adding restart-persistent cumulative water
diagnostics is not trajectory-neutral on the national GPU configuration. The
model and all engineering gates passed, but both independent frozen
temperature screens failed with a broad warm bias.

Do not launch the prepared winter event, restart overlap, month, annual,
20-year, or 100 m simulations. Do not relax the temperature thresholds after
seeing the result. The next agent should use the preserved summer history for
a bounded process diagnosis and propose a mechanism-based correction before
rerunning seasonal events.

## Current scientific problem

The 2020-07-01 00 UTC through 2020-07-04 00 UTC V29 event completed cleanly
with 25 three-hourly records.

The two failing pre-frozen screens are:

| Metric | HICAR | REA-L | Frozen HICAR maximum | Result |
|---|---:|---:|---:|---|
| SwissMetNet height-adjusted T2m RMSE | 5.120 K | 1.435 K | 3.435 K | FAIL |
| TabsD daily temperature RMSE | 4.365 K | 1.025 K | 3.025 K | FAIL |

HICAR's station T2m bias is `+3.663 K` over 3,716 pairs. The TabsD bias is
about `+3.382 K`; it is broad and largest in the 1000--2000 m bands. This is
not a boundary-only artifact.

Concurrent diagnostics are consistent with an overly warm/dry surface regime:

- station relative-humidity bias: `-14.31` percentage points;
- station global-shortwave bias: `+28.31 W m-2`;
- station interval-precipitation bias: `-0.676 kg m-2`, with almost no
  precipitation produced at the sampled HICAR points.

These are hypotheses, not causal attribution. The first bounded diagnosis
should stratify the preserved trajectory by time of day, elevation, land
class, snow/soil initialization, cloud/radiation state, precipitation, and
surface sensible/latent fluxes. It should compare HICAR, REA-L, SwissMetNet,
TabsD, SIS, and RhiresD on the existing matched samples. Do not begin with
another national simulation.

## What passed

### National 200 m numerical and runtime baseline

- The 80-level SLEVE 2/6 geometry passes the actual distributed-grid
  Jacobian/thickness gates.
- The discretely adjoint wind solver is the accepted national baseline.
  Initial physical-wind solve status must be zero with true relative residual
  at most `1e-5`; mass-conservation residual must be at most `2e-5`.
- Four `normal` nodes, each with four GPU compute ranks and one CPU-only I/O
  rank, are the validated Switzerland 200 m layout.
- Canonical CPU and GPU build procedures are in
  `.agents/skills/hicar-balfrin-runtime/references/build-and-performance.md`
  and the entry point is
  `case_studies/swiss_200m/scripts/build_hicar_balfrin.sbatch`.

### Restart equivalence

The qualified restart baseline is HICAR
`246c8992564ad3e89f49546776ae31ad8a1ce239`, available from
`git@github.com:ofuhrer/HICAR.git` as
`codex/restart-noahmp-state-v26`.

- Compact 20-step split/uninterrupted test: whole-model final history exact.
- 701 x 701 bridge: 32/32 history and 193/193 restart fields exact.
- Swiss-national two-hour policy gate: PASS under the frozen tolerances.
- The final missing Noah-MP trajectory state was snow age `tau_ss`.

This qualifies restart from a validator-published boundary for the tested
trajectory. It does not prove independent-year equivalence or authorize
production.

### Selective SCHNAPS upstream integration

Codeberg `main` was audited through
`622c2d42f6939b94f77fe6f68eb9cf5d822e1ab1` (2026-07-24): all 50 commits
after the shared ancestor were classified, all runtime/domain/test/build
touches received file-level review, `develop` has no commits ahead of `main`,
and the remaining diverged topic branches contain older pre-squash work.

The applicable fixes were ported onto the qualified V26 restart baseline as
`codex/schnaps-relevant-fixes` at
`7700c97a0248abcc1db055ef04c22e1ff9ec6d22`:

- `cc276e4c`: Morrison CPU/GPU correctness, lateral-wind rotation, density
  exchange placement, and radiation-horizon indexing;
- `c4c8e81a`: applicable I/O, initialization, GPU-lifecycle,
  manual-vertical-grid, soil, and metadata fixes plus regressions;
- `7700c97a`: decomposition-stable advection compiler policy.

Local debug validation passed 52/52 tests. Balfrin CPU/GPU release builds,
four-rank CPU tests, four-A100 halo tests, the full variational-wind Alpine
bridge, and the upstream Standard Morrison CPU/GPU comparison all passed.
The official upstream tolerance comparator reported 4/4 fields passing with
no failures, warnings, or missing fields (jobs `4950793`, `4950851`,
`4950867`, `4950945`, `4950946`, `4950947`, and `4951003`--`4951006`).

The complete commit classification, deliberate deferrals, executable
checksums, job topology, and comparison metrics are in
`case_studies/swiss_200m/validation/schnaps_upstream_integration_7700c97a.json`.

Keep the GitHub fork as `origin` and the old GitHub project remote unchanged;
track the migrated project with the additional fetch remote:

```text
schnaps https://codeberg.org/SCHNAPS-Model/SCHNAPS.git
```

This branch is implementation evidence, not a production pin, and does not
alter the failed V29 scientific verdict or authorize new production.

### V29 summer engineering and budget gates

Balfrin model job `4949561` completed in `4:49:49` on four normal nodes.
Independent jobs `4949562`--`4949565` passed scientific budget/source,
solver/conservation, SwissMetNet-report publication, and OGD-report
publication.

The original checkpoint wrapper job `4949566` failed because the immutable
runtime omitted a transitive Python leaf. Its failed report was preserved.
Checksum-frozen validator-only replay `4950083` passed all 24-, 48-, and
72-hour checkpoints without rerunning HICAR. Each restart is
43,479,462,490 bytes. The replay report SHA-256 is
`43c7270508ecaa3abc9a883fbd9d86eff7eb3bdce94e4c804cfc968c567b2e01`.

The V29 event also passes:

- exact source/executable/static provenance;
- all wind solves and mass-conservation checks;
- production-cumulative water semantics with no decreases;
- active-soil-interior water residual `+0.00345 kg m-2` against a
  `5 kg m-2` limit;
- nonzero paired runoff `0.34940 kg m-2`;
- surface energy-closure MAE `1.839 W m-2` against `10 W m-2`;
- station wind-vector, pressure, and precipitation catastrophic-degradation
  screens;
- RhiresD precipitation catastrophic-degradation screen.

Replacement assessor `4950108` published `HOLD_AND_DIAGNOSE` and exited
nonzero by design because the two temperature screens failed.

## Source identities

### Coordinator

- Remote: `git@github.com:ofuhrer/icon_downscaling.git`
- Active branch: `main`
- Consolidated handoff checkpoint:
  `482b1f441d625fff00a9ebd634ebeeed3e01f75d`.

### HICAR

- Remote: `git@github.com:ofuhrer/HICAR.git`
- Migrated-upstream tracking remote:
  `https://codeberg.org/SCHNAPS-Model/SCHNAPS.git` as `schnaps`.
- Stable production-performance base:
  `feature/icon_downscaling` at
  `d6c52a54bc6338ea82922f9b69d9fdc02b54267a`.
- Qualified restart baseline:
  `codex/restart-noahmp-state-v26` at
  `246c8992564ad3e89f49546776ae31ad8a1ce239`.
- Failed scientific V29 handoff:
  `codex/v29-summer-warm-bias` at
  `5da4b1980497f20468e6e4b5b4c4a584849c3454`.
- Selective SCHNAPS integration candidate:
  `codex/schnaps-relevant-fixes` at
  `7700c97a0248abcc1db055ef04c22e1ff9ec6d22`.

V29 is intentionally not a production pin. Its water diagnostics are useful
and its restart policy passes, but it is a scientifically failed baseline.

### V29 durable source

The exact V29 Git bundle is:

```text
/store_new/mch/msopr/olifu/icon_downscaling/recovery/v1/source/hicar-water-budget-v29-5da4b198.bundle
```

SHA-256:
`8d6121e163a3141f2ea296fae9bb60e6889afd8a90c1d528968eca49f366596e`.

## Canonical evidence

### V29 transition

Local compact outcome:

```text
case_studies/swiss_200m/validation/water_budget/baseline_transition_v29_5da4b198/summer_transition_outcome.json
```

Its SHA-256 is
`aa2fd304d2617a59ffaaf6391648c6ce98a140177903c7fedbd6f0d073a85460`.

The complete Balfrin event assessment SHA-256 is
`d758f25227136294e6274de293daf4090c02c566bd1fdc0f40f0f816769b5785`.

The selected 25-record V29 history, model configuration/log, and independent
reports are published by
`recovery/archive_plan_v29_summer_handoff_v1.json` under:

```text
/store_new/mch/msopr/olifu/icon_downscaling/recovery/v1/artifacts/qualification/v29-summer
```

The archive deliberately excludes the three 43.48 GB restarts because no
downstream overlap is authorized. Its publication manifest is:

```text
/store_new/mch/msopr/olifu/icon_downscaling/recovery/v1/manifests/archive-v29-summer-handoff-v1.json
```

Manifest SHA-256:
`e32284b286ef7d2f482b57412ec912ad4f5764a5bc4ff5680a4bdc9336c9aedc`.
Independent readback job `4950163` passed for all 13 files and
16,349,047,095 bytes.

### Restart qualification

Retain only the compact V26 qualification evidence needed to reproduce the
current restart conclusion:

```text
case_studies/swiss_200m/validation/restart_continuity/restart_bitwise_micro_v26_246c8992.json
case_studies/swiss_200m/validation/restart_continuity/restart_bridge_noahmp_state_v26_246c8992_strict.json
case_studies/swiss_200m/validation/restart_continuity/restart_bridge_noahmp_state_v26_246c8992_policy.json
case_studies/swiss_200m/validation/restart_continuity/restart_national_noahmp_state_v26_246c8992_strict.json
case_studies/swiss_200m/validation/restart_continuity/restart_national_noahmp_state_v26_246c8992_policy.json
```

Failed historical candidates remain recoverable from the checksum-bound
bundles/manifests under
`/store_new/mch/msopr/olifu/icon_downscaling/recovery/v1`; they do not need
active branches, worktrees, build directories, or raw outputs.

Before cleaning the legacy Balfrin root HICAR checkout, its previously
unrecorded 63,329-byte dirty streaming/advection patch against `86c9a87e` was
archived as
`source/hicar-balfrin-root-dirty-after-86c9a87e.patch`, SHA-256
`dbec40233ff87cdae0b02c55d63c61019fcc84982344aa7d0cdbb36aba1a5a8e`.
It passed an apply check and independent archive readback job `4950168`.
It is non-promoting legacy evidence, not an active source branch.

### Other validated foundations

- Swiss 200 m static domain and SLEVE geometry:
  `case_studies/swiss_200m`.
- Independent validation contract:
  `case_studies/swiss_200m/config/observational_validation_contract.json`.
- REA-L-derived summer and winter land-state initialization reports:
  `case_studies/swiss_200m/validation/rea_l_land_initialization_20200701_0000.json`
  and
  `rea_l_land_initialization_20200115_0000.json`.
- Streaming and archive contract:
  `case_studies/swiss_200m/streaming/README.md`.
- Pre-emption-safe controller:
  `orchestration/README.md`. The controller now requires a checksum-bound
  immutable runtime release and a separately published pinned Python
  environment; automatically journals and retires verified raw output,
  forcing, failed attempts, and superseded restarts; preserves periodic/final
  checkpoints; caps unretired per-chain backlog; and counts pending jobs
  against the global 44-node capacity.
  Live engineering drill jobs `4951109`--`4951111` passed both graceful
  cancellation and true hard-kill recovery: SIGTERM exited `75:0` with an
  interruption report, SIGKILL exited `0:9` without cleanup, and the
  controller created a third immutable retry before capacity was paused.
  The non-promoting report is
  `/scratch/mch/olifu/icon_hicar/engineering/preemption-recovery-20260727-v4/preemption_recovery_engineering.json`,
  SHA-256
  `2530af3f9f8e5e5507579e0babb6e1e4ad132c9d76e11be0bac2d31594b12d86`.
  It binds engineering runtime release `preemption-engineering-20260727-v5`
  (manifest SHA-256
  `a441de9d926662fc13f6f5d1e6e6b398b53dc31bd6c8bffdb8a703ce1739c4ea`)
  and Python-environment report SHA-256
  `d163de3316e9eb9202f75e198bddd2b562e8bbc61c407093341345aa31c84021`.
  It is explicitly engineering-only and does not change any scientific or
  production authorization. The exact committed implementation at coordinator
  commit `91f917e03e7dfb2d526e1d615cb11584820922d8` is also published as the
  clean production-purpose runtime
  `/scratch/mch/olifu/icon_hicar/runtime/releases/preemption-production-91f917e-v1`;
  its manifest SHA-256 is
  `3c5313f48b6d828c789438a993618b1c785c565aab26ea5ce81961228bb6840d`.
  Python bootstrap job `4951118` completed and published
  `/scratch/mch/olifu/icon_hicar/runtime/python_environment-preemption-production-91f917e-v1.json`,
  SHA-256
  `14cef4b493b52e9b36a436f104c166f34a1ddac08458ecccecadfef64af5e4eb`.
  Both artifacts pass the original checksum contract and the current Python
  imports still match the report, but a 2026-07-27 independent re-audit found
  that this release is **not launchable**: it omits
  `case_studies/swiss_200m/config/hicar_swiss_200m.nml.in`, which the frozen
  renderer reads; the forcing producer selects its validator from the mutable
  case root rather than the frozen release; and the shared `venv_static`
  remains owner-writable while reconciliation does not recheck its recorded
  package set. The cancellation drill exercises the controller and sleep
  helper, not the real HICAR/srun/restart path. No first-year campaign plan is
  published under the Balfrin campaign root. Treat the production-purpose
  runtime and Python report as failed launch-readiness evidence until these
  gaps are repaired and a real bounded HICAR cancellation/restart drill passes.
  This correction does not change the separate scientific hold.
- Wind-climatology engineering pathway:
  `case_studies/swiss_200m/wind_climatology/PRODUCT_CONTRACT.md`.

## Production authorization

All of the following remain false:

- V29 winter event;
- V29 hour-48 restart overlap;
- paired V29 transition assessment;
- 200 m month pilot;
- annual cycle;
- 20-year 200 m production;
- 100 m engineering/science escalation.

`month_expected_hicar_commit` remains unset. The month-source adapter and
canonical-plan preparer are fail-closed planning tools only.

No month-long simulation should be submitted while the warm-bias problem is
open.

## Next bounded goal

Diagnose the V29 warm/dry surface regime from the archived 72-hour history,
without new national model compute.

Required outputs:

1. A time-of-day by elevation/land-class decomposition of T2m, humidity,
   shortwave, precipitation, surface sensible/latent heat, soil temperature,
   and soil moisture differences against REA-L and available observations.
2. A determination of whether the error is present at initialization or grows
   during the first diurnal cycle.
3. A ranked causal hypothesis tied to specific HICAR/Noah-MP state,
   initialization, coupling, radiation/cloud, or precipitation code.
4. One minimal discriminating test on a small representative domain or a
   short national window; no month run.
5. Only after a mechanism-based improvement: repeat the frozen summer event.
   Run winter and the independent hour-48 overlap only if summer passes
   unchanged thresholds.

## Cleanup state

Cleanup is complete and recorded in
`recovery/cleanup_handoff_v29.json`.

The HICAR remote now retains only `main`, `feature/icon_downscaling`, the
qualified V26 restart reference, and the failed-but-reproducible V29
scientific reference. The three superseded solver-research branches were
deleted after bundle/source verification. Local HICAR is clean on
`feature/icon_downscaling` at `d6c52a54`.

Balfrin cleanup jobs `4950170`, `4950184`, and `4950197` completed
successfully. They
removed 148 experiment source clones, 59 build variants, all superseded
restart/water-budget trajectories and V29 restart payloads, the legacy 250 m
raw case/cache, obsolete radiation/cloud-fraction payloads, stale
bundles/logs/locks/environments, and one-time wrappers. No user jobs remain.
`$SCRATCH/icon_hicar` is reduced to 20,243,081,756 bytes.

Retained scratch consists of the clean HICAR checkout, operational
dependencies/observations, Alpine-bridge inputs, and the expensive Swiss
100 m/200 m source, forcing, and static inputs. The selected V29 history and
rebuild-critical source remain under the durable root. Scratch deletions are
not otherwise recoverable.

Locally, ignored caches/builds/output and obsolete one-off restart,
multilevel-solver, cloud-recovery, and transport-probe scripts/reports were
removed. The optional fieldextra checkout was removed and can be recreated
from `externals/fieldextra.lock`.

## Verification state

- Full local coordinator suite before cleanup: `327 passed`.
- Portable coordinator suite after consolidation: `265 passed`.
- Repository syntax, whitespace, and scoped Ruff checks after consolidation:
  PASS (`156` Python and `147` shell files).
- Clean GitHub clone with initialized HICAR submodule: portable suite and
  repository checks PASS at coordinator `482b1f4`.
- Public coordinator CI run `30245948287`: PASS.
- Current pre-emption-focused suite: `26 passed`, including eleven-slot
  empty-capacity submission, full-cluster pending-job backpressure,
  interruption-resumable retirement, immutable runtime/Python publications,
  and SIGKILL retry classification.
- Current portable coordinator suite: `276 passed`. A whole-workspace run has
  the same `276` coordinator passes with `11` opt-in HICAR
  source-contract failures because the local submodule is intentionally on
  clean production-performance base `d6c52a54`, which does not contain the
  V29 water/metadata and restart-initialization source line those tests
  inspect. No pre-emption test failed.
- Against the exact V29 source, `7/11` opt-in HICAR source-contract tests
  pass: all restart-initialization and water-budget contracts pass. The four
  remaining tests describe the separate wind-climatology/CF-metadata source
  line, which has not been merged into V29. Public CI correctly excludes this
  union-of-development-lines suite. A future production source must integrate
  and requalify the needed lines; do not infer that V29 satisfies the wind
  output contract.
- `summer_transition_outcome.json` publication hash check: PASS.
- V29 source branch and durable Git bundle: available.
- V26 qualified source branch: available.
- The HICAR submodule is clean on `feature/icon_downscaling` at `d6c52a54`;
  the V26 and V29 source references are remote branches, not the public
  coordinator pin.
- The conservative full-deletion recovery audit remains intentionally
  `NOT READY`: the annual production archive contract is unresolved and
  durable payload verification must run on Balfrin where `/store_new` is
  mounted. This does not invalidate the completed, explicitly scoped scratch
  cleanup.
