# ICON-to-HICAR scientific assessment

## Current conclusion

The project has a literature-led reference configuration and a qualified
bit-reproducible GPU radiation path, but it does not yet have a scientifically
evaluated national all-season result for that complete configuration.

Fresh independent daylight replicas showed that the selected one-block RRTMGP
configuration still separates after repeated radiation calls, despite being
exact through the first output times. The former one-block qualification is
therefore withdrawn. The bounded RRTMG reference keeps the rest of the setup
fixed while avoiding the RRTMGP implementation path. Two independent
12-node/48-compute-rank daylight replicas were exact for all 30 output
variables at seven 10-minute records (976,215,334 values) and all 198 terminal
restart variables (8,497,193,716 values). All fields were finite and daytime
shortwave and longwave were active. A two-hour continuous-versus-segmented
restart proof with the corrected SST products is still required.

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
  physics. Rebuild into the versioned forcing directory and repeat the finite
  daylight smoke before launching the campaign; a prognostic lake model remains
  future work.
- Conditioned local RBF weights are required. An old nearly singular stencil
  produced a 320 m s-1 wind spike and is invalid. Operators with excessive L1
  amplification are rejected and vector bounds are checked.

### Model configuration

- RK3, CFL factor 1.6, third-order horizontal/vertical advection, FCT option 1,
  density transport and no extra constant-z diffusion.
- The fork's adjoint variational wind projection, dynamic Froude alpha in the
  published 0.1--1 range, Sx 600 m/30 degrees, TPI 4 km/200, 500 m smoothing,
  two passes, cap 2500, hourly updates; thermal and linear-theory corrections
  off.
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
  34 min 06 s and 21 min 32 s. Horizontal RBF remapping is now the largest
  remaining preprocessing target, but is not a launch blocker.
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
  margin. Exact segmented restart equivalence remains to be shown with the
  final SST products.

## Experiment now being executed

Each season is a single 48-hour physical trajectory split into four 12-hour
preemptible segments. The first 24 hours are spin-up; the original 24-hour
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

## Remaining work before launch

The final 15 km static is complete and contains HLM/SVF/slope/aspect computed
with HORAYZON from a 20.2 km exterior REA-L terrain band, EGM2008 and 90
azimuths. The 20 km ray extent remains a documented terrain-radiation
approximation, not a convergence claim. All four season-specific runtime
domains are complete. The clean final HICAR source is built with NVHPC/OpenACC
and NCCL; the executable is pinned by checksum.

1. Complete the RRTMG qualification with a two-hour
   continuous-versus-restart proof using the rebuilt local-baseline SST
   products, regular forcing relaxation, Noah-MP surface option 1, and the
   complete final physics/static/input configuration.
2. Stream validated regular forcing records at segment granularity while the
   smoke and campaign trajectories advance.
3. Launch the four independent restart chains on `preemptible`, then evaluate
   them against all usable SwissMetNet stations and native REA-L-CH1.

Do not tune individual parameters until this reference result identifies a
specific scientific ambiguity. Do not resume open-ended RRTMGP debugging while
the bounded RRTMG qualification can answer the campaign decision directly.
