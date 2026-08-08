# ICON-to-HICAR scientific assessment

## Current conclusion

The repository is now oriented around one R&D path, but the complete setup is
not yet scientifically ready for a long campaign. The input architecture and
selected namelist are credible enough for a small-domain end-to-end pilot.
Added value over REA-L-CH1 remains unproven and is the central empirical
question.

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
| Terrain coordinate | SLEVE 2/6; smoothing window 5, 10 cycles | Previously stable; retain for controlled comparison |
| Wind | Adjoint variational, `Sx=true`, 500 m smoothing, alpha 1, 2500 iterations | Best-supported current option |
| Dynamics | RK3, density advection | Explicit and internally consistent |
| Atmospheric input | Hourly native REA-L decoded and transformed by hicarprep | Selected; pilot still required for the simplified direct route |
| Moisture | Dry-air mixing ratios; QI explicitly zero only because source is absent; W diagnosed by HICAR | Explicit and interpretable; W remains a sensitivity |
| LBC | Hourly sparse hicarprep states, 10 km shoulder | Usable provisional choice; width is not closed |
| Land initialization | Native TERRA state transferred by SMI to four NoahMP layers | Selected starting point; epoch extrapolation and transfer uncertainty remain |
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
- The national grid has 3.75 million horizontal cells. One hourly 80-level
  forcing record is of order 10 GB before allowing for compression and working
  arrays, so generating a week of cached hourly records as the first test would
  be unreasonable. Pilot the 701x701 Alpine bridge, then measure preprocessing,
  model throughput, memory, and output volume before selecting the campaign
  extent.

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

## Campaign and evaluation design

`orchestration/rd_campaign.py` is the only campaign controller. It uses 24-hour
segments by default, one serial restart chain per season, independent seasonal
chains, bounded retries, at most two concurrent pp-short input jobs, and the
preemptible partition for HICAR. Filesystem truth is limited to input ready
markers, Slurm job IDs, terminal restarts, `segment.json`, and
`segment.complete`. The campaign record includes source commits and working
tree hashes.

The four-season experiment should use equal-duration, synoptically varied
windows and score HICAR and REA-L against the same SwissMetNet sites, QC masks,
and times. Current comparison code uses nearest HICAR cells and bilinear REA-L.
Its main statistical limitation is that instantaneous HICAR values are compared
with hourly station aggregates. Reports need paired differences and uncertainty
intervals before a claim of added value is robust.

## Decision sequence

1. Finish the remaining two hourly pairs for the corrected 701x701 runtime
   domain. The static geometry, four-layer Noah-MP land state, and 00 UTC direct
   forcing/LBC pair already pass; 01 and 02 UTC are the remaining brackets.
2. Run a continuous two-hour case and a one-hour plus one-hour restart case;
   require identical terminal model state and inspect the LBC turnover. Repeat
   with an independent cold-cloud/autumn-storm case.
3. Measure resource use, then select the scientifically useful campaign domain
   and run equal windows in winter, spring, summer, and autumn as 24-hour
   preemptible segments.
4. Produce REA-L surface references without reintroducing fieldextra, retrieve
   matching SwissMetNet observations, and calculate paired seasonal metrics.
5. Decide whether HICAR adds wind skill, in which regimes/elevations, and
   whether errors implicate input mapping, LBC width/W, land initialization,
   or physics.
6. Only then test 100 m or plan a 20-year run.

## Verification status

- Coordinator focused tests: 80 passed.
- Repository syntax/policy checks: passed.
- HICAR source is clean at `7ad54787`; reflected-shortwave compile and targeted
  GPU unit-test evidence passed. The clean campaign build from that exact
  commit completed as job 5042485 and its targeted GPU test passed as job
  5042502.
- Four-season campaign: not launched; no readiness claim should precede the
  exact-staggering/direct-product and restart-equivalence pilots.
