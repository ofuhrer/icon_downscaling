# ICON-to-HICAR legacy evidence ledger

> [!NOTE]
> This file preserves historical evidence and canonical locators, including
> older transactional language. It does not define the current project mode,
> scientific synthesis, priorities, or next experiment; those live in
> [`AGENTS.md`](../AGENTS.md) and
> [`project-assessment.md`](project-assessment.md). A PASS, hold, gate,
> promotion, authorization, or use of words such as “current” and “active”
> below must be read in its historical context, not as a standing work queue.

## Historical evidence

### Fixed-parameter improvement candidate

Research against Swiss/Alpine WRF-Noah practice established that the historic
Swiss WRF static package (ASTER/CORINE/constant silty-clay-loam soil) is a
benchmark, not a replacement for the newer public HICAR sources. The first
non-production candidate uses modal 10 m WorldCover aggregation and maps all
six SoilGrids depth means onto Noah-MP's four soil layers. For clean
attribution it copies `topo`, `topo_highres`, `topo_driving`, and
`topo_blend_weight` bitwise from the published baseline. The static file
writes `soil_type_layer`, layer bounds, sand/silt/clay diagnostics, and a JSON
processing manifest; publication is validate-then-atomic-rename.

HICAR locally exposes a guarded `soiltexture_var` input for the already-present
Noah-MP `nmp_opt_soil=2` pathway. The national renderer keeps `nmp_opt_soil=1`
by default and activates the four-layer input only with
`--depth-varying-soil`. The change is not production-qualified and no national
simulation has been authorized. Research conclusions, ranked Swiss national
data upgrades, and the paired A/B/C case design are frozen in
`case_studies/swiss_200m/fixed_parameters/RESEARCH_AND_EXPERIMENT_DESIGN.md`;
the corresponding static gate is `audit_static_candidate.py`.

The static-only A/B/C audit passed as
`STATIC_AUDIT_PASS_PAIRED_COUPLED_CASES_REQUIRED` in Balfrin job `5019543`.
A is baseline SHA-256 `bf03fa8c...`; B and C deliberately share candidate
SHA-256 `7b470441...` and differ only by `nmp_opt_soil=1` versus `2` plus
`soiltexture_var`. Terrain differs by exactly 0.0 m. Modal aggregation changes
378,506 cells (10.10%) while reducing fragmentation in every audited
land-cover group. Surface soil changes on 112,466 cells (3.00%); every active
land cell has a valid class and composition closure is 99.90-100.10%. All 600
source files passed size/SHA-256 verification. The 896 MB shared source cache
is `$SCRATCH/icon_hicar/cache/hicar_static_public`; the checksum-bound release
is `/store_new/mch/msopr/olifu/icon_downscaling/qualification/static_abc_v1`.
Build-wrapper job `5019323` failed only after candidate publication because
its first minimal runtime omitted `config/domain.json`; the repaired
read-only-runtime validator and decisive audit passed, and the durable release
records this distinction.

The supporting build blocker is resolved without a global range-check waiver.
Clean HICAR source `43c636be24fb920cafebac17ab0ce0bab7f8343c` passed the
canonical NVHPC/OpenACC GPU-MPI build in job `5020628`; executable SHA-256 is
`d10362a7946ddec4fe549bcad55a291c29c37863fed1688fe86d9a13a7e50bca`.
The source and build provenance are retained with the synthetic model gate
described below. This is an experiment build, not the project production pin.

The authoritative GLAMOS SGI2016 audit passed without applying an automatic
glacier correction. Baseline recall against the reference polygon is
`0.9642083`, candidate recall is `0.9771667` (`+0.0129583`), and the candidate
cell-centre mask covers `960.0 km2` versus `961.2781 km2` in the reference.
The decision is `GLAMOS_AUDIT_PASS_NO_AUTOMATIC_CORRECTION`; the
checksum-bound evidence is at
`/store_new/mch/msopr/olifu/icon_downscaling/qualification/authoritative_static_reference_audit_v1`.
The swissTLM3D inventory is epoch-aligned to 2021 WorldCover, but the 3.14 GB
national archive was intentionally not downloaded for an unbounded audit.

The executable paired-process design is version 3 at
`case_studies/swiss_200m/fixed_parameters/qualification_artifacts/static_process_case_plan_v3/process_case_plan.json`
(SHA-256
`009dce31f7a37c9919351990322659057a1242fc4ab22dcc06d3b57ed04188f9`).
It contains 60 independent, uninterrupted A/B/C runs: four frozen events, two
tiles, and adaptive 24, 48, 72, 120, and 168 h preconditioning leads followed
by a common 24 h score window. The minimum lead must pass against the next
longer lead and the complete longer tail; failure of 120 versus 168 h is
`DO_NOT_INTERPRET_PROCESS_EFFECTS`. The durable plan is
`/store_new/mch/msopr/olifu/icon_downscaling/qualification/static_process_case_plan_v3`.
No process run or national coupled run has been authorized.

### Terrain-radiation causal candidate

Terrain radiation is implemented as a non-production causal candidate while
the national default remains off. The renderer exposes `off`, `direct`,
`direct-diffuse`, `full-local`, and `full-neighborhood` profiles and requires
an audited static artifact. The HICAR source separates direct, diffuse,
reflected-shortwave, and terrain-longwave controls; it now derives direct and
diffuse terrain corrections independently from restart-persistent unmodified
horizontal-plane fluxes. This corrects the confirmed former direct/diffuse
coupling. The reflected-shortwave pathway already carried irradiance units
and was retained with an analytic test.

`publish_terrain_radiation_static.py` enforces 90 sectors at 0,4,...,356
degrees, HLM zenith-angle convention (flat/open = 90 degrees), SVF and angle
ranges, source-DEM checksum, vertical datum, search distance, generator
identity, atomic publication, and a checksum-bound manifest. The bounded
synthetic/valley causal ladder, known limitations, diagnostics, and promotion
gates are recorded in
`case_studies/swiss_200m/fixed_parameters/RESEARCH_AND_EXPERIMENT_DESIGN.md`.
Checksum-bound 21 x 21 flat/open and single-sector blocked-horizon geometry
and merged statics are published under
`/store_new/mch/msopr/olifu/icon_downscaling/qualification/terrain_radiation_synthetic_v1`.
The blocked fixture uses azimuth 88 degrees (Fortran sector 23), a 30-degree
horizon, HLM 60 degrees, and analytic SVF 0.9972222. UTC is explicit as
`tzone=0.0`; the sampled blocked sector shadows the direct beam at 06:50,
06:55, and 07:00 UTC and exposes it at 07:05 UTC.

