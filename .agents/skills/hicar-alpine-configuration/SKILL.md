---
name: hicar-alpine-configuration
description: Choose and validate HICAR namelist, vertical-grid, terrain-coordinate, wind-solver, and physical-quality settings for 100-250 m Alpine downscaling. Use when designing experiments, tuning wind downscaling, diagnosing upper-level vertical velocity, selecting model top or SLEVE decay, or scaling from a small domain toward Switzerland.
---

# HICAR Alpine configuration

Treat every configuration choice as a hypothesis: change one parameter family
at a time, retain an appropriate control, and analyze the causal contrast
before scaling.

## Established experimental baseline

For 200--250 m wind-focused Alpine experiments, use this as the best-supported
starting point unless the experiment deliberately tests one of its choices:

```text
auto_level = 1
nz = 80
model_top_height = 12000 m
height_lowest_level = 15 m
stretch_fac = 0.65
terrain_smooth_windowsize = 5
terrain_smooth_cycles = 10
decay_rate_L_topo = 2.0
decay_rate_S_topo = 6.0
wind = 'variational solver'
Sx = .True.
smooth_wind_distance = 500 m
RK3 = .True.
```

Use `auto_level=1, nz=60, model_top_height=12000 m` as a cheaper fallback when
80 levels are not needed to answer the question. These are tested starting
points, not frozen final choices.

For the current Switzerland-scale 200 m case, retain the 80-level baseline.
The tested 60-level fallback converged worse, and fixed low solver alpha was
unstable. Do not run physics or publish output unless the initial physical wind
solve returns status 0 with relative residual at most `1e-5`; merely reaching
the iteration cap with a reduced residual is not acceptance.

The six-hour Switzerland 200 m engineering qualification passed with the
discretely adjoint solver: all solver and conservation gates, two-record
full-state output validation, and height-aware comparisons against the
matching REA-L records passed. Treat this as a validated engineering baseline,
not a multi-day scientific result or a frozen final configuration.

Keep the published static DEM unchanged and use the validated HICAR
large-/small-scale terrain split (`terrain_smooth_windowsize=5`,
`terrain_smooth_cycles=10`) for the 80-level SLEVE 2/6 grid. Before wind
initialization, require the actual distributed grid to have finite, positive
mass-level Jacobians and interface thicknesses. For the Switzerland 200 m
baseline, retain the acceptance margins `minimum_mass_jacobian >= 0.1` and
`minimum_interface_thickness >= 5 m`; the validated values are 0.17194 and
12.257 m.

## Design rules

- Target roughly 5-8 mass levels below 200 m AGL for wind-energy applications.
- Cover Alpine terrain up to about 4500-5000 m ASL while keeping the top around 10-14 km ASL unless the science requires a deeper atmosphere.
- Prefer the variational wind solver. `wind='none'` produced a model-top-controlled `w` pathology in the reference case.
- Keep `Sx=.True.` initially and use conservative SLEVE decay (`2/6`). Values `1/1` retain too much terrain influence aloft.
- Treat the SLEVE terrain split as part of the vertical-coordinate design.
  HICAR's sampled analytic `gamma` is not a sufficient invertibility test;
  gate the actual constructed mass and interface geometry.
- Do not use stronger external DEM filtering as a wind-solver intervention.
  If scientific anti-aliasing is desired, apply a scale-selective,
  land/sea-aware filter in static preprocessing so all derived static fields
  can be regenerated consistently, and retain the unfiltered DEM as a
  reference sensitivity.
- Tune vertical grid and model top before solver iterations, diffusion, forcing interpolation, or advection options.
- Change one parameter family at a time and retain a common forcing/static baseline.

## Deferred production qualification reference

The following 100 m, month, annual, publication, and promotion requirements
apply when consolidating a scientifically selected strategy. During R&D,
reuse only the individual checks that are material to the active inference.

For the national 100 m engineering gate, do not reuse a small-domain result
or a four-cell scaling as evidence. First reproduce the SLEVE geometry from
the actual published DEM with the exact namelist split and require mass
Jacobian >= 0.1, interface thickness >= 5 m, and mass-level spacing >= 5 m.
Then run two short 16-node/64-GPU segments separated by an exact-end restart.
Sample device memory on every GPU and available host memory on every node,
requiring the exact 16-node by four-GPU sampling topology and at least 15%
headroom for both segments. Re-hash the frozen gate config, paired-event
authorization, geometry report, and actual national static domain in the
final assessor; a copied commit or static hash in a mutable plan is not
evidence. Require the continuation to log an actual restart read, emit only
new unique times, pass every independent solver/conservation audit, and
compare routine history fields with the restart at the boundary. Require the
complete planned Slurm accounting DAG. Record positive output/restart bytes,
Slurm wall, the complete HICAR init/forcing/output/physics timer inventory,
restart-write wall upper bound, and validation wall. This gate also requires
the runner's passing production-provenance block. It qualifies engineering
capacity only; it does not establish 100 m scientific added value or authorize
production.

