# ICON-to-HICAR scientific assessment

## Current conclusion

The repository is now oriented around one R&D path. The first direct-input
pilot found an unstable cached RBF stencil that created isolated 320 m s-1
winds; the remap is corrected and two-hour direct-input runs are finite across
the hourly turnover. Restart testing separated two effects. A checkpoint at a
process end is not the same state as the corresponding interior time, but an
interior checkpoint produced by integrating one disposable overlap hour is
bit-for-bit equal to the continuous control in all 19 tested output fields.
Restart initialization then introduces a much smaller numerical perturbation
(one-hour RMS 0.00122 K for surface temperature and below 0.0001 m s-1 for each
10 m wind component). This is an acceptable quantified R&D floor, not bitwise
restart equivalence. Added value over REA-L-CH1 remains unproven and is the
central empirical question.

The active path is:

`native REA-L GRIB -> hicarprep target state + sparse LBC -> HICAR -> common
station/REA-L comparison`

The old fieldextra forcing path, pressure/wind certificates, publication
states, release gates, recovery bundles, and generalized production campaign
machinery have been removed. Runtime checks now protect only input identity,
chronology, dimensions, finite values, water representation, safe Slurm use,
and restart completeness.

## Selected experimental setup

| Component | Current choice | Assessment |
| --- | --- | --- |
| Grid | 200 m | Use the 701x701 Alpine bridge for turnover/restart pilots; the 2271x1651 national grid needs separate resource evidence |
| Vertical grid | 80 levels, 12 km top, nominal 26 m first level, stretch 0.65; hard 20 m thickness floor | Sound starting point; Alpine-bridge minimum is 20.768 m, but the choice is not uniquely established |
| Boundary terrain | 20 km cosine blend from REA-L-CH1 HSURF at the edge to the high-resolution DEM inward | Verified in the runtime static; deliberately broader than the separate 10 km atmospheric LBC shoulder |
| Terrain coordinate | SLEVE 2/6; smoothing window 5, 10 cycles | Previously stable; retain for controlled comparison |
| Wind | Adjoint variational, `Sx=true`, 500 m smoothing, alpha 1, 2500 iterations | Best-supported current option |
| Dynamics | RK3, density advection | Explicit and internally consistent |
| Atmospheric input | Hourly native REA-L decoded and transformed by hicarprep | Corrected 00/01/02 UTC products and a two-hour turnover run pass; seasonal generality is untested |
| Moisture | Dry-air mixing ratios; QI explicitly zero only because source is absent; W diagnosed by HICAR | Explicit and interpretable; W remains a sensitivity |
| LBC | Hourly sparse hicarprep states, 10 km shoulder | Usable provisional choice; width is not closed |
| Land initialization | Native TERRA soil state plus ICON SWE/depth/density and bulk `T_SNOW` transferred to NoahMP | Integrated starting point; glacier snow is preserved and sourced vegetation climatologies are wired, but the winter response and any supplied climatology still need controlled tests |
| Radiation | RRTMGP, 600 s update; terrain radiation off | Correct campaign baseline until focused terrain-radiation tests pass |

The forcing renderer now accepts only this configuration plus three output
profiles. It requires one static domain, four soil layers, matching hourly
forcing/LBC records, exact segment bracketing, and explicit restart input.

## Critical input findings

- The old sparse-LBC evidence was not a clean validation of the new hicarprep
  atmospheric route: its initial state originated in the old fieldextra path.
  Certificate labels hid this dependency. Those labels and that route are gone.
- The direct hicarprep transform is conceptually sound: strict native P/T/U/V/
  QV/QC/W/HHL/HSURF/FR_LAND decode, explicit missing-QI policy, scalar and
  vector target weights, target-column vertical reconstruction, dry-water
  conversion, and paired forcing/LBC output.
- The active direct hourly producer decodes from the daily 00 UTC REA-L cycle,
  transforms one valid time, validates the target record, and publishes only
  the paired ready markers needed by concurrent readers.
- The native land packager no longer imports the retired remapper. SMI is a
  model choice, not a certification result. Static-epoch back extrapolation is
  still an acknowledged approximation and should be quantified if land-state
  errors dominate.