The full synthetic model gate ran seven cases in job `5020639` with the clean
experiment build. The terrain component passes: on flat terrain the enabled
direct flux matches its own raw RRTMGP component within
`6.103515625e-05 W m-2`, diffuse is bitwise identical, the blocked direct beam
has the analytic shadow transition, and the diffuse profile applies the
expected SVF. Enabled-versus-off coupled-run drift is retained as a diagnostic
and is not used as the component-identity test.

The exact same stack fails restart continuity for ten saved variables. The
largest first post-split surface differences include `53.356 W m-2` latent
heat, `46.074 W m-2` sensible heat, and `0.8736 K` surface temperature; the
corresponding final differences decay to about `0.147 W m-2`, `0.210 W m-2`,
and `0.00604 K` but remain outside tolerance. The final decision is therefore
`TERRAIN_COMPONENT_PASS_RESTART_GATE_FAIL`. Its complete checksum-bound run,
assessment, exact executable, build provenance, workflow snapshot, and full
HICAR Git bundle are durable at
`/store_new/mch/msopr/olifu/icon_downscaling/qualification/terrain_radiation_model_gate_v4`;
assessment SHA-256 is
`286e531b846dfcb68b5ad4549add1983e1cc6e9f895648c6861155ba08b58838`.
This permits neither a valley case nor production. Any later A/B/C process
case must keep preconditioning and scoring in one uninterrupted run until the
candidate restart path passes.

Current focused static/terrain tests pass (`27 passed`). The full coordinator
suite is `365 passed, 3 failed`; the three failures are the pre-existing,
unmerged V29 cumulative water/precipitation metadata contracts. They were not
weakened or hidden.

### Retired Thompson causal-resolution path

We are testing one causal hypothesis, not tuning the model: **does replacing
Morrison with Thompson microphysics correct the early cloud/rain deficit that
appears before the later land/surface divergence?**  The causal evidence is
common-grid and time-aligned; at 03 UTC the HICAR cloud and rain deficits are
already present, while the predeclared land-divergence threshold is first met
at 15 UTC.  This rules in a cloud-path correction as the one permitted
discriminating change, but does not yet establish that Thompson succeeds.

The former decision path was frozen and sequential:

1. `4957030` must publish a complete, valid 24-hour Thompson model product.
2. Dependent assessor `4957277` must return `PASS_NON_PROMOTING` and show
   strictly smaller absolute common-grid cloud *and* rain deficits than
   Morrison at both 06 and 09 UTC.
3. Immutable post-assessment job `4958080` then independently either rejects
   the correction or authorizes the frozen 72-hour summer gate.  It does not
   run HICAR.
4. Only an authorization permits regeneration/publication of the retired
   72-hour forcing payloads and one Thompson summer gate.  Seasonal validation
   may resume only if that gate passes unchanged science criteria.

The first job did not complete: Balfrin cancelled `4957030` at 17:07:20 CEST
on 2026-07-28 after 5:50:22 wall time (Slurm state `CANCELLED`, exit signal
15, cancellation recorded against the job owner, no time-limit or model-error
reason).  It had reached 07:07 UTC simulation time and had written the 07:30
UTC output before termination.  No completion publication or ready marker was
created, so dependent assessor `4957277` and the following gate job `4958080`
were cancelled by their `afterok` dependencies; no promotion decision exists.
The cancellation is not evidence for or against the Thompson mechanism
correction. It is the explicit operational decision not to use Thompson
because the throughput is prohibitive. Do not resubmit Thompson or start its
summer gate.

### Wind-production candidate and spin-up gate

The qualified wind-solver base remains `7700c97a`. The fixed-height
wind-climatology implementation had been stranded in unreferenced HICAR commit
`2999c9bdf6e0ed50a7f44311e2c8555e26848d31`, which predates the production
solver/advection corrections. Only that output feature and its CF metadata
fixes were ported onto the qualified base as local HICAR candidate
`b5146a3c67612bd7d29abcd7c480ae4ae1bd31a2`; the V29 water-diagnostic sibling
branch was not merged.

Candidate `b5146a3c` supplies output-time `u_agl`, `v_agl`, and `rho_agl` at
50, 75, 100, 125, 150, and 200 m AGL, the `height_agl` coordinate, correct
10 m/surface/PBL metadata, coupled allocation, and no forced full-level `z`
history for the compact profile. A fresh Balfrin release GPU/NCCL build of
the byte-identical source diff passed as job `4959835`; executable SHA-256 is
`b787611d13f11ae154bc5887e28027593415a580117e33a1cd08d2ab2b5fb2c8`.
An exact-commit clean rebuild for the active experiment passed as Balfrin job
`4960006`; executable SHA-256 is
`6378df4940f06538f888c0e889e15e057d1c610c6b0ae68fa4f053ecf63ac7f0`.
The candidate has not replaced the project-wide production pin and still
requires a topology-matched output smoke on retained inputs.

`wind_production_candidate.json` freezes Morrison as the conservative current
physics and preserves the previously assessed 24-field-equivalent
`wind_climatology` source stream: `u/v` at 10 m, `u/v/rho` at six hub heights,
and `ustar`, roughness, surface Richardson number, and PBL height. Raw records
are reduced and retired; the national archive keeps compact
monthly/seasonal/diurnal sufficient statistics and extremes, with chronology
only for selected heights, periods, or regions. ICON `VMAX` remains excluded.
Resolved sample maxima are not gusts; `wind_speed_of_gust` remains behind the
duration-matched Swiss observation gate already defined in
`PRODUCT_CONTRACT.md`.

The executable spin-up design is in `SPINUP_EXPERIMENT.md` with manifest
builder `prepare_wind_spinup_experiment.py` and bounded NetCDF assessor
`assess_wind_spinup_convergence.py`. The bridge screen compares discarded
spin-ups of 0, 1, 2, 3, 6, 12, 24, and 48 hours over identical 24-hour target
windows plus one validation-overlap hour. It requires summer, winter,
strong-gradient/foehn, and calm-stable regimes, uses the 48-hour member as the
convergence reference, and selects the shortest candidate whose entire
longer-spin-up tail passes at every height and event. A Swiss-grid confirmation
and a 30-day daily-cold-start versus restart-linked stitched-chain comparison
remain mandatory. The pre-emptible planner now permits a shorter final segment,
so a 26-hour chain is represented as 24 plus 2 hours, and binds each
independent chain to its time-matched REA-L land initialization. The
32-member, four-regime bridge screen is launched as land-preparation array
`4960048`, followed by dependent campaign watcher `4960158`. It contains 76
restart-linked segments, uses 30-minute wind output, Morrison physics, at most
11 concurrent two-node preemptible model attempts, and at most two concurrent
CPU tasks. The strong case is Storm Sabine on 2020-02-10; the calm/stable
candidate is the documented 2014-11-21 Swiss Plateau inversion and remains
subject to its frozen REA-L-only regime check. This qualification does not
authorize production. All 32 land initializations published, watcher
`4960158` entered `RUNNING`, chained successor `4960220`, and submitted its
first two-way-throttled forcing batch as array `4960221`.

