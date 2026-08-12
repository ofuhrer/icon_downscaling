# ICON-to-HICAR scientific assessment

## Current conclusion

The project has a literature-led reference configuration and a GPU path that
is bit-reproducible across independent placements. Segmented restarts are
numerically, not bitwise, reproducible. It does not yet have a scientifically
evaluated national all-season result for that complete configuration.

Fresh independent daylight replicas showed that the selected one-block RRTMGP
configuration still separates after repeated radiation calls, despite being
exact through the first output times. The former one-block qualification is
therefore withdrawn. The bounded RRTMG reference keeps the rest of the setup
fixed while avoiding the RRTMGP implementation path. Two independent
12-node/48-compute-rank daylight replicas were exact for all 30 output
variables at seven 10-minute records (976,215,334 values) and all 198 terminal
restart variables (8,497,193,716 values). All fields were finite and daytime
shortwave and longwave were active. The corrected-SST two-hour restart test was
finite and deterministic across full and streaming forcing lists, but differed
from the uninterrupted trajectory after the restart. The perturbation is small
relative to ten-minute evolution and is accepted for this wind-focused R&D
campaign rather than making bit identity a launch requirement.

The earlier restart discrepancy had two independent causes: an end-time
tolerance skipped the final fractional timestep of terminal segments, and the
Noah-MP restart path reconstructed soil geometry with a different
single-precision operation order. Both are corrected. The deliberate NetCDF
timestamp offset of +0.432 s is serialization metadata, not an additional
physics timestep.

The first four-season 24-hour experiment was numerically successful but used a
less complete setup and only 65 sites. It showed no general added value over
REA-L-CH1 and a large autumn ridge-wind degradation. That result is diagnostic
history, not the assessment of the reference setup now being prepared.

The first winter initialization of the complete setup exposed a separate
fork-specific wind-projection defect: diagnosed dynamic alpha coupled to the
adjoint projection produced localized high-terrain 10 m winds up to 139.30
m s-1, with 2,101 cells above 30 m s-1, although forcing was bounded and the
solver converged. An otherwise identical initialization with `alpha_const=1`
bounded the maximum at 21.085 m s-1 with no cells above 30 m s-1; its conservation
residual also passed. The dynamic-alpha coupling is therefore rejected for the
campaign. Alpha 1 is the conservative bounded R&D reference, not a claim of
published equivalence or scientific optimality.

The retained post-publication `legacy` solver is not the operator used by the
published HICAR releases and fails to converge for this national domain. A
bounded current-stack adapter of the released v1.2 analytic operator passed
small decomposed oracle tests but also failed the predeclared national
initialization gate: its best true residual was `9.50e-4`, 1.91 times the
required `4.98e-4`, after three 2500-iteration attempts, and the fallback
solver diverged. No national output was produced. That research branch is
retained as evidence, not selected for the campaign; resurrecting PETSc or an
old full HICAR release would expand the experiment while regressing the current
input, physics and I/O stack.

## Scientific reference

The complete rationale and option-by-option specification is in
`case_studies/swiss_200m/REFERENCE_SETUP.md`. The closest published precedent is
the January 250 m Swiss-Alps HICAR experiment of Reynolds et al. (2023); no
published national, all-season 100--200 m HICAR wind validation exists. The
reference is therefore an untuned, internally coherent starting point rather
than a claimed optimum.

### Domain and vertical grid

- 200 m AEQD grid, 2061 x 1431 cells, centred at 46.815 N, 8.225 E, with a
  40 km external margin around Switzerland.
- Copernicus GLO-30 surface elevation, unsmoothed at the physical surface, with
  a 30 km cosine relaxation to exact REA-L-CH1 `HSURF` at the boundary.
- 80 SLEVE levels, 15 km ASL top, nominal 20 m lowest layer, stretch 0.65,
  split smoothing 5 cells/10 cycles and decay scales 2/6 with exponent 1.35.
- The rebuilt national geometry has minimum interface spacing 17.008 m and
  minimum Jacobian 0.3375. The acceptance floor is 12 m; values below 10 m are
  forbidden.

### Meteorological input

- Hourly native REA-L-CH1 is decoded and transformed only with the maintained
  `hicarprep` path. The old fieldextra route is retired.
- Regular full-domain forcing supplies P, T, dry-air QV/QC/QI, earth-relative
  U/V, W on the authoritative target HFL, and valid-time SST on water cells.
  Terrain-following W uses U/V rotated into the target-grid basis for the slope
  dot product, while serialized U/V remain earth-relative. HICAR rotates those
  horizontal winds and applies the diagnostic projection.