- Snow initialization transfers instantaneous ICON `W_SNOW`, `RHO_SNOW`, and
  `T_SNOW`. Hicarprep remaps SWE and derived snow volume separately with
  nonnegative, same-surface weights, diagnoses target depth and bulk density,
  and remaps bulk snow temperature on snow support with conservative skin-
  temperature bounds. HICAR uses that bulk value in active NoahMP snow layers
  only on cold starts; restart snow temperatures remain prognostic. The pinned
  NoahMP driver now preserves caller-provided SWE and depth on configured USGS
  ice category 24 rather than erasing glacier snow. This path is unit-tested
  and compiles locally, but still needs the staged two-hour winter diagnostic
  before snow or surface-energy improvement is claimed. Fractional snow cover
  and elevation-dependent redistribution remain deliberately unimplemented.
- The WorldCover path already reclassifies every 10 m pixel before 200 m area
  aggregation and retains all USGS category fractions; replacing that mapping
  is not the first land-cover intervention. Runtime-domain `VEGFRA`, `LAI`,
  `ALBEDO`, and maximum vegetation fraction are now range checked, normalized,
  and wired into HICAR when supplied. Twelve-month `VEGFRA` uses HICAR's native
  monthly path; the other climatologies are materialized at the initial valid
  time. No climatology is fabricated: absent explicit fields, HICAR retains its
  LAI 1, vegetation fraction 60%, and maximum vegetation fraction 80% defaults.
  A sourced `VEGFRA`/`LAI` sensitivity remains the next interpretable land-cover
  test.
- The independent preprocessing audit found real launch defects. The renderer
  now resolves `NZ` and `ADVECT_DENSITY`; the forcing/LBC pair is validated
  before either ready marker; forcing caches are season/domain-specific; and
  runtime completion checks now require exact timestamps, selected physics,
  all forcing brackets, and an exact terminal restart.
- Sparse LBC U and V now always have distinct face-grid point sets, dimensions,
  weights, and bounds checks. Their values are reconstructed by midpoint
  interpolation from the mass-grid wind used by the regular forcing reader.
  This matches the structured-grid staggering convention but still needs a
  real two-hour turnover diagnostic because insertion occurs after the wind
  projection and can temporarily perturb discrete continuity.
- Static construction now partitions the public-source product and appends
  HHL/HFL before publication. The first rebuilt Alpine-bridge grid exposed an
  important weakness in the nominal-level setting: its true SLEVE minimum was
  only 12.509 m. The active defaults now require every model layer to exceed
  20 m and record both requested and achieved geometry limits. A nominal 26 m
  first level gives a measured 20.768 m minimum and 0.2473 minimum Jacobian on
  the 701x701 grid; the rejected artifact is retained separately and cannot be
  accepted by runtime validation. The HICAR namelist must use the same nominal
  26 m setting because HICAR reconstructs SLEVE at runtime; the renderer now
  checks the static geometry parameters, endpoints, and actual thickness before
  writing the namelist.
- Native ICON HHL is ordered correctly, but unconstrained RBF interpolation of
  each interface independently crossed one layer in four of 491,401 target
  columns (worst thickness -123.6 m). Hicarprep now remaps the column endpoints
  and positive layer thicknesses, enforces a 20 m source-geometry floor, and
  redistributes only the thin-layer deficit while preserving the endpoints.
  The first real 00 UTC forcing/LBC pair passed with thickness rescaling limited
  to 0.9904--1.0023 and completed in 9:59 (job 5042543).
- That first target record was nevertheless invalid. One nearly singular
  ten-donor scalar RBF stencil had normalized weights as large as -204..206 and
  L1 amplification 897. It created three adjacent interior wind-spike columns
  44.4--44.6 km from the boundary: maximum vector speed 319.7 m s-1 at about
  777 m AGL, despite native U/V ranges of -20.3..94.6 and -59.4..35.4 m s-1.
  The sparse boundary happened not to include the bad interior columns, so its
  winds remained moderate and the previous finite-only forcing validator
  passed. Hicarprep now accepts only conditioned scalar operators, rejects a
  cached operator with L1 amplification above 10, maps scalar U/V within the
  local donor extrema, and rejects target wind speed above 200 m s-1. The
  rebuilt real operator needed a maximum 1e-8 nugget and reduced the maximum
  weight and L1 amplification to 1.65 and 6.49 (job 5042789). The seasonal
  cache must use the rebuilt operator; the old cache is invalid.