The live screen exposed two production-relevant controller gaps. Its 76
segment plans contain 1,260 forcing-record slots but only 296 unique valid
times (4.26-fold duplication), projecting to about 324 GiB instead of 76 GiB
if every prefetched copy coexists. It also submitted one 1,000-task forcing
array and repeatedly re-hashed whole forcing payloads while polling, which
blocked segment finalization and model release despite hundreds of ready
records. Runtime `runtime-v2` fixes this without changing any model runner:
CPU arrays are capped at 32 tasks, record polling trusts the atomic ready
publication, and the existing segment finalizer remains the independent
full-checksum gate before HICAR. The migration report is
`campaign-v1/manifests/controller_runtime_migration_v2.json`; it proves zero
model attempts before migration and that only the controller and campaign
planner differ between runtimes. The terminal 1,000-task array was abandoned
after preserving its 416 completed records. Watcher `4963705` submitted
24 ready segment finalizers as `4963707`; the first five HICAR attempts
were released while finalization continued. At 20:47 CEST the full
11-attempt model capacity (`4963722`--`4963744`, with non-contiguous Slurm
IDs) was queued on `preemptible`, and the next bounded 32-record forcing batch
`4963745` was running concurrently. The preemptible partition was fully
allocated at that instant, so the model jobs were pending for scheduler
priority/resources rather than a workflow dependency. The next Swiss-domain
or production campaign must additionally use a checksum-bound shared
valid-time forcing cache rather than duplicating payloads per chain.

Forty-three attempts have so far received scheduler SIGTERM pre-emption. Each
published `attempt_interrupted.json`, exited `75:0`, and showed no model or
solver failure before interruption; available solver status and residuals
remained within the declared gate. Controller `runtime-v2` classified them as
retryable and submitted fresh immutable attempts.

The exact-RAP audit blocker is repaired without rerunning HICAR.
`evaluate_hicar_solver_log.py` now requires one logged exact-Galerkin hierarchy
declaration and checks the parsed RAP-verification count against its declared
coarse-level count; it no longer assumes nine levels for every
domain/decomposition. Both synthetic eight-/nine-level tests and the two
preserved failing 24-hour logs pass. Immutable `runtime-v3` changes only that
evaluator relative to `runtime-v2` (manifest SHA-256
`75b0db84498ee8377c49d580e3447083f7a234ace224812fcc0c5d36cec6509e`);
its read-only Python environment report SHA-256 is
`6bef5690e89097fbe970b27e1d703be6589f9c9e2d4378d6088a15c9844f827a`.
Migration report
`campaign-v1/manifests/controller_runtime_migration_v3_rap_audit_repair.json`
(SHA-256
`3afba663c80f2d55e39c78bd216f96b91518aab3c0d4bed25978b7decadc291a`)
preserves the pre-repair definition/plan/state, records that no model rerun is
required, clears only the two proven audit blockers, and corrects the stale
definition checksum left by the `runtime-v2` migration.

Replay array `4965010` passed all 12 preserved completions, and subsequent
audits remain clean. The bridge screen completed at
`2026-07-29T04:44:12.584553+00:00`: controller state is `COMPLETE`, and the
checksum-bound `execution/campaign_completion.json` publication is `PASS`.
All 32 chains and 76 restart-linked segments have ready model completions,
solver reports, compression reports, segment-retirement records, and
restart-retirement records; all 156 declared compressed payloads are present,
with no terminal controller errors. Watchers `4965071` and `4965072` completed
successfully, the campaign queue is empty, and rolling retirement reduced the
final scratch footprint to about 252 GB.

The frozen REA-L-only Plateau gate passed before HICAR differences were
inspected: all 24 hours were calm/stable over 2,168 frozen cells, with hourly
median 10 m wind 0.389--0.897 m/s and median lower-300 m
potential-temperature increase 2.83--6.58 K. The completed convergence
assessment then found that every shorter member (0, 1, 2, 3, 6, 12, and 24 h)
fails against the 48 h reference in all four regimes and at every assessed
10--200 m AGL height. All 32 runs pass finite/range physicality, so this is a
trajectory-convergence failure rather than corrupt output. The fail-closed
decision is `HOLD / MINIMUM_SPINUP_NOT_BRACKETED`, selected spin-up is null,
and 48 h is only a tested lower bound; in particular, the proposed one-hour
cold-start discard is rejected. Extend the convergence bracket before running
the Swiss confirmation or 30-day stitched-chain gate.

The authoritative derived decision is
`campaign-v1/analysis/wind_spinup_convergence_decision.json` (SHA-256
`a0f1800fe293cf30b9de99f20aae203b0f1ac6413ac5f52fc5a1d9051fcd2ddf`);
it preserves the immutable source assessment SHA-256
`ed6bd4501b627fce6ca097de83bd3a9aee97dfb20db0c3e2bb9921b93ecbffb4`.
Assessment runtime `assessment-runtime-v4` is checksum-bound and validated
(manifest SHA-256
`bfa0d77a468f66986638cfb7d5b7fe6e3d1c5370f2742697d04cc7429bd0ee36`).
The bounded-open-file assessor completed as job `4967011` in 24:11 on
`pp-short` with eight CPUs and about 2 GB peak RSS; prior unbounded attempts
were cancelled voluntarily after reaching about 13.6 and 33.4 GB.

The follow-up mechanism assessment supersedes “extend the coupled spin-up
bracket” as the next action. Independent conversions of the same REA-L valid
time have bit-identical values for all 18 NetCDF variables; only the global
`history` attribute changes. Across the preserved 24 h and 48 h states,
same-valid-time pressure is exactly identical, while density, potential
temperature, water vapour, PBL height, friction velocity, surface temperature,
and soil state differ materially. The wind difference is domainwide, and its
10 m spatial error is most consistently associated with friction-velocity
differences. HICAR therefore resets the hourly wind target from identical
forcing but applies and integrates it through a different prognostic
thermodynamic/PBL/land/advection trajectory.