- The campaign uses HICAR's literature-established regular full-domain forcing
  relaxation with `relax_filters=true`; it does not configure or generate
  sparse LBC. The newer sparse path remains available for controlled
  experiments, but its scalar-only state, 10 km shoulder and 3600 s timescale
  are not sufficiently established to define the national reference.
- Missing QI is explicitly zero; QR/QS/QG are not invented. Supersaturation is
  retained. Cold-cloud and precipitation spin-up remain interpretation limits.
- Prepared physical W is locally very large over the steepest resolved terrain:
  about -25 m s-1 in the winter forcing near the first 200 m AGL. HICAR's
  corresponding CFL-limiting physical component was about -21 m s-1 while the
  solver and conservation checks passed. This is inherited from the deliberate
  low-level terrain-following blend toward `u_grid*dh/dx+v_grid*dh/dy`, not a
  growing HICAR instability, but it is not qualified against observations and
  likely contributes to cold-cloud/precipitation adjustment. The present
  campaign qualifies horizontal surface-wind downscaling only; W, clouds and
  precipitation remain outside its scientific claims. Terrain-W conditioning
  is a dedicated follow-up sensitivity after the horizontal-wind result.
- The former blank `sst_var` held every lake at 280 K. HICAR still floors SST
  at 273.15 K, so frozen-lake physics is absent. Of 84,346 target water cells,
  10,773 (12.77%, about 431 km2) lack compact same-surface REA-L support. The
  former nearest-water fallback reached 55.67 km and produced multi-kW m-2
  turbulent heat fluxes over unsupported high-elevation water, so it is
  rejected. The selected `sst-local-baseline-v1` policy retains compact
  same-surface remapping where supported and otherwise uses the exact-valid-time
  monotone local all-surface RBF baseline. The unsupported-water mask and
  nearest same-surface candidate distance remain diagnostics; the remote value
  is not used. This is a defensible immediate approximation, not lake thermal
  physics. The corrected policy passed the finite two-hour daylight smoke; a
  prognostic lake model remains future work.
- Conditioned local RBF weights are required. An old nearly singular stencil
  produced a 320 m s-1 wind spike and is invalid. Operators with excessive L1
  amplification are rejected and vector bounds are checked.

### Model configuration

- RK3, CFL factor 1.6, third-order horizontal/vertical advection, FCT option 1,
  density transport and no extra constant-z diffusion.
- The fork's adjoint variational wind projection with fixed alpha 1, Sx 600
  m/30 degrees, TPI 4 km/200, 500 m smoothing, two passes, cap 2500 and hourly
  updates; thermal and linear-theory corrections off. Dynamic alpha remains a
  scientifically relevant mechanism but is excluded from this fork/domain
  reference because of the quantified initialization spikes above.
- Morrison, YSU, Noah-MP, revised-MM5, simple prescribed-water temperature,
  RRTMG and Noah-MP internal snow; no cumulus scheme. YSU top-down radiative
  mixing remains explicitly disabled.
- Noah-MP keeps untuned table phenology (`dveg=3`) and diagnosed albedo
  (`alb=2`), so external LAI/VEGFRA/static albedo are not campaign inputs.
- HICAR's revised-MM5 atmospheric surface layer is retained, while modular
  Noah-MP uses its supported surface-exchange option 1. The restored option 3
  is no longer part of the campaign baseline and need not be qualified before
  launch.
- Simple-water fluxes convert revised-MM5's exchange velocity to physical
  fluxes as `rho*CHS*dq` and `rho*cp_moist*CHS*dT`. The prior code treated CHS
  as dimensionless, multiplied wind a second time and omitted density, causing
  multi-kW m-2 latent fluxes over exposed lakes.
- RRTMG runs every 600 s. Terrain shading and direct/diffuse shortwave
  corrections are enabled after HLM/SVF/slope/aspect are generated; reflected
  shortwave and terrain longwave are disabled for the bounded first baseline.

## Established engineering foundations

- Namelist rendering resolves NZ and density advection and rejects unresolved
  tokens or incompatible vertical geometry.
- Static construction is reproducible and includes authoritative HHL/HFL.
  Invariant terrain/mask/soil fields and the WorldCover 2021 epoch land-use
  product are kept separate and are joined explicitly during initialization.
- Each selected regular forcing record is validated before its ready marker;
  sparse experiments still validate and publish the forcing/LBC pair jointly.
  Cache identity includes the complete runtime-domain hash.
