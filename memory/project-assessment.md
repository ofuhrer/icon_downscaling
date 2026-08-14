# ICON-to-HICAR scientific assessment

## Current conclusion

The project has a literature-led reference configuration, a GPU path that is
bit-reproducible across independent placements, and a completed four-event
national evaluation. Segmented restarts are numerically, not bitwise,
reproducible. The preregistered wind decision is **mixed**, not added-value
qualification: vector RMSE is non-degrading in all four events and materially
better in autumn, but scalar speed RMSE is materially worse in winter, spring
and summer. The interpolation-only control is complete and reproduces the
evaluator's native-REA-L station RMSEs to `1.8e-15 m s-1`. It is neutral versus
REA-L in winter, spring and summer but materially worse in autumn. HICAR adds
no material vector benefit over interpolation in the first three events and
materially degrades their speed, while it materially improves both speed and
vector error over interpolation in autumn. This isolates an amplitude-damping
tradeoff rather than robust four-event dynamical added value. Bounded
Sx/TPI-off restart sensitivities now establish immediate causal over-damping in
both the spring weak-wind and autumn strong-wind events. In spring, national
speed RMSE improves materially from 1.800 to 1.627 m s-1 while vector RMSE
improves neutrally from 2.254 to 2.200 m s-1. In autumn, speed improves
materially from 2.042 to 1.753 m s-1 while vector RMSE remains neutral at
2.605 versus 2.613 m s-1. The autumn sensitivity is neutral versus REA-L for
speed (1.830 m s-1) and retains a material vector advantage over REA-L
(2.613 versus 2.839 m s-1). High-elevation and ridge speed also improve
materially. Alpha-one HICAR without bundled Sx/TPI is therefore the selected
next R&D reference. This bounded one-hour evidence establishes mechanism, not
four-event skill; a fresh multi-event Sx/TPI-off integration is now running
sequentially on the capacity-bounded `normal` partition and requires its locked
evaluation before production use. Its first matched winter continuation block
already confirms a broad, bounded amplitude effect: over 68,262,600 core-land
samples from 16:10--20:00, Sx/TPI-off is faster than the otherwise identical
Sx/TPI-on control in 89.7% of 10 m samples and 88.9% of 50 m samples, with
mean speed changes of +0.286 and +0.233 m s-1 respectively. Neither case has a
value above 30 m s-1. This supports removal of excessive damping but is not a
station-skill result. At the exact +24 h evaluation-start endpoint, Sx/TPI-off
also reduces full-domain speed RMSE against the vertically interpolated
forcing from 1.021 to 0.643 m s-1 at 10 m and from 0.907 to 0.579 m s-1 at
50 m; correlations increase from 0.925/0.964 to 0.965/0.983. This confirms a
closer target-grid amplitude/structure match, not observational skill. The
effect persists into the first scored 00:10--04:00 block: Sx/TPI-off is faster
in 90.2% of 10 m and 89.5% of 50 m core-land samples, by +0.315 and
+0.262 m s-1 on average, while both variants remain below 30 m s-1.

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
- The fork's adjoint variational wind projection with fixed alpha 1, bundled
  Sx/TPI modification off, two passes, cap 2500 and hourly updates; thermal and
  linear-theory corrections off. The former Sx 600 m/30 degree, TPI 4 km/200 m
  and 500 m smoothing setup materially over-damped speed in exact spring and
  autumn +24 h sensitivities. Dynamic alpha remains a scientifically relevant
  mechanism but is excluded from this fork/domain reference because of the
  quantified initialization spikes above.
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
  and monotone outputs are exact. A full 2020-07-01 23 UTC national-record
  qualification completed in 16 min 04 s and its 2,383,027,129 values
  (9.56 GB encoded) were bit-identical to the production record; dimensions
  and every variable attribute also matched. Only temporary source paths and
  the checksum of the independently regenerated, scientifically identical SST
  provenance product differed as global metadata. On this same timestamp the
  horizontal-remap stage fell from 1,830.35 to 262.15 s (7.0 times faster),
  transform time from 2,225.37 to 658.54 s (3.4 times faster), and end-to-end
  time from 42 min 12 s to 16 min 04 s. Other unchunked records had already run
  near 16 min, so this proves removal of a severe worst-case remap stall rather
  than a general 2.6-times throughput expectation. The fork-worker stage's
  directly observed cgroup memory peaked near 62.5 GB, but Slurm's copy-on-write
  aggregate RSS changed only from 240.7 to 238.2 GB. Chunking is therefore
  qualified as an exact, low-risk temporary-allocation and worst-case-latency
  improvement, not as evidence that two workers can safely share one node. It
  remains deliberately absent from the immutable active-campaign forcing
  cache; use it for future input generation without regenerating already-ready
  records.
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
  segment was initially projected to fit the six-hour allocation. The first
  completed full national campaign segment now replaces that extrapolation:
  12 simulated hours took 4491 s of HICAR time (9.62 simulated hours per wall
  hour) and 1 h 18 min 28 s including wrapper work. RRTMG radiation consumed
  3413 s, or 76.0% of total model time; advection used 14.7%, halo wait 3.0%,
  input plus output 1.17%, and wind balance 0.16%. Radiation is therefore the
  first performance target if this physics stack is retained, but its locked
  600 s cadence must remain unchanged during the four-event comparison. With
  the final SST products, full-list and streaming segmented runs were
  bit-identical. Continuous versus restarted output separated first in
  land-surface/PBL fields at 10:10, but not in 10 m wind or radiation. At that
  time RMSE was 0.65 W m-2 for latent heat, 0.76 W m-2 for sensible heat,
  0.031 K for skin temperature and 40.6 m for PBL height: respectively 5.6%,
  2.5%, 3.6% and 24% of the uninterrupted ten-minute change. By 11:00 the
  ratios were 2.5%, 2.1%, 1.6% and 9.4%, and 10 m vector-wind RMSE was
  8.7e-9 m s-1. Local extrema warrant monitoring, but this is numerical restart
  reproducibility adequate for the present wind assessment, not bit identity.
  The first real Sx/TPI-off national restart independently shows no wind jump:
  over 2,928,375 core cells, the 12:00--12:10 vector-increment RMSE is
  0.107 m s-1 at 10 m and 0.123 m s-1 at 50 m, respectively 0.90 and 0.93
  times the larger adjacent ten-minute increment. All four 11:50--12:20
  states are finite; the known +0.432 s serializer offset was normalized only
  for matching timestamps.
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