A corrected two-hour restart replay crossed an interior hourly wind-update
boundary and compared Sx on/off for the Plateau and Storm Sabine regimes.
Removing Sx changed neither full-level nor 10 m winds above `1e-6 m s-1` and
left the 24 h-versus-48 h difference ratio at 1.0, so Sx is not the mechanism.
The combined `advect_density=False` screen is not scientifically usable with
the qualified adjoint solver: after an explicit diagnostic restart override,
the independent conservation gate rejected `relative_Bq=0.25952` against its
`2e-5` limit. The gate was not disabled. Density-weighted projection consumes
the differing density and remains a plausible propagator, but it was not
independently isolated.

The authoritative fail-closed conclusion is
`campaign-v1/analysis/mechanism-v1/mechanism_conclusion_v2.json` (SHA-256
`f31accc85ffe067bd7202f01fdc0f12820b054ee0f8da42305b3024d9270b3be`);
the corrected pathway report is
`campaign-v1/analysis/mechanism-v1/pathway-v2/pathway_assessment_v2.json`
(SHA-256
`8688bb21bd89b2de40678a3f7709692667ebe4177ab1da8e9715d34e2a5f38bd`).
It rejects coupled independent 26 h segments for production. The next bounded
gate is independent `wind_only` HICAR downscaling at every forcing valid time,
which conditions the terrain projection on source theta/qv/density without a
free coupled trajectory. It must prove same-valid-time invariance and physical
skill before production. Surface wind, hub-height shear, and gust diagnostics
remain separately gated because `wind_only` has no evolved local PBL/land
history. If that gate fails, use observation-calibrated diagnostic
terrain/roughness downscaling anchored directly to ICON winds.

The 2026-07-29 utilization review found two campaign-throughput defects that
must be corrected before production. The bridge jobs request two nodes, but
the experiment definition explicitly retained `model_slots=11` from the
four-node generator template; this capped HICAR at 22 nodes even though the
44-node budget permits 22 two-node slots. Accounting confirms the campaign
reached its configured 11-job ceiling near 23:10 UTC, then declined to three
jobs/six nodes at the 02:10 UTC dashboard endpoint. The decline was input
starvation: the controller permits only one CPU array at a time with two
active tasks, orders solver audit/compression/retirement ahead of forcing,
and still prepares duplicated per-chain records rather than a shared
valid-time cache. Production must derive model slots from node budget and
per-attempt nodes, and must prevent lifecycle work from starving a
shared-cache forcing producer lane.

The future-campaign implementation now enforces those requirements in the
coordinator checkout. The planner publishes one checksum-bound cache index,
maps overlapping segment records to the same valid-time payload, and rejects
initial model-slot underfill. Normal campaigns budget the full 46-node
`preemptible` partition (23 two-node bridge attempts or 11 four-node national
attempts); smaller budgets require `bounded_qualification=true`. The
controller deduplicates producers, interleaves input and lifecycle work at a
default 3:1 task ratio within an eight-way `pp-short` CPU cap, and
reference-retires each
forcing record and cycle cache only after every consumer has safely retired.
Each shared-node worker reserves four CPUs as a roughly 7 GB memory-placement
proxy (measured worker peak about 3.8 GB); publication-safe failures retry
with 8 and then 16 CPUs before the third failure blocks. Programmatic
submissions now fail closed unless their explicit, reviewed partition is
live `UP` and `AllowGroups` contains `ALL` or exact group `s83`; supplemental
`s83opr`/`s83disp` membership is never accepted.
This code is validated locally but is not part of the immutable `runtime-v3`
used by the active experiment; publish and qualify a new immutable runtime
before the next campaign.

The campaign storage budget is approximately 0.4 TiB simultaneous peak; reserve
0.5 TiB until measured compression and restart sizes from the candidate run are
available. Rolling retirement leaves about 0.25 TiB at completion, dominated by
32 final restart files (about 0.17 TiB). Once continuation is no longer needed,
retiring those final restarts reduces the durable scratch result to roughly
0.08 TiB. Total write traffic is about 0.9 TiB and must not be confused with
simultaneous capacity.

## Historical V29 scientific problem

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

These are hypotheses, not causal attribution. The preserved V29 history has
now been stratified by valid time, UTC hour, elevation, and active-soil class,
and joined only to the frozen SwissMetNet, TabsD, SIS, RhiresD, and REA-L
evidence; no model was rerun. The published artifact-only report is:

```text
/store_new/mch/msopr/olifu/icon_downscaling/recovery/v1/artifacts/qualification/v29-summer/diagnostics/v29_surface_regime.json
```

Its manifest and ready marker are present; report SHA-256 is
`3ecb995b4705b5438c351d5377f7870012649b6f71596b9a7ccd41113a1a4c47`.
It ranks a cloud--precipitation--radiative deficit as the strongest symptom
pattern, surface moisture/flux feedback as unresolved, and terrain-horizon
shading as non-dominant: the all-domain HICAR shortwave bias changes by only
`0.662 W m-2` between SIS and SIS-No-Horizon. It does not make a causal claim
or authorize a configuration experiment, because the remaining cloud/
precipitation and surface-feedback alternatives cannot be separated from the
archived evidence alone.

The follow-on archived-namelist trace sharpens that hold: V29 prescribed only
P/T/QV/U/V/W and geometry while using Morrison microphysics and RRTMGP; it did
not map hydrometeors (`qcvar`--`qsvar`) or downwelling SW/LW forcing. The
retained history lacks cloud/hydrometeor state and the frozen reference skill
is aggregate, so it cannot establish whether cloud/radiation error or a
land-surface feedback starts first. The revised report has SHA-256
`3f52ef5d0969f2ab40e7795262f26dae4baeffd8988984fcf79e303e37e2adea` and
records `NOT_SEPARABLE_FROM_RETAINED_ARTIFACTS`; no experiment is authorized.

For the next bounded baseline, the renderer now has an opt-in
`mechanism_diagnosis` output profile. It preserves the V29 physics and
forcing contract while requesting HICAR's internally generated hydrometeors
(`qc/qi/qr/qs/qg`), column cloud fraction (`cldfrac`), direct/diffuse and
cloud radiative terms, radiative tendencies, surface fluxes, and soil state.
It requires the same REA-L land initialization as the qualification profile.
This is instrumentation only; it has not been run and does not authorize a
physics or national sensitivity.

`case_studies/swiss_200m/validation/diagnose_v29_surface_regime.py` is the
artifact-only entry point for the HICAR history portion of that diagnosis. It
requires preserved V29 history, static domain, and frozen assessment JSON and
does not submit or run HICAR. The 200 m renderer now freezes the effective
RRTMGP defaults explicitly and rejects a cold-start `qualification` profile;
these are provenance safeguards, not a changed scientific configuration.

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

The applicable fixes were ported onto the qualified V26 restart baseline at
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