- The national grid has 3.75 million horizontal cells. One hourly 80-level
  forcing record is of order 10 GB before allowing for compression and working
  arrays, so generating a week of cached hourly records as the first test would
  be unreasonable. Pilot the 701x701 Alpine bridge, then measure preprocessing,
  model throughput, memory, and output volume before selecting the campaign
  extent.
- Corrected 00, 01, and 02 UTC forcing/LBC pairs all passed the paired real-data
  validator with the conditioned operator (jobs 5042798, 5042799, and 5042810).
  A continuous two-hour full-physics run at HICAR `5fc3c71b` then completed
  across the 01 UTC turnover with exact output/restart times and finite bounded
  core state (job 5042814). Its CFL timestep changed from 1.40 to 0.8758 s at
  turnover as diagnosed grid-relative vertical wind increased from about 64 to
  127 m s-1. This establishes numerical turnover, not physical realism.
- The corresponding one-hour plus one-hour chain also completed, but its
  terminal core state was not identical to the continuous run. With the
  baseline restart initialization, maximum one-hour differences included
  about 0.45 K temperature and 0.34 m s-1 grid-relative vertical wind, with
  larger local differences in cloud and land diagnostics. Restart output
  correctly omits the predecessor's already-written terminal timestamp; the
  validator now handles that without weakening the state checks. A focused
  experiment that suppressed the restart-time wind projection made the
  divergence much worse (roughly 30 K temperature and 91 m s-1 grid-relative
  wind in the active core), so that intervention was rejected and HICAR was
  returned to `5fc3c71b`. The failure is not resolved by simply skipping the
  projection. At that stage, initialization/cadence state around the forcing
  boundary remained unresolved; the following experiments quantified it.
- Subsequent controlled tests at `e89b3f0c` localized that mismatch. Cold-start
  replicates were bitwise identical. Changing `Sx`, the forcing-list origin,
  the restart interval, forcing-reader look-ahead, and canonicalizing the
  probed wind operator did not repair the split. The first one-hour run already
  differed before restart read solely because its configured process end was
  01 UTC. Running through 02 UTC while writing a 01 UTC checkpoint made all 19
  stored fields at 01 UTC bitwise identical to the continuous control. A
  restart from that exact interior checkpoint remained non-bitwise after the
  second hour, but the surface impact was small: taix max/RMS 0.150/0.00122 K,
  u10 max/RMS 0.0144/0.000071 m s-1, v10 0.0148/0.000048 m s-1, and
  precipitation 0.00548/0.0000153 in the stored accumulation units. The
  campaign therefore writes each continuation checkpoint one hour before
  process shutdown and retains the duplicated overlap time as a per-season
  restart-uncertainty diagnostic.

## Reflected-shortwave cadence defect

The reported defect was real. `ra_driver.F90` rebuilt total shortwave from
direct plus diffuse every model step, while terrain-reflected shortwave was
added only on full radiation updates. With a 600 s radiation interval, the
reflected contribution disappeared between updates.

HICAR commit `7ad54787` caches and reuses the last reflected component between
full radiation calls, while excluding the stale value during a refresh before
computing the new component. The exact patch compiled with NVHPC/OpenACC on
Balfrin (job 5042469), and its targeted GPU unit test passed (job 5042480).
This proves the recurrence logic, not the full model energy budget. Terrain
radiation therefore stays off in the campaign baseline pending a short cadence
diagnostic.

## What previous stable integrations actually established

- The strongest full-state result is a six-hour Swiss 200 m run at HICAR
  `79c87b86`: 80 levels, 12 km top, full Morrison/YSU/NoahMP/revMM5/RRTMGP
  physics, adjoint variational wind, density advection, RK3, and CFL factor 1.6.
  Its minimum interface thickness was 12.257 m; all stored atmospheric fields
  were finite and plausible at the endpoint. It used the retired fieldextra
  regular forcing path, not direct target-native hicarprep.