All 16 campaign segments completed and passed their terminal validators. The
final evaluation uses a fixed 147-station intersection, with 24 exact common
ending-hour pairs for both wind metrics in every event. Its equal-station
results are:

| Event | speed delta HICAR-REA-L (m s-1) | vector delta (m s-1) | classification |
| --- | ---: | ---: | --- |
| DJF | +0.169 | -0.035 | degraded |
| MAM | +0.341 | +0.085 | degraded |
| JJA | +0.314 | -0.035 | degraded |
| SON | +0.034 | -0.474 | qualified |

Vector leave-one-event-out station-event medians improve under every omission,
and ridge, valley and >=1500 m safeguards pass without repeated broad vector
regression. The campaign nevertheless misses the vector replication gate (one
material improvement rather than two) and speed support is degrading. This is
evidence that alpha-one HICAR changes vector direction beneficially in the
autumn event but does not establish robust four-event value, while its speed
amplitude is generally worse than native REA-L.

The identically sampled interpolation-only control answers the required first
causal question. Its fixed cohort is the same 147 stations; every event has 25
matched endpoints and 24 scored intervals. The independently reconstructed
REA-L station RMSEs agree with the evaluator for all 1,176 season/station/
metric comparisons to a maximum absolute difference of `1.78e-15 m s-1`.
Equal-station network RMSEs are:

| Event | metric | interpolation | HICAR | REA-L | HICAR - interpolation |
| --- | --- | ---: | ---: | ---: | ---: |
| DJF | speed | 1.897 | 2.128 | 1.960 | +0.231 |
| DJF | vector | 2.600 | 2.625 | 2.660 | +0.025 |
| MAM | speed | 1.461 | 1.800 | 1.459 | +0.338 |
| MAM | vector | 2.287 | 2.360 | 2.275 | +0.073 |
| JJA | speed | 1.502 | 1.836 | 1.522 | +0.334 |
| JJA | vector | 2.359 | 2.341 | 2.376 | -0.019 |
| SON | speed | 3.699 | 3.164 | 3.130 | -0.535 |
| SON | vector | 5.513 | 4.605 | 5.079 | -0.908 |

In DJF/MAM/JJA the control's mean station speed-standard-deviation ratio is
0.92--0.95, whereas HICAR compresses it to 0.60--0.67 and adds roughly
0.6--0.84 m s-1 of mean negative speed bias. In SON the control and REA-L are
too variable (ratios 1.45 and 1.39); HICAR reduces the ratio to 1.05. Correlation
changes little. A few exposed or high-elevation stations dominate the autumn
national control penalty: at MRP, for example, control mean speed is 15.54
m s-1 versus 5.45 observed and 10.61 REA-L, while HICAR reduces it to 5.96.
ATT, GOR, PIL, COV, SAE and related sites show the same direction. The evidence
therefore points to excessive control wind amplitude over resolved terrain and
a HICAR damping mechanism that is useful in this strong event but over-active
in the other three.

## Remaining work

The final 15 km static is complete and contains HLM/SVF/slope/aspect computed
with HORAYZON from a 20.2 km exterior REA-L terrain band, EGM2008 and 90
azimuths. The 20 km ray extent remains a documented terrain-radiation
approximation, not a convergence claim. All four season-specific runtime
domains are complete. The clean final HICAR source is built with NVHPC/OpenACC
and NCCL; the executable is pinned by checksum.

1. Complete the running fresh four-event campaign with alpha one and bundled
   Sx/TPI off. It reuses the validated executable, statics and ready forcing
   cache and keeps the same 48 h trajectories, 24 h spin-up, four 12 h
   segments, 12-node topology, RRTMG and regular boundary relaxation so the
   intervention remains isolated.
2. Evaluate that fresh campaign on the locked 147-station cohort and the
   preregistered vector/speed and terrain safeguards. Until it passes the full
   multi-event decision, retain interpolation-only as the operational baseline
   and describe Sx/TPI-off HICAR as the scientifically preferred candidate.

The spring sensitivity intentionally changes `wind/Sx` across a validated
restart. Ordinary restarts remain fail-closed. The experiment uses the explicit
restart override, records the sole `T` to `F` mismatch, and separately allows
only the known missing `domain.height_lowest_level` provenance attribute from
the older 4a425677 executable. Wrong or non-finite numeric attributes still
fail validation.

Do not tune individual parameters until this reference result identifies a
specific scientific ambiguity. Do not resume open-ended RRTMGP debugging while
the bounded RRTMG qualification can answer the campaign decision directly.