After qualification, `feature/icon_downscaling` was fast-forwarded to this
commit and the coordinator HICAR gitlink was advanced. The redundant
`codex/schnaps-relevant-fixes` ref was removed after reachability was
verified. This is now the single production engineering source line; it does
not include the scientifically failed V29 diagnostics and does not alter the
V29 scientific verdict.

The complete commit classification, deliberate deferrals, executable
checksums, job topology, and comparison metrics are in
`case_studies/swiss_200m/validation/schnaps_upstream_integration_7700c97a.json`.

Keep the GitHub fork as `origin` and the old GitHub project remote unchanged;
track the migrated project with the additional fetch remote:

```text
schnaps https://codeberg.org/SCHNAPS-Model/SCHNAPS.git
```

This source is an engineering production pin, not a scientific production
authorization.

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
- Reproducible Balfrin onboarding implementation:
  `cf75ce18785baa85167f68dbef666eef0fa994b5`.

### HICAR

- Remote: `git@github.com:ofuhrer/HICAR.git`
- Migrated-upstream tracking remote:
  `https://codeberg.org/SCHNAPS-Model/SCHNAPS.git` as `schnaps`.
- Production engineering branch and coordinator submodule pin:
  `feature/icon_downscaling` at
  `7700c97a0248abcc1db055ef04c22e1ff9ec6d22`.
- Qualified restart baseline:
  `codex/restart-noahmp-state-v26` at
  `246c8992564ad3e89f49546776ae31ad8a1ce239`.
- Failed scientific V29 handoff:
  `codex/v29-summer-warm-bias` at
  `5da4b1980497f20468e6e4b5b4c4a584849c3454`.
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
  that the historical `91f917e` release is **not launchable**: it omits
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
- Reproducible Balfrin onboarding and repaired pre-emptible runtime:
  `docs/balfrin-quickstart.md` is the supported operator entry point and
  `config/balfrin.env` is the shared non-secret site-default record. Runtime
  releases now include the namelist template, fieldextra target grid, site
  defaults, canonical builder, and frozen forcing validator. Each runtime gets
  a separate read-only Python environment; reconciliation verifies the
  interpreter hash, exact sorted package inventory, requirements binding, and
  absence of writable paths. Primary wrappers load the central site record
  before modules and require the exported immutable `REPO_ROOT` plus
  published `HICAR_VALIDATION_PYTHON`; they no longer fall back to a mutable
  `$SCRATCH/icon_hicar` runtime or shared `venv_static`.

  A clean GitHub clone at coordinator `cf75ce1` passed the Balfrin preflight
  on `balfrin-ln002`, including the exact HICAR `7700c97a` pin and remote
  branch, `preemptible` partition, REA-L FDB metadata, operational
  fieldextra/ICON-grid assets, scratch, and writable `/store_new`. Preflight
  report SHA-256:
  `1d0cd2c8212557f9e1555985daa9072c7e1653e20d2093d913335ddeda5466d2`.

  Clean production runtime manifest
  `coordinator-cf75ce1-v1` has SHA-256
  `4da64d2c6f18b5d747ed9b3fc6dfb74f887841e2682339fa7b18362727b4e0cd`.
  Python bootstrap job `4951249` published a schema-v2 immutable-environment
  report with SHA-256
  `8ee6bd6b2f29a96d927efc9abaccc63b95b1aeb19fbdaf091889984d26fcf7d3`
  and passed independent runtime revalidation.

  The recovery restore helper reproduced the summer initialized static domain
  from `/store_new` at its canonical SHA-256
  `dc949651c18f9c30e6d18419ba4935ce5d100493c594179da44bcb2942e14ef0`.
  Canonical GPU/NCCL build job `4951256` passed at HICAR `7700c97a`;
  executable SHA-256:
  `756763fa439b2c642c948ccc8f20e4b29eeef91b96f0088ee751bb1018ca5eec`;
  published build-provenance SHA-256:
  `35f58d66a7930520fd10e7e315a58e420a53ca25af1eec1050cf8820cf2997cc`.

  The generated two-hour, one-chain plan has SHA-256
  `d9b548ddef3d7a62c4ed5520aea8fffa77ee7889ba14180ecebe8046ee4c911c`.
  Dry reconciliation selected one bounded three-record CPU array followed by
  a four-node `preemptible` model attempt; no Slurm job was submitted.

  Coordinator `a39bbb5457a6f1eaff38dc642638c741799aa4c4` now contains the
  repaired target-stack qualification path. It adds exact predecessor restart
  provenance to model completion, campaign-specific multi-chain
  authorization, report readiness for compressed output, a two-segment real
  HICAR TERM/KILL recovery driver, nonblocking signal-aware model-output
  forwarding across the Slurm batch-shell boundary, and unlimited-by-default
  retries only for explicitly retryable scheduler outcomes. Pending attempts
  count against the global model-slot and node budgets: the controller leaves
  bounded work eligible and Slurm starts it as nodes become available,
  without a separate free-node poller or fixed retry delay. Development on
  `main` remains mutable; the read-only runtime is only a per-campaign
  execution snapshot.

  Balfrin supplied real daytime pre-emption evidence during the engineering
  runs: jobs `4951991`, `4952023`, `4952291`, and `4952336` exited through the
  signal wrapper with retry code `75`, new immutable attempts were submitted,
  and job `4952375` later completed and published a validated national
  one-hour checkpoint. Those runs also exposed and preserved failed evidence
  for an over-scoped scientific-output gate, a missing compression-report
  ready marker, and `scancel --full` bypassing wrapper publication; each issue
  was corrected rather than waived.

  Final runtime `coordinator-a39bbb5-v1` has manifest SHA-256
  `250b16df447310e94168fd98c08fc6b172259d5dfd82519e30417d9efcd3ec28`;
  its read-only Python environment report SHA-256 is
  `30a4a06ba8b03e8dd567028cff89e2e30cc210e979b045d33c8277d257046740`.
  The shared batch-shell TERM/KILL probe passed under that runtime. The real
  two-segment, four-node HICAR qualification at
  `/scratch/mch/olifu/icon_hicar/engineering/hicar-preemption-recovery-a39bbb5-v1`
  also passed: controller `4953041`, predecessor `4953049`, TERM `4953076`
  (`75:0` with interruption report), KILL `4953079` (`0:9` without cleanup),
  and successful restart retry `4953123`. Its non-promoting report SHA-256 is
  `9af88ff0ea621f3cbd248e93a777952ecbc1b6021d2015af12bf2f45cf93f9d8`;
  campaign-completion SHA-256 is
  `15d6f53225c4483c1b31c2c73e772b953ab03dcbec2ed8572bc40f27aa902365`.
  The report is `ENGINEERING_ONLY`, `promotion_eligible=false`, and
  `scientific_authorization=false`; it qualifies recovery mechanics only and
  does not authorize a first-year campaign.
