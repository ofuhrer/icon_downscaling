---
name: hicar-alpine-configuration
description: Choose and validate HICAR namelist, vertical-grid, terrain-coordinate, wind-solver, and physical-quality settings for 100-250 m Alpine downscaling. Use when designing experiments, tuning wind downscaling, diagnosing upper-level vertical velocity, selecting model top or SLEVE decay, or scaling from a small domain toward Switzerland.
---

# HICAR Alpine configuration

Treat configuration as an experiment hierarchy: prove numerical stability and physical plausibility on a small representative Alpine domain before increasing area or reducing `dx`.

## Current production-candidate baseline

For 250 m wind-focused Alpine downscaling, use this as the current production-candidate default:

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

Use `auto_level=1, nz=60, model_top_height=12000 m` as a cheaper fallback when domain size makes 80 levels too expensive. These are tested starting points and the current operational choice for new experiments; they are not a fully production-qualified final configuration until the Switzerland-scale and multi-day validation milestones pass.

For the current Switzerland-scale 200 m case, retain the 80-level baseline.
The tested 60-level fallback converged worse, and fixed low solver alpha was
unstable. Do not run physics or publish output unless the initial physical wind
solve returns status 0 with relative residual at most `1e-5`; merely reaching
the iteration cap with a reduced residual is not acceptance.

The six-hour Switzerland 200 m engineering qualification passed with the
discretely adjoint solver: all solver and conservation gates, two-record
full-state output validation, and height-aware comparisons against the
matching REA-L records passed. Treat this as the production-candidate
engineering baseline. It is not yet a multi-day scientific baseline.

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

## Validation hierarchy

1. Run a short debug smoke test and require finite required outputs.
2. Run at least 1 h release validation and compare against the debug reference within explained optimization tolerances.
3. Run a 24 h sensitivity case over representative high terrain.
4. Run the 701 x 701 steep-Alpine bridge; it is a regression, not a proxy for
   national conditioning.
5. Run the full national geometry, solver, and conservation gates.
6. Inspect `w`, `w_grid`, horizontal winds, temperature, humidity, pressure, precipitation, and conservation diagnostics by level and terrain class.
7. Verify the top level does not control horizontal maxima and that large residual vertical velocities are physically localized.
8. Only then move from 200-250 m toward 100 m or multi-day production.

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

## Long-duration scientific qualification

- Escalate duration as paired 72-hour summer/winter events, then one
  continuous month, then an annual seasonal cycle. A successful event does
  not by itself qualify a seasonal or 20-year campaign.
- For the month stage, use five exact-end restart segments of
  7/7/7/7/3 days, declare the first seven days as spin-up, and retain 24
  days. Require 249 unique three-hourly records. Start an independent
  uninterrupted eight-day reference from the first seven-day restart so it
  crosses the next boundary, then compare every qualification field for at
  least 24 hours after that boundary using the tolerances frozen in
  `config/scientific_pilot_plan.json`. A shared-timestamp boundary check alone
  is not restart-equivalence evidence. The month promotion assessor must also
  require the exact station/model time axis, all 31 unique TabsD days, all 30
  unique RhiresD windows, and 247 unique in-period SIS matches; raw list
  lengths are not sufficient coverage evidence.
- After the final month segment, run the physical-budget, REA-L-source,
  SwissMetNet, and OGD-grid comparisons independently. Screen all retained
  elevation and active-soil class means for large nearly monotonic tendencies;
  treat a screen hit as an attribution requirement, not automatic numerical
  drift. Annual execution requires an exact signed classification of every
  flag, zero unexplained flags, all frozen HICAR-versus-REA-L degradation
  limits, every lossless compression publication, and an `APPROVED` durable
  archive contract with a published restore drill. An unresolved archive
  contract must produce a specific hold even when the science passes. Also
  require a separately approved application-quality contract containing
  absolute limits and metric weights; relative non-degradation against the
  driver alone must not authorize an annual run. Every month/annual model
  completion must also carry a passing production-provenance block: clean
  pre-run tracked status plus no untracked build inputs under the declared
  HICAR source/configuration paths, full source commit, executable SHA-256
  verified again after execution, exact archived plan and
  forcing-publication hashes, static-domain hash, and model-log hash.
  Unrelated untracked runtime logs are permitted. A legacy completion without
  this block may inform event science but cannot authorize an annual or
  20-year campaign. Every segment and equivalence run in one gate must share
  a single source commit, executable hash, and static-domain hash.
- Before submitting a month or 100 m capacity DAG, require its submitter to
  validate semantic provenance markers in the runner, model validator, and
  final assessor, then checksum every referenced Slurm script plus those
  critical Python runtime files into the dry run and submission receipt. This
  is a pre-submission stale-stack gate; file existence alone is insufficient.
- In a long streaming chain, schedule forcing retirement only after the
  consuming model publication has validated output and exact-end restart.
  Recheck the published plan, forcing hashes, and model-provenance hashes;
  withdraw all forcing ready markers before deleting payloads; atomically
  publish a checksum-bearing retirement report; and require that report in the
  promotion assessor. A policy sentence without an executable retirement job
  is not a bounded-storage workflow.
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
