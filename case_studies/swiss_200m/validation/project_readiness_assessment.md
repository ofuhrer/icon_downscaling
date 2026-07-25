# REA-L-CH1 to HICAR hectometre downscaling readiness

Assessment date: 2026-07-25

## Decision

The project is not ready for a 20-year scientific production campaign.

The 200 m workflow is engineering-capable, but its first seasonal scientific
gate exposed a sustained-runtime accelerator failure: the summer event
reached 52 simulated hours with clean numerical gates and then stopped in
the RRTMGP cloud-fraction GPU kernel. An isolated level-local repair passes
local tests, a target-stack build, the four-GPU unit/halo suite, and a
production-physics smoke. The 701-by-701-cell 200 m regional bridge passes,
and a fresh 72-hour national recovery is running with exact 24-hour
checkpoints. Its first 42.37 GB checkpoint passes exact-time, source,
80-level schema, and whole-file checksum validation after the model advanced
beyond the boundary; the sustained national gate is not yet complete. The prepared
winter event and all physical, REA-L,
SwissMetNet, OGD, solver, and paired verdict stages are held behind that
recovery. No event verdict exists yet.

The actual national 100 m DEM now passes the independent 80-level SLEVE
geometry gate, but the 64-GPU initial-solve, memory, restart, and throughput
case remains unexecuted. Its planner is gated on a passing paired-event
verdict and cannot authorize science or production.

## Readiness scorecard

| Area | 200 m | 100 m | Evidence and remaining condition |
|---|---|---|---|
| National wind solver and geometry | Ready for pilots | Geometry only | Six-hour 200 m national and current event initialization gates pass. The actual 100 m grid passes Jacobian/thickness/spacing limits, but still needs its initial solve and capacity run. |
| Hourly REA-L forcing stream | Ready for pilots | Gate planned | Live 200 m FDB-to-HICAR records take 61–85 s with bounded transient storage. The 100 m two-segment gate will measure conversion rather than extrapolate it. |
| Restart segmentation | Engineering-qualified | Gate planned | Two consecutive 200 m segments and their common boundary passed, and the repaired national recovery has published a validated 24-hour checkpoint. The month design adds the required 24-hour post-boundary restarted-versus-uninterrupted trajectory comparison; 100 m has an exact-end two-segment gate. |
| Land and snow initialization | Event-qualified input | Not demonstrated | Exact REA-L soil/snow states pass summer and winter construction plus a national HICAR consumption smoke. Event behavior, spin-up, and year-chain equivalence remain unscored. |
| Physical output validation | Recovery gate active | Not started | The qualification profile and bounded class/budget evaluator pass a smoke. The first summer event failed in radiation after 52 hours; its repair now passes the hard regional bridge and is undergoing the national-duration recovery. No month or annual result exists. |
| Independent observations | Access and code ready; scores pending | Not ready | SwissMetNet plus public RhiresD, TabsD, SIS, and SIS-No-Horizon are reproducibly packaged. Event comparisons are queued; application limits and weights remain deliberately unresolved. |
| Long-duration stability | Decision gate implemented; evidence absent | Not ready | The annual assessor now enforces seasonal coverage, restart and initialization equivalence, drift attribution, absolute and relative quality, recovery, archive restore, and release immutability. No annual national run exists; published HICAR evidence remains seasonal and catchment-scale rather than multi-year at 100–200 m. |
| Routine output and archive | Partly ready | Planning only | Routine and fixed-height wind products, lossless compression, and rolling retention work. The visible `msclim` tape plan is smaller than the 200 m campaign estimate; no project-owned destination or restore drill is approved. |
| Capacity and cost | Measured planning base | Scaling estimate | The 20-year 200 m estimate is about 36.9k node-hours, 147.5k GPU-hours, and 13.3–21.2 TiB compressed routine output. The 100 m estimate is about 295k node-hours, 1.18M GPU-hours, and 53.0–84.8 TiB. Twenty simultaneous year chains would require 80/320 nodes at 200/100 m and cannot fit the 46-node `normal` partition. Even with continuous near-exclusive access, capacity waves give optimistic lower bounds of 30.7–57.6 days at 200 m and 288–576 days at 100 m. The seven-day 100 m base chunk is 17.7 h and its upper uncertainty exceeds the 24 h job limit, so the live benchmark may force shorter segments. |
| Release reproducibility | Needs consolidation | Needs consolidation | The accepted national solver is on a research branch substantially ahead of the production-performance branch; the coordinating repository itself lacks a frozen release commit. |

## Most important gaps