- A 72-hour summer run at `5da4b198` used the same coupled configuration and
  completed with finite surface/land output, valid daily restarts, and passing
  solver diagnostics. Its timestep was recomputed at regular hourly forcing
  updates and varied about 3.44--3.77 s. It was numerically stable but
  scientifically poor: comparison to SwissMetNet showed a 3.66 K warm bias,
  14.3 percentage-point dry RH bias, near-zero modeled precipitation during an
  observed wet period, and weak 10 m wind correlations. REA-L was better on
  every headline metric in that event: HICAR versus REA-L RMSE was 5.12 versus
  1.44 K for 2 m temperature, 23.50 versus 10.48 percentage points for RH,
  2.55 versus 1.40 kg m-2 for interval precipitation, 2.27 versus 1.58 m s-1
  for wind speed, and 3.35 versus 2.37 m s-1 for the wind vector. Stability is
  not skill.
  Its daily restart files were checked for presence and finite state, not
  against a continuous control, so it did not establish restart equivalence.
- The earlier two-hour Storm Sabine sparse-LBC run at `5d557495` crossed the
  01 UTC bracket with finite fields using CFL factor 1.6 and dt 1.28 then
  1.03 s. It used the same 20 km ICON-topography blend as the current bridge,
  but it was a hybrid: its regular IC/hourly forcing was produced by fieldextra
  (specific humidity, supplied W, runtime relaxation), while only the sparse
  LBC came from hicarprep. It therefore did not validate the new direct route.
- The 33-hour 250 m prototype retained 32 finite hours but used only 40 levels,
  a roughly 143 m first level, wind='none', and no PBL/LSM/surface/radiation/
  water physics; it then aborted at forcing-list exhaustion. It is only a
  coarse dynamical-core smoke result.

These precedents show that density advection and CFL factor 1.6 can be stable;
they do not support treating a lower CFL as the primary repair for the current
pilot or claiming that the old restart files were trajectory-equivalent. The
requested vertical floor remains approximately 20 m for new work and must
never fall below 10 m; historical 12 m evidence is comparison evidence, not
the selected grid.

## Campaign and evaluation design

`orchestration/rd_campaign.py` is the only campaign controller. The current
four-season case uses two logical 12-hour segments per season, one serial
restart chain per season, independent seasonal chains, bounded retries, and
the preemptible partition for HICAR. A non-final attempt integrates a thirteenth
overlap hour but exposes its 12-hour interior checkpoint to the successor. The
overlap is discarded for scoring in favor of the restarted segment and is
compared separately to quantify restart perturbation. Filesystem truth is
limited to input ready markers, Slurm job IDs, restarts, `segment.json`, and
`segment.complete`. The campaign record includes source commits and working
tree hashes.

The four-season experiment should use equal-duration, synoptically varied
windows and score HICAR and REA-L against the same SwissMetNet sites, QC masks,
and times. Current comparison code uses nearest HICAR cells and bilinear REA-L.
Its main statistical limitation is that instantaneous HICAR values are compared
with hourly station aggregates. Reports need paired differences and uncertainty
intervals before a claim of added value is robust.

## Decision sequence

1. Complete the equal 24-hour winter, spring, summer, and autumn overlap-
   checkpoint chains on preemptible resources; compare each duplicated overlap
   hour as a restart uncertainty check.
2. Produce REA-L surface references without reintroducing fieldextra, retrieve
   matching SwissMetNet observations, and calculate paired seasonal metrics.
3. Decide whether HICAR adds wind skill, in which regimes/elevations, and
   whether errors implicate input mapping, LBC width/W, land initialization,
   or physics.
4. Only then test 100 m or plan a 20-year run.

## Verification status

- Coordinator tests: 92 passed.
- Repository syntax/policy checks: passed.
- HICAR source is clean at `e89b3f0c`; its tree equals the selected restart-safe
  `5fc3c71b` simulation baseline plus the land/snow initialization changes.
  The rejected reader and restart-wind interventions remain absent. The clean
  topology-matched NVHPC/NCCL build and overlap/restart pilots completed on two
  nodes. The generic GPU unit-test executable still has an unrelated
  multi-device PRESENT failure and the CPU build exposes a pre-existing
  SNOWPACK real-constant overflow; neither occurs in the production topology.
- Four-season forcing/reference/observation preparation is active. Model
  submission begins only from the clean overlap-capable coordinator commit.