- Treat window length, overlap, warm-up, and retained-core ownership as an
  experiment policy, not fixed defaults. The existing 72-hour summer tests
  are evidence for one layout only. Contrast regimes and model ages before
  selecting a production window policy.
- Scale to month, annual, or 100 m work only when a shorter experiment changes
  a scientific decision. Use the preemptible controller and shared forcing
  cache when they save compute; retain source/configuration identity and key
  outputs, without imposing production archive or promotion ceremony during
  R&D.
- Do not integrate legacy HICAR `runoff_surface` or `runoff_subsurface`
  snapshots as `mm s-1`. NoahMP converts its internal rates to millimetres per
  soil timestep before HICAR copies the last-step depth, despite the legacy
  rate metadata. Dividing by the exact soil timestep can reconstruct only a
  sparse instantaneous-rate sample; it is representativeness-limited and
  cannot prove interval closure. Month and annual water-budget promotion
  require restart-persistent online interval/cumulative runoff and evaporation
  totals with exact time bounds/reset semantics, proven-compatible
  precipitation accumulation, and the groundwater stores needed to close the
  inventory. Test nonzero fluxes across a restart boundary; zero-valued
  fixtures do not exercise this contract.
- Enforce rolling restart retention with jobs, not documentation alone.
  Delete a superseded boundary only after the adjacent successor completion
  has revalidated both restart hashes; preserve any overlap-start checkpoint
  until the trajectory comparison passes; atomically publish the lifecycle
  result; and make promotion depend on those reports. Serialize adjacent
  retirements because one job's successor is the next job's deletion target;
  concurrent hash/delete operations create a lifecycle race. Keep the latest
  main checkpoint plus explicitly selected recovery/validation checkpoints.
- For the 2019-10-01 through 2020-10-01 annual cycle, require 2,929 unique
  three-hourly model records. The full-reference counts are 366 TabsD days,
  365 complete 06--06 UTC RhiresD windows, 2,927 matched SIS records, and
  2,929 station model times; every DJF/MAM/JJA/SON subset must retain at least
  95% coverage. Before computing those fractions, require each station,
  TabsD, RhiresD, and SIS timestamp collection to be duplicate-free and a
  subset of its exact frozen annual axis; counts alone can be inflated by
  duplicate or out-of-period matches. Emit both whole-period and seasonal
  station/grid metrics.
  Require four restart-trajectory boundaries, plus independently initialized
  DJF and JJA overlaps with at least 21 retained days after characterized
  spin-up. Apply the frozen absolute application limits and the tighter
  seasonal HICAR-versus-REA-L margins. Only
  `GO_20_YEAR_200M_PRODUCTION` from
  `validation/assess_scientific_annual.py`, together with the immutable
  release, failure-recovery, and archive restore evidence it checks, may
  authorize production; it never authorizes 100 m science. The release must
  point to published, nonempty source, executable, static-domain,
  configuration, and annual-plan artifacts under the approved destination
  whose actual SHA-256 values match the manifest and the annual segment
  identity. The archive transfer must likewise expose a checksum-verified
  published manifest at the contract-approved destination; syntactically
  valid hash strings alone are not evidence.
  `streaming/prepare_scientific_annual_pilot.py` is planning-only: it may
  publish the 53 main segments and six equivalence overlaps after
  `GO_ANNUAL_CYCLE` and contract approval, but it must not submit annual
  compute before the generated plan and allocation are reviewed.
- Keep REA-L source consistency separate from independent skill. Compare
  HICAR and REA-L with the same observation times, sites, quality mask, and
  sampling operator; finer spatial structure is not evidence of added value.
- On Balfrin, retrieve hourly SwissMetNet event data with
  `case_studies/swiss_200m/scripts/retrieve_smn_event_observations_balfrin.sbatch`.
  The verified JRetrieve setup uses `OPR_HOME=/oprusers/osm`, PROD station
  group `SMN`, data-quality category 4, and use limitation 50. Raw
  use-limitation-50 data are internal-only: keep them in the event scratch
  tree and retain only checksum-bearing manifests and aggregate validation
  reports in the coordinating repository.
- The qualification profile currently writes instantaneous HICAR fields
  every three hours, whereas SwissMetNet temperature, humidity, wind, and
  radiation fields are hourly aggregates. Label those event comparisons as
  representativeness diagnostics. Sum ending-hour station precipitation over
  the exact HICAR interval before comparison.