1. **Qualify the radiation repair, then complete the paired event verdict.**
   Require parent-versus-fix equivalence, the hard regional bridge, and a
   fresh 72-hour summer run with recoverable checkpoints. Only then run the
   winter event, solver audits, budget/source comparisons, station/OGD
   comparisons, and the frozen paired decision. The month and 100 m capacity
   planners must remain blocked until that stack passes.

2. **Long-duration scientific qualification.** HICAR's atmosphere is strongly
   constrained by the driving model, which limits free large-scale drift, but
   soil, snow, canopy, surface fluxes, and hydrometeor stores can retain
   systematic bias or drift. A stable solver is not evidence of a stable
   climate downscaling.

3. **Independent validation and acceptance thresholds.** Agreement with the
   REA-L source only checks transformation and broad consistency. It cannot
   establish added value at 200 m or 100 m. Thresholds must be frozen before
   the annual stage for temperature, precipitation, wind, radiation, snow,
   water balance, and energy balance, stratified by elevation, exposure,
   land-cover class, boundary distance, and season.

4. **Year-chain initialization equivalence.** REA-L's skin temperature, soil
   temperature, integrated soil water, SWE, and snow density are now mapped
   reproducibly into HICAR. This makes independent year chains possible in
   principle, but they remain scientifically unacceptable until an overlap
   shows that their retained statistics converge to a continuous reference
   after a characterized spin-up.

5. **A real 100 m capacity case.** Fourfold cell count alone is not enough to
   predict GPU memory, decomposition quality, wind convergence, SLEVE
   geometry, I/O, or forcing throughput in the steeper terrain resolved by
   the national 100 m grid.

6. **Output and archive design.** The qualification profile is intentionally
   larger than production. Layered soil output activates a repeated
   three-dimensional coordinate and is too expensive as a routine 20-year
   product. Sparse checkpoints or online aggregates are needed, together
   with a named durable archive and a restore/reprocess policy.

   The current 2026 `msclim` tape plan is 10 TB, below the estimated
   13.25–21.20 TiB compressed 200 m routine output plus 0.77 TiB of annual
   checkpoints. A data owner and CSCS division SPOC must approve a larger
   envelope and a measured restore drill.

7. **Release consolidation.** A production campaign needs one immutable
   source revision, executable checksum, configuration contract, validation
   suite, environment description, and campaign manifest. Validated research
   history should be consolidated without reintroducing retired solver paths.

## Goal and staged promotion path

**Goal:** qualify HICAR for long-duration REA-L-CH1 downscaling by completing
a staged 200 m scientific pilot with REA-L-derived land/snow initialization,
restart-equivalent streaming execution, physical and budget diagnostics, and
independent reference validation, ending in an explicit go/no-go decision for
a seasonal or 20-year campaign and for a 100 m capacity qualification.

Promotion order:

1. Complete 72-hour summer and winter event pilots. Require all numerical
   gates, class-aware physical ranges, source comparisons, budget diagnostics,
   and independent station/gridded comparisons.
2. On a passing paired verdict, run the bounded 100 m engineering-capacity
   gate and the five-segment 31-day 200 m pilot. Neither authorizes
   production.
3. Use the event/month evidence to freeze application-specific absolute
   observation limits and metric weights. In parallel, approve a durable
   archive allocation and complete a measured transfer/restore drill.
4. Run one annual seasonal cycle, including an independently initialized
   winter and summer overlap and four restart-trajectory boundaries. The
   frozen assessor requires 2,929 model records and at least 95% station,
   TabsD, RhiresD, and SIS coverage within every season. Only this stage can
   authorize parallel yearly chains.
5. Freeze the production release, output contract, archive destination,
   checkpoint policy, and campaign recovery procedure before launching the
   20-year calculation.

## Terrain treatment

The accepted 200 m solver works with the current unsmoothed national terrain,
so smoothing is not a prerequisite for solver convergence. At 100 m, however,
grid-scale slopes and sub-grid terrain variance can exceed the validity of the
vertical coordinate and surface parameterizations even when the linear solver
converges.

If a terrain sensitivity is needed, implement it in the external static-domain
pipeline so topography, land/sea classification, boundary relaxation, and
derived static fields remain consistent. Use a scale-selective filter with a
documented response and preserve the large-scale orographic mean. A repeated
normalized 1-2-1 smoother is unsuitable as the default because it also damps
resolved larger scales. Compare at least the unfiltered baseline and one
scale-selective candidate; judge them by geometry, solver, precipitation,
temperature, wind, and snow behavior rather than slope alone.

## Limitations that cannot be engineered away