- Wind-climatology engineering pathway:
  `case_studies/swiss_200m/wind_climatology/PRODUCT_CONTRACT.md`.

## Historical production authorization state (deferred)

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

## Historical bounded goal (superseded by project-assessment.md)

No configuration experiment is currently authorized. Before proposing one,
isolate a testable mechanism with evidence that distinguishes the retained
cloud/precipitation/radiation and land-surface feedback alternatives; preserve
the frozen summer thresholds and do not submit a national simulation. Only
after a mechanism-based improvement may the frozen summer event be repeated;
winter and the independent hour-48 overlap remain conditional on an unchanged
summer pass.

### REA-L FDB runtime status

`fdb/5.18:v1` is the qualified project runtime with the currently installed
`uenv 5.2.0-dev` client. Its `rea-l-ch1` view passes `fdb-info` and a narrow
REA-L `T_2M` read for 2010-01-01 00 UTC (1147980 grid points, parameter
500011). The project pin and smoke-test default use this verified image.

The newer `fdb/5.19:v2` and currently recommended `fdb/5.21:v1` images have
been pulled but are not project runtimes: the same client fails before command
execution with `KeyError: 'activate'` when activating their newer nested view
metadata. This upgrade is deferred and does not block the diagnostic baseline;
do not advance the pin until a candidate passes the same two checks.

### V29 REA-L mechanism-diagnostic sidecar

The additive V29 REA-L sidecar has been regenerated and published without
rerunning HICAR. It contains all 25 three-hourly records from 2020-07-01 00
UTC through 2020-07-04 00 UTC, with cloud cover, direct/diffuse downwelling
shortwave, downwelling/net longwave, sensible/latent heat, and rain/snow/
graupel intervals alongside the existing source reference. The collection
finalizer verified 25/25 records, all hashes and ready markers, and the full
diagnostic set on each non-initial record.

The durable payload is
`/store_new/mch/msopr/olifu/icon_downscaling/recovery/v1/artifacts/qualification/v29-summer/diagnostics/rea_l_surface_diagnostics_v1.tar`
(SHA-256
`74372d70736d7ba844a0c41e4b2d8cec6766695d0fc4c9b2e366d5367ac83647`),
published by manifest
`/store_new/mch/msopr/olifu/icon_downscaling/recovery/v1/manifests/archive-v29-surface-diagnostics-v1.json`.
The sidecar is non-promoting evidence only. It reconstructs interval fluxes
from cycle endpoints. Independently regridding endpoint fields can create
bounded negative shortwave leakage near the zero-radiation contour; values no
more negative than 15 W m-2 are clipped and their per-record extent is
recorded, while larger values fail publication. This reference-only handling
does not alter HICAR forcing, physics, or the frozen summer gate.

### Historical instrumented mechanism baseline

The predeclared 24-hour diagnostic baseline contract is
`case_studies/swiss_200m/config/v29_mechanism_diagnosis_contract.json`:
Switzerland at 200 m, 2020-07-01 00 UTC to 2020-07-02 00 UTC, 80 levels,
REA-L land initialization, unchanged V29 physics/forcing/numerics/terrain,
and three-hourly `mechanism_diagnosis` output only.  The output profile adds
cloud, hydrometeor, radiative, turbulent-flux, and soil-state observability;
it is not a physical sensitivity or a production profile.  The stream runner
and validator explicitly support and range-check this profile.

For interpretation, matched instantaneous `cloud_area_fraction` is the
strongest reference comparison.  HICAR direct/diffuse downwelling shortwave
and upward sensible/latent heat flux have matching physical conventions to
the REA-L sidecar but differ in snapshot versus ending-interval-mean timing,
so they cannot establish lead/lag.  HICAR `lwtr` is net downward longwave,
whereas the sidecar longwave is downwelling; it is not a direct comparison.

At HICAR production pin `7700c97a`, the formerly omitted wind
`alpha_const` resolves to `1.0`.  The renderer now writes that effective V29
value explicitly, so future reruns preserve the baseline physics rather than
depending on an upstream default.  This source-maintenance change does not
alter the already submitted diagnostic namelist.

An isolated engineering runtime snapshot is published at
`/scratch/mch/olifu/icon_hicar/mechanism_diagnosis/v1/runtime`, bound to
coordinator `0546424984cd24901fbc6154de23d81a2d543ae9` plus its recorded
diagnostic overlay.  Its clean HICAR checkout is the production pin
`7700c97a0248abcc1db055ef04c22e1ff9ec6d22`; it uses the provenance-recorded
GPU/NCCL executable
`/scratch/mch/olifu/icon_hicar/onboarding/build/hicar-7700c97a-cf75ce1-gpu-nccl-v1/HICAR_gpu`
(SHA-256 `756763fa439b2c642c948ccc8f20e4b29eeef91b96f0088ee751bb1018ca5eec`).

The exact 25-record hourly forcing plan is published at
`/scratch/mch/olifu/icon_hicar/mechanism_diagnosis/v1/forcing_20200701/chunk_plan.json`.
Its two-concurrent `pp-short` producer array is job `4953959`; it uses the
verified `fdb/5.18:v1` runtime.  Do not submit the HICAR baseline until this
forcing publication is PASS and all 25 ready markers validate.

The forcing array and aggregate publication subsequently passed.  The first
baseline submission (`4953985`) failed during startup because the immutable
runner linked Noah-MP/RRTMGP support assets from a missing `HICAR/run` tree;
it produced no scientific output.  Runtime `v2` is a new checksum-bound
engineering release with an explicit, preflight-validated
`HICAR_RUNTIME_SUPPORT_DIR`, and has its own published read-only Python
environment.  Retry `4953990` uses the same frozen forcing, static domain,
production executable, and physics, with support assets from the clean
production-commit checkout.  It is the only authorized diagnostic model run;
await its completion manifest and the non-promoting mechanism assessment
before proposing any correction or seasonal rerun.

The forcing finalizer `4953984` is dependency-bound to that array.  The only
authorized instrumented baseline, `4953985`, is dependency-bound to the
finalizer and therefore cannot start unless the aggregate forcing publication
passes.  Its immutable submission receipt is
`/scratch/mch/olifu/icon_hicar/mechanism_diagnosis/v1/baseline_submission.json`;
it binds the forcing plan, REA-L static initialization, engineering runtime,
clean HICAR `7700c97a`, and the recorded GPU executable hash.  It is a
24-hour non-promoting diagnosis, not a frozen 72-hour correction gate.