- Retrieve public gridded references with
  `case_studies/swiss_200m/scripts/retrieve_ogd_grid_references_balfrin.sbatch`.
  It resolves the MeteoSwiss OGD STAC `archive-ch` assets and publishes
  checksum-bearing NetCDF plus ready markers under
  `$SCRATCH/icon_hicar/observations/ogd_grid/<year>`. For the event gate,
  aggregate HICAR precipitation to the RhiresD grid and exact 06--06 UTC
  daily window, evaluate REA-L on the identical grid/window, compare
  trapezoidally sampled three-hour HICAR and REA-L temperature over each
  00--24 UTC TabsD day, and compare three-hourly instantaneous HICAR
  shortwave with matching hourly SIS and SIS-No-Horizon while explicitly
  reporting that temporal representativeness mismatch. Retain the same
  daily TabsD operator for month/annual temperature checks.
  For a cross-year annual cycle, set `OGD_PERIOD_START` and
  `OGD_PERIOD_END_EXCLUSIVE`; the retriever must publish both annual surface
  files and every intersecting monthly SIS/SIS-No-Horizon asset. Do not reuse
  a ready manifest whose recorded year/month selection differs.
  Historical CombiPrecip/CPC is not currently available through this public
  archive and is not required when RhiresD is operational.
- Before a month pilot, require complete solver-log auditing, finite/range
  and restart gates, class-aware surface water/energy diagnostics, REA-L
  source comparison, side-by-side HICAR/REA-L SwissMetNet metrics, and
  RhiresD/SIS gridded comparisons for both event seasons.
- Freeze the event acceptance margins before model results exist. If the first
  seasonal event fails an independent physical-skill screen, stop escalation:
  do not submit the second season, restart overlap, month, annual, or finer
  grid merely to average out the defect. Preserve the complete event and use
  bounded post-processing to stratify the failure by time, elevation,
  boundary distance, and land class. Resume compute only with a
  mechanism-based source or configuration hypothesis; do not retune the
  threshold after seeing the result.
- As an early event-scale restart screen, compare an independent continuation
  from hour 48 through hour 72 against all eight subsequent three-hourly
  qualification records of the continuous summer event. Reuse the immutable
  published forcing by verified reference rather than retrieving or converting
  it again. This does not replace the month-stage multi-day overlap.

## Wind-climatology output

- Do not preserve ICON `VMAX` as a HICAR production field. Publish HICAR
  resolved sample maxima with exact sampling cadence and interval bounds, and
  do not call them gusts. A gust product requires a separately named
  HICAR-native method and validation against observations with the intended
  short-duration definition.
- The SwissMetNet event feed uses `fkl010h0`/`dkl010h0` for hourly mean wind.
  Retrieve `fkl010h1`, the hourly maximum of one-second wind, as a separate
  channel for a future gust gate. If the HICAR diagnostic has three-second
  support, explicitly harmonize or stratify the durations; never silently
  compare or relabel one-second and three-second extremes.
- For renewable-energy output, keep `u10m`/`v10m`; mass-grid `u`, `v`, and
  density interpolated without extrapolation to 50/75/100/125/150/200 m AGL;
  friction velocity; momentum roughness length; surface-layer bulk Richardson
  number; and PBL height. Reduce raw output online into vector/scalar moments,
  density-weighted cubic speed, resolved maxima, direction sectors, and speed
  exceedance counts.
- HICAR's `mol` is a surface-layer temperature scale in kelvin, not
  Monin--Obukhov length; exclude it from stability products. HICAR stores the
  raw MM5-revised bulk Richardson number. The surface-layer scheme clips only
  the value used by its similarity-function inversion at `SBRLIM=250`, so
  calm stable output can legitimately exceed 250; validate finiteness and a
  broad sanity range rather than applying 250 as an output cap.
- In a spin-up convergence ladder, the longest member is the reference and
  its exact self-match is not evidence of convergence. Select a minimum only
  if at least one shorter member and its complete longer-duration tail pass
  every frozen event/height criterion. If only the reference passes, report
  `MINIMUM_SPINUP_NOT_BRACKETED` with the reference duration as a lower bound
  and extend the bracket before any stitched-chain or production gate.
- If same-valid-time winds retain cold-start-age dependence after the hourly
  target is reset from bit-identical forcing, do not assume shorter segments
  remove drift. Compare pressure, theta, qv, density, PBL, friction velocity,
  and land state before changing wind parameters. For the Swiss 200 m
  Morrison bridge, pressure was identical but the coupled prognostic state was
  not; Sx on/off changed winds by less than `1e-6 m s-1` in both Plateau and
  Storm Sabine replays and is not the cause.