- National input transformation uses eight deterministic fork workers on one
  exclusive 456 GB CPU node per record. The selected real-record benchmark
  fell from about 80 min serial to 15 min 58 s, with 239 GB peak RSS; every
  regular-forcing and LBC variable was bit-exact. Two and four workers took
  34 min 06 s and 21 min 32 s. Horizontal RBF application now uses contiguous
  32,768-target chunks without changing donor order or arithmetic. At national
  80-level/ten-donor scale this reduces each gather/product temporary from
  18.88 GB to at most 210 MB (about 90 times smaller); scalar, stacked-level
  and monotone outputs are exact and a bounded benchmark was time-neutral.
  This is qualified locally but deliberately not deployed into the active
  campaign; a future forcing deployment still needs one bounded real-record
  and end-to-end pilot.
- Target HFL is preserved exactly. The serialized top mass level includes the
  one-float32-ULP allowance needed to cover HICAR's runtime reconstruction.
- Land initialization uses native REA-L TERRA soil temperature/water, deep-soil
  temperature and snow amount/depth/density/bulk temperature. Frozen phase,
  glacier history and slow Noah-MP states remain approximate.
- Output/restart validators check exact expected times, selected physics,
  forcing turnover and terminal restart content.
- The 12-node/48-compute-rank RRTMG setup is bit-reproducible across independent
  GPU placements for a one-hour daylight trajectory and terminal restart. The
  run took 18 min 33--53 s including 34 GB restart publication, so a 12-hour
  segment is projected to fit the six-hour preemptible allocation with useful
  margin. With the final SST products, full-list and streaming segmented runs
  were bit-identical. Continuous versus restarted output separated first in
  land-surface/PBL fields at 10:10, but not in 10 m wind or radiation. At that
  time RMSE was 0.65 W m-2 for latent heat, 0.76 W m-2 for sensible heat,
  0.031 K for skin temperature and 40.6 m for PBL height: respectively 5.6%,
  2.5%, 3.6% and 24% of the uninterrupted ten-minute change. By 11:00 the
  ratios were 2.5%, 2.1%, 1.6% and 9.4%, and 10 m vector-wind RMSE was
  8.7e-9 m s-1. Local extrema warrant monitoring, but this is numerical restart
  reproducibility adequate for the present wind assessment, not bit identity.
- The final alpha-one, corrected-water daylight qualification completed all
  continuous, full-list segmented and segment-local-list trajectories. The two
  segmented modes were bit-identical in outputs and restart states; all fields
  were finite. Continuous versus restarted wind was exact at the first
  post-restart record and had vector RMSE `6.0e-9 m s-1` at the terminal
  record. Across all 13 continuous output slices, 10 m wind maxima were
  13.5--16.7 m s-1 and no cell exceeded 30 m s-1. This is the launch
  qualification for the bounded alpha-one R&D configuration.

## Selected campaign experiment

Each season is a single 48-hour physical trajectory split into four 12-hour
restartable segments. Four consecutive attempts on `preemptible` were
externally displaced after 22--71 minutes without model failures. The retained
workflow therefore uses one 12-node segment at a time on non-preempting
`normal`, preserving the qualified 60-rank topology. On the live 46-node
partition this consumes 26.1%; the controller derives the live capacity rather
than relying on that snapshot. A fail-closed 50% partition-share check and
global one-model concurrency bound make this operational choice explicit. The
first 24 hours are spin-up; the original 24-hour
event is evaluated:

| Season | trajectory | evaluation |
| --- | --- | --- |
| Winter | 2020-01-14 00 to 2020-01-16 00 UTC | Jan 15--16 |
| Spring | 2020-04-28 00 to 2020-04-30 00 UTC | Apr 29--30 |
| Summer | 2020-06-30 00 to 2020-07-02 00 UTC | Jul 1--2 |
| Autumn | 2020-10-01 00 to 2020-10-03 00 UTC | Oct 2--3 |

Essential station-comparison fields are written every 600 s. Six ending-
ten-minute HICAR samples form each SwissMetNet-compatible civil-hour mean;
HICAR grid-relative 10 m wind is inverse-rotated before vector verification.
REA-L interval means are transparently approximated from consecutive hourly
endpoints. Snow remains an endpoint state and precipitation an ending-hour
interval. HICAR and native REA-L use identical valid intervals and all usable
common stations. RMSE remains the headline; bias, MAE, standard-deviation ratio
and correlation explain scalar errors, while wind also uses speed bias/MAE and
vector RMSE. Stratification is limited to season, elapsed time,
elevation/terrain class and selected station-level diagnostics. With four
events, lead traces are event/diurnal diagnostics, not forecast-skill curves,
and correlations are descriptive rather than rankings. Daylight shortwave is
checked directly against SwissMetNet but excluded from HICAR-versus-REA-L
added-value ranking because the staged native REA-L reference lacks shortwave.
HICAR station sampling is restricted to the nearest land cell because all
SwissMetNet automatic stations are terrestrial. The evaluator records the
unconstrained and selected cells and their displacement, rejects shifts above
1 km, and keeps large overrides visible as representativeness warnings.
Post-spin-up tables retain the physical simulation lead and expose a separate
evaluation-relative lead: the scored ending-hour intervals are exactly 1--24
(physical leads 25--48). Footprint evidence uses the same six-sample hourly
completeness contract as the evaluator, so the final artifact no longer relies
on stale instantaneous-time fields.