The forcing array and finalizer have now passed: 25/25 records and
6,901,654,411 bytes are published in
`forcing_20200701/forcing_publication.json` with its ready marker.  Job
`4953985` is running on the four-node national GPU layout.  Do not submit any
other physics run while it is active.

Job `4953985` failed after 25 seconds before model initialization because the
fresh source clone lacked build-generated Noah-MP/RRTMGP/microphysics support
assets at the runner's assumed `run/` location.  This is an engineering
staging failure, not a scientific result.  Engineering retry `4953990` uses
the independently clean exact-commit source
`/scratch/mch/olifu/icon_hicar/onboarding/clean-clone-6de6366/HICAR`, which
contains those assets and remains at `7700c97a`; all scientific inputs and the
executable hash are unchanged.  The failed and retry submission evidence is
in `baseline_submission.json` and `baseline_retry1_submission.json`.

Retry `4953990` completed the 24-hour model integration, but its publication
validator rejected the output before creating a ready marker: its all-cell
soil range check found `soil_water_content=1.0` and
`soil_column_total_water=1500 kg m-2`.  This is a validation-scope defect, not
evidence of a new model instability: the existing qualification validator
already excludes USGS water (class 16) and permanent snow/ice (class 24),
whose Noah-MP pseudo-soil is saturated and is not a soil-hydrology diagnostic.
The mechanism profile now applies that same static active-soil mask only to
its three soil fields, retaining all-cell checks for the cloud, radiation,
flux, and hydrometeor evidence.  The dependent assessor `4954188` is
`DependencyNeverSatisfied`; do not interpret the unready model output or
submit another physics run.  The next authorized action is an immutable
post-processing revalidation of this exact output/restart with the corrected
scope, followed by a newly dependency-bound non-promoting assessor.
That revalidation is job `4954484` on `pp-short`, using immutable release
`mechanism_diagnosis/v1/validation-v3` (manifest SHA-256
`30c5f4a6eb086282faa33a1ebffd3d7d1cf0ac93e467a0d7129d9bee7e9bed75`).
It reads only the completed output/restart and cannot execute HICAR.  Its
replacement assessor is `4954485`, dependency-bound after its PASS; submission
evidence is `baseline_revalidation_v3_submission.json` with a ready marker.
Revalidation passed with the same output SHA-256
`5cdb1b14b487a732fcc0670d8966d96b343dda985278014a31c9087d84fc568b`;
the active-soil maxima are `0.4872206` and `738.243 kg m-2`.  Its first
assessor (`4954485`) failed before a report because it incorrectly applied the
200-m HICAR mask (1651x2271) to native REA-L sidecars (320x640).  Assessment
release `v2` instead preserves separate native-grid spatial means (HICAR
active-land mean and cosine-latitude-weighted finite REA-L-grid mean), labels
them non-pointwise, and is job `4954504`.  This is post-processing only and
does not alter the model output or authorize a rerun.

Assessment `4954504` passed and published
`baseline_20200701_retry1/mechanism_assessment_v2.json` with its ready marker
(HICAR history SHA-256 unchanged).  It confirms a persistent native-domain
HICAR cloud-cover deficit versus REA-L (−0.074 at 03 UTC, increasing to
−0.237 at 00 UTC) and vastly weaker HICAR rain intervals (for example,
0.0017 versus 0.121 kg m-2 at 06 UTC and 0.0035 versus 1.991 kg m-2 at
18 UTC).  This does **not** identify a leading mechanism: REA-L has no
matched soil-state history, and the HICAR flux snapshots versus REA-L
ending-interval means cannot establish lead/lag.  The predeclared decision is
therefore **INCONCLUSIVE / HOLD**: do not alter V29 physics and do not submit
the 72-hour summer rerun.  Any future diagnostic must add time-matched
reference land-state history and/or common-grid time-resolved flux evidence
before authorizing one correction.

### Historical causal-resolution work

The active objective is to unblock the V29 scientific hold with a causal
comparison and one evidence-selected correction, rather than accepting another
native-grid observational verdict. The first required input is a time-matched
REA-L land-state history (`SKT`, `T_SO`, `W_SO`, `W_SNOW`, and `RHO_SNOW`) at
the nine 3-hour endpoints from 2020-07-01 00 through 2020-07-02 00 UTC. These
fields use the existing qualified depth-remapping contract; they are source
diagnostics, not a HICAR forcing or physics change.

The first source-only array `4956101` failed before FDB access because its
read-only runtime was incorrectly also used as a temporary-work root; dependent
finalizer `4956102` was cancelled. This is an engineering staging failure, not
a source or scientific result. Runtime `v3` moves temporary work explicitly
outside its read-only tree and is checksum-bound at
`/scratch/mch/olifu/icon_hicar/mechanism_diagnosis/v3/runtime` (hash inventory
`runtime.sha256`). It was submitted as source-only `pp-short` array `4956112`
(two concurrent tasks) with dependent finalizer `4956113` on 2026-07-28. All
nine extraction tasks passed. Its original finalizer incorrectly required
zero-byte ready markers to be nonempty; replacement finalizer `4956124` in
immutable runtime `v4` passed without rerunning the extracts. The resulting
publication is
`mechanism_diagnosis/v3/rea_l_land_history/rea_l_land_history_publication.json`
with its ready marker and nine payload hashes. It is now valid source evidence
for the causal assessor. No HICAR model job, physics correction, summer rerun,
or seasonal escalation has been submitted by this action.

The remaining observation gap is HICAR's snapshot surface fluxes. The renderer,
stream runner, and validator now support the instrumentation-only
`causal_surface_30min` profile: fixed 1800-s output, only two-dimensional
cloud/radiation/flux/land fields, and no 80-level hydrometeor histories. It
preserves V29 physics, forcing, terrain, and numerics while making a
three-hour composite flux mean reconstructible from seven endpoint samples.
The profile is not yet submitted; its immutable causal contract and
common-grid assessor must be frozen before the one permitted instrumented
baseline is launched.

`config/v29_causal_resolution_contract.json` now freezes that baseline and
the deterministic next action. `validation/assess_v29_causal_resolution.py`
maps active HICAR cells to the REA-L grid, cosine-area-weights the common
mask, aligns endpoint land/cloud states, and reconstructs HICAR three-hour
flux means from seven 30-minute samples. Its decision is either a cloud-path
correction, a land-path correction, or a required predeclared discriminating
pair—never an observationally inconclusive scientific verdict. The baseline
and assessor have not yet been submitted or used to authorize a correction.