- Do not set `advect_density=.False.` as a production diagnostic with the
  qualified adjoint solver or disable its conservation gate. The tested branch
  was rejected at `relative_Bq=0.25952` versus the `2e-5` requirement.
- `wind_only` omits the evolved high-resolution meteorology that motivates
  using HICAR. Do not confuse it with a wind-focused fully coupled product;
  use it only as an explicit paired intervention when that distinction is the
  scientific question.
- The tested alternative of independent 24-hour coupled windows launched every
  12 hours with hard ownership of model ages `(12, 24]` also fails as a
  continuous trajectory. Across four regimes, two handoffs, and seven heights,
  zero of 56 handoff-height combinations passed the frozen convergence
  thresholds; worst-handoff vector RMSE was `1.44--8.96 m s-1` against the
  `0.20 m s-1` limit. Every owned core remained physically valid. Do not hide
  this trajectory disagreement by linearly tapering independently developed
  small-scale circulations. Exact evidence is published under
  `/store_new/mch/msopr/olifu/icon_downscaling/qualification/wind_overlap_handoff_v1`.
- Short coupled windows may still be assessed for marginal climatological
  statistics by reducing each deterministically owned core independently.
  That path requires observation-facing skill and launch-origin sensitivity
  evidence. It does not supply continuous chronology, persistence, or block
  extremes unless a separate seam-safe method passes.

## Deferred terrain-radiation production qualification

The component invariants below remain useful for focused R&D. The promotion
ladder and national-scale requirements apply only during later consolidation.

- Keep `terrain_shading=.False.` as the production default until the bounded
  causal ladder passes.
- Use the renderer profiles in cumulative order: `off`, `direct`,
  `direct-diffuse`, `full-local`, then `full-neighborhood`. Do not jump from
  off to the inseparable full package.
- Preserve the RRTMG(P) horizontal-plane direct and diffuse components as
  restart state. Apply horizon/slope projection to raw direct and SVF only to
  raw diffuse; never reconstruct direct as total minus an already shaded
  diffuse field.
- For UTC forcing and model timestamps, render `tzone=0.0` explicitly. A local
  civil-time default shifts the solar azimuth/elevation test and can turn a
  correct horizon crossing into an apparent component failure.
- First require flat/open identity, blocked-horizon behavior, finite/range and
  surface-energy closure, exact split-restart agreement to the project
  tolerance, and shadow timing within one 4-degree sector plus one radiation
  interval.
- Test flat/open component identity within each enabled run: compare corrected
  direct/diffuse fluxes with that run's preserved raw RRTMGP components.
  Preserve enabled-versus-off coupled-trajectory drift as a diagnostic, but do
  not confound it with the no-op terrain correction itself.
- If the candidate fails split-restart continuity, keep terrain-component and
  restart decisions separate. Run any later A/B/C preconditioning plus score
  window uninterrupted; do not join them with a restart until the exact
  candidate stack passes its restart gate.
- Qualify on a synthetic domain and compact valley tile across clear summer,
  winter/snow, and nocturnal drainage cases. Do not authorize a national run
  until component attribution, observation-facing wind/radiation behavior,
  runtime, and storage all pass.
- Retain the model limitations in interpretation: isotropic diffuse SVF,
  nearest-sector horizon lookup, finite horizon search, bilinear horizon
  regridding, and simplified local/neighborhood reflected-SW and terrain-LW
  view factors.

## Physical plausibility checks

- Report min/max and robust percentiles, not only NaN counts.
- Treat HICAR `precipitation`, `snowfall`, and `graupel` as accumulated
  surface amounts in `kg m-2`. Use a common broad, duration-independent
  corruption screen for finite/range validation; do not apply an
  instantaneous-rate cap to multi-day accumulations. Assess event
  plausibility and hydrometeor partitioning independently.
- Map the level and terrain location of maximum `|w|`.
- Separate AGL from ASL diagnostics.
- Compare forcing `W` and HICAR `w/w_grid` near the upper boundary.
- For source-aware column comparisons, match sparse HICAR mass points to the
  nearest regular forcing point, interpolate source fields by geometric HFL
  height (HHL for `W`), do not extrapolate, and average HICAR's adjacent
  staggered `u`/`v` faces to the mass point.
- Screen upper-level temperature and potential-temperature extremes.
- Test boundary-zone sensitivity and terrain-decay sensitivity.
- Preserve output needed to distinguish coordinate vertical velocity from physical vertical velocity.

Read [references/sensitivity-results.md](references/sensitivity-results.md) for the evidence behind the baseline and the remaining tuning order.