- HICAR cannot create synoptic information absent from REA-L-CH1. Hectometre
  structure is model-generated and remains conditional on the approximately
  1 km forcing and its biases.
- A 100–200 m grid does not resolve all Alpine turbulence, canopy, building,
  glacier, snow-transport, and convective processes. Parameterization error
  remains and may become more visible as grid spacing shrinks.
- Surface classifications and DEMs represent different epochs and effective
  scales. Local realism cannot be guaranteed merely by increasing horizontal
  resolution.
- Boundary relaxation and strongly constrained large-scale winds limit the
  independence of the interior solution. Added value must therefore be shown
  for the intended applications, not assumed from finer maps.
- Published HICAR studies show useful seasonal snow-pattern skill but also
  elevation-dependent precipitation, nocturnal temperature, albedo, thermal
  wind, and melt biases. Calibration can redistribute these errors but cannot
  prove structural correctness outside the validation period.
- The estimated 100 m campaign is intrinsically expensive. Even a successful
  benchmark does not remove the roughly eightfold compute and fourfold output
  increase relative to 200 m. Balfrin `normal` currently has a 24-hour job
  limit; a seven-day 100 m segment is viable only if the live benchmark stays
  below that limit with operational margin.
- Balfrin has 46 `normal` nodes, so the nominal “one year per chain, all years
  at once” schedule is physically impossible at either resolution. The
  capacity-wave calendar estimates assume uninterrupted, nearly exclusive
  partition use and exclude queueing, retries, maintenance, archive transfer,
  and competing production; operational elapsed time can only be longer.

## How established regional systems reduce risk

Full regional NWP/reanalysis systems generally combine scale-aware orographic
filtering, analysis cycling, land/snow initialization or assimilation, lateral
boundary forcing, and long spin-up/overlap validation. REA-L-CH1 itself uses
daily 24-hour cycles and daily snow analysis, with cold-start and soil-spin-up
strategies across its production periods. That cycling strategy is a warning
against treating one generic HICAR cold start as adequate for twenty years.

For this project, the closest practical analogue is not to turn HICAR into a
data-assimilating NWP system. It is to initialize each candidate chain from
REA-L, quantify the discontinuity and spin-up against a continuous HICAR
overlap, retain only qualified statistics, and use external observations to
test whether the finer simulation adds useful information.

## Canonical evidence

- `production_6h_qualification_4927314.json`
- `rea_l_20year_resource_estimate.json`
- `streaming_forcing_qualification_20200701.json`
- `rea_l_land_state_inventory.json`
- `rea_l_land_initialization_20200701_0000.json`
- `rea_l_land_initialization_20200115_0000.json`
- `rea_l_land_initialization_smoke_20200701_00_02.json`
- `land_init_smoke_surface_classes.json`
- `radiation_cloud_fraction_recovery_qualification.json`
- `restart_checkpoint_validator_smoke_20200701_02.json`
- `radiation_cloud_fraction/live_restart_20200702_0000.json`
- `../config/scientific_pilot_plan.json`
- `../config/observational_validation_contract.json`
- `../config/production_archive_contract.json`
- `assess_scientific_annual.py`
- `ogd_grid_reference_2020_events.json`
- `../../swiss_100m/validation/sleve_geometry_80l.json`

Literature basis:

- Berg et al. (2024), HICAR seasonal catchment simulations,
  <https://doi.org/10.3389/feart.2024.1393260>
- Reynolds et al. (2024), coupled HICARsnow seasonal simulations,
  <https://doi.org/10.5194/tc-18-4315-2024>
- Horak et al. (2019), decade-scale base-ICAR precipitation at 4 km,
  <https://doi.org/10.5194/hess-23-2715-2019>
- REA-L FDB access,
  <https://meteoswiss.atlassian.net/wiki/spaces/IW2/pages/829947927/REA-L-CH1+FDB>
- REA-L setup and spin-up notes,
  <https://meteoswiss.atlassian.net/wiki/spaces/APN/pages/232849544/Notes+on+REA-L-CH1+setup>
- REA-L output requirements,
  <https://meteoswiss.atlassian.net/wiki/spaces/APN/pages/234029121/Output+of+REA-L-CH1>
- Approved MeteoSwiss archiving and retention guidance,
  <https://meteoswiss.atlassian.net/wiki/spaces/UA/pages/1697382577/Archiving+Retention+Guidelines>
- CSCS resource planning for 2026,
  <https://meteoswiss.atlassian.net/wiki/spaces/CSCS/pages/1250197512/CSCS+Resources+2026>