The causal runtime was copied from the validated V29 coordinator, overlaid only
with the causal contract/profile/validator/assessor, given an independent
read-only Python environment, and checksum-frozen by `pp-short` job `4956169`
(PASS). The predeclared four-node national baseline is job `4956200` on
`normal`, submitted on 2026-07-28. It uses the unchanged V29 forcing plan,
static initialization, clean HICAR source commit, executable, support assets,
and physics; the only model difference is `causal_surface_30min` output.
The 24-hour causal baseline `4956200` passed publication validation (49
30-minute records, complete 00:00--00:00 coverage, checksum-bound forcing,
static domain, executable, and clean HICAR source). Its first assessor job
`4956526` failed only because it supplied the first of three deliberately
segmented history files. The read-only segmented-history assessor release
`causal_resolution/v2/runtime` corrected that engineering defect; its
non-promoting replay `4956970` passed and published
`causal_resolution/v1/baseline_20200701/causal_resolution_assessment.json`.

The common-grid, time-aligned result is decisive:
`CLOUD_PATHWAY_CORRECTION`. HICAR has persistent negative cloud and rain
anomalies by 03 UTC, whereas the first land-divergence threshold is at 15 UTC.
The selected one-change correction is recorded in
`config/v29_cloud_pathway_correction_contract.json`: Morrison to Thompson
microphysics only; forcing, land initialization/model, radiation, terrain,
numerics, output cadence, and frozen summer thresholds remain fixed. Job
`4957030` was the resulting 24-hour, non-promoting Thompson validation; it was
submitted with a checksum-frozen renderer wrapper that proves the sole
namelist substitution.  Balfrin externally cancelled it after it reached
07:07 UTC simulation time, before the required 24-hour completion; it created
neither a completion publication nor a ready marker.  Its dependent `pp-short`
common-grid assessor `4957277` was therefore cancelled by `afterok`, and no
runtime or scientific conclusion is permitted.  The live comparison
nevertheless establishes a material performance question: the
otherwise matched Morrison baseline `4956200` completed its full 24 hours in
`1:39:55`, while Thompson job `4957030` had elapsed `4:14:29` at only 05:30
UTC.  Both use four normal nodes, 16 GPUs/20 ranks, the same executable,
forcing, static domain, variational wind solver, 30-minute causal output, and
nearly identical timestep (Morrison 3.7818 s; Thompson 3.77 s).  This points
to cost within the selected Thompson microphysics path rather than a smaller
timestep or changed runtime topology; confirm with final HICAR timers after
the frozen scientific run completes.  Do not perturb this run for profiling.
No summer gate may start until the read-only
`causal_resolution/v3/thompson_20200701/acceptance_criteria.json`
(`sha256=61e95cd0bb82a89fa1553436d961f7cfcb0a70fbf17b2df6b4895149d0cd4387`)
is met: at both 06 and 09 UTC the Thompson run must strictly reduce the
absolute common-grid cloud and rain deficits from the frozen Morrison
baseline, with a `PASS_NON_PROMOTING` cloud-pathway report and a valid model
publication. Failure falsifies this single mechanism-specific correction;
it is not an inconclusive scientific result and does not authorize the summer
gate.

The promotion decision is not manual: the immutable
`causal_resolution/v4/runtime` gate-assessment release records an auditable
`AUTHORIZE_FROZEN_72H_SUMMER_GATE` or `REJECT_CLOUD_PATHWAY_CORRECTION` result
after checking the Thompson publication and the two endpoint comparisons. Its
negative-control execution against the unchanged baseline correctly rejected
promotion.

The immutable v4 post-assessment gate job `4958080` is dependency-bound after
`4957277`; it reads only the Thompson model publication and causal report and
will publish `AUTHORIZE_FROZEN_72H_SUMMER_GATE` or
`REJECT_CLOUD_PATHWAY_CORRECTION`. It does not invoke HICAR.

The historic summer specification is archived at
`/store_new/mch/msopr/olifu/icon_downscaling/recovery/v1/artifacts/qualification/v29-summer/chunk_plan.json`
(2020-07-01 00 UTC, 72 h, 73 forcing records), but its scratch forcing payloads
have been retired. Therefore an authorized Thompson summer gate must first
regenerate and publish a new 72-hour checksum-bound forcing bundle from the
same REA-L period; it must not reuse the old model output or treat absent
scratch paths as a published input.
seasonal job has been submitted.

## Cleanup state

Cleanup is complete and recorded in
`recovery/cleanup_handoff_v29.json`.

The HICAR remote now retains only `main`, `feature/icon_downscaling`, the
qualified V26 restart reference, and the failed-but-reproducible V29
scientific reference. The three superseded solver-research branches and the
redundant selective-integration topic ref were deleted after reachability or
bundle/source verification. That cleanup left HICAR clean at `7700c97a`;
the subsequent wind-output candidate is described above.

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
- Current onboarding/pre-emption-focused regressions: PASS, including
  immutable runtime leaves, Python tree/package drift rejection, site
  preflight, checksum-bound restore, bounded campaign generation, eleven-slot
  empty-capacity submission, full-cluster pending-job backpressure,
  interruption-resumable retirement, and SIGKILL retry classification.
- Current portable coordinator suite: `297 passed`; repository checks PASS
  (`169` Python and `150` shell files). A fresh GitHub clone at coordinator
  `ef3949e8b32c8149e3409a821d4a5e10d3271314` on `balfrin-ln003` passed the
  corrected site-config-first preflight, including live REA-L FDB metadata,
  production HICAR pin `7700c97a`, `preemptible`, shared fieldextra/ICON
  assets, scratch, and `/store_new`. Preflight report SHA-256:
  `6066c402b116de37847c08eddc210e1a1eaed252b93ec88219757086ea3c850e`.
  Operator Markdown links and code fences pass the repository documentation
  audit.
- Public coordinator CI run `30265943868` for `ef3949e`: PASS.
- The opt-in HICAR source-contract gate is intentionally `4 passed, 7
  failed` at production pin `7700c97a`: the qualified V26 restart
  initialization contract passes, while metadata/water-diagnostic checks
  describe the scientifically failed V29 and other unmerged output work.
  They remain visible and must not be xfailed or weakened.
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
- The HICAR submodule is clean on `feature/icon_downscaling` at wind-output
  candidate `b5146a3c`; the qualified project-wide production pin remains
  `7700c97a`, and V26/V29 remain evidence branches rather than the default
  production source.
- The conservative full-deletion recovery audit remains intentionally
  `NOT READY`: the annual production archive contract is unresolved and
  durable payload verification must run on Balfrin where `/store_new` is
  mounted. This does not invalidate the completed, explicitly scoped scratch
  cleanup.