The evaluation decision rule is fixed before seeing the campaign scores.
Vector-wind RMSE is primary and speed RMSE is co-primary. The decision cohort
is the exact intersection of station keys with exactly 24 common ending-hour
observation/HICAR/REA-L pairs for both metrics in all four events. It must
contain at least 20 stations. Each source report must independently show the
25 ordered inclusive hourly endpoints spanning the evaluation day, physical
lead-time metrics exactly 25--48 and therefore evaluation-relative leads
exactly 1--24. Station, aggregate and common-triplet counts must reconcile.
Missing or unequal evidence removes a station from the intersection; an
under-sized cohort or inconsistent source report fails the decision closed.

For each event and metric, the estimand is the equal-station network RMSE,
`sqrt(mean_i(RMSE_i^2))`, over that one fixed cohort. For
`delta = RMSE(HICAR) - RMSE(REA-L)`, only changes strictly larger than
`max(0.10 m s-1, 5% of REA-L RMSE)` are material; equality is neutral. Event
classification is strong for material vector/material speed improvement,
qualified for material vector improvement/neutral speed, neutral when both
are neutral, mixed for vector improvement/speed degradation or neutral
vector/speed improvement, and degraded for any material vector degradation or
for neutral vector/speed degradation. Speed cannot rescue a vector-degraded
event.

The campaign vector gate requires non-degradation in at least three of four
events, material improvement in at least two, a negative median over the fixed
cohort's station-event deltas, non-degrading vector medians in all four
leave-one-event-out views, and passing safeguards. One vector-degraded event
is therefore explicitly tolerated only when all those requirements still
hold; two vector-degraded events classify the campaign as degraded. The speed
gate used for strong added value requires non-degradation in at least three
events, material improvement in at least two and a negative fixed-cohort
station-event median. Strong added value requires both gates and at least two
jointly strong events. Qualified added value requires the vector gate, no
material speed degradation in any event and a non-degrading speed
station-event median. Repeated speed degradation alone does not force the
`degraded` label; without vector or safeguard degradation it is mixed.

Safeguards use the same fixed cohort. Ridge, valley and station elevation
>=1500 m must each retain at least 10 stations in every event. A safeguard
fails when equal-station vector RMSE degrades materially in the same stratum
in at least two events. A repeated safeguard failure or at least two national
vector-degraded events classifies the campaign as degraded. Degraded, neutral
and mixed outcomes all trigger the identically sampled interpolation-only
control next, with any safeguard failure reported; none opens a tuning matrix.
MAE, station-bias/centered-error decomposition, component correlation and
speed-variance ratio explain the primary result but cannot overturn worse
RMSE. Event ranges and leave-one-event-out directions are descriptive, without
p-values or climatological claims. One further physics or wind sensitivity is
run only if the diagnostics identify one coherent mechanism.

The national postprocessor evaluates these exact rules and fails closed when
required evidence is absent. The executable truth table, thresholds, cohort,
source reconciliation, leave-one-event-out gate and safeguards were fixed
before the seasonal scores were available.

## Remaining work

The final 15 km static is complete and contains HLM/SVF/slope/aspect computed
with HORAYZON from a 20.2 km exterior REA-L terrain band, EGM2008 and 90
azimuths. The 20 km ray extent remains a documented terrain-radiation
approximation, not a convergence claim. All four season-specific runtime
domains are complete. The clean final HICAR source is built with NVHPC/OpenACC
and NCCL; the executable is pinned by checksum.

1. All 196 hourly regular-forcing records are validated and ready. Advance the
   remaining restart-chain segments one qualified 12-node segment at a time on
   `normal`; six of sixteen segments are complete.
2. Evaluate the completed trajectories against all usable SwissMetNet stations
   and native REA-L-CH1, then decide from those results whether the bounded
   alpha-one projection provides useful downscaling or only a stable control.

Do not tune individual parameters until this reference result identifies a
specific scientific ambiguity. Do not resume open-ended RRTMGP debugging while
the bounded RRTMG qualification can answer the campaign decision directly.
