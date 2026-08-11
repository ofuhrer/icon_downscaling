# ICON-to-HICAR scientific assessment

## Current conclusion

The project has a practical, bit-reproducible GPU runtime and a literature-led
reference configuration, but it does not yet have a scientifically evaluated
national all-season result for that complete configuration.

The GPU nondeterminism was triggered by using multiple RRTMGP column blocks per
compute rank. On the selected 2061 x 1431 domain and 12-node/48-compute-rank
layout, `rrtmgp_block_N=256` gives one block per rank. Four independent one-hour
replicas were bit-exact in every output slice and all 196 restart variables. A
00--08 UTC continuous run and a 00--04 + 04--08 segmented run were also
bit-exact, including after sunrise. This is the campaign runtime; the default
two-block path remains unqualified. The literal block size is topology-specific
and must be rechecked if the domain or decomposition changes.

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
- Sparse lateral relaxation supplies only T/P/QV/QC/QI on mass-grid support,
  using a provisional 10 km shoulder and 3600 s timescale. Sparse U/V were
  removed because they were inserted earth-relative into grid-relative winds
  after projection; sparse W is excluded to preserve the balanced field.
- Missing QI is explicitly zero; QR/QS/QG are not invented. Supersaturation is
  retained. Cold-cloud and precipitation spin-up remain interpretation limits.
- The former blank `sst_var` held every lake at 280 K. The reference prescribes
  hourly REA-L skin temperature over source-water support. HICAR still floors
  SST at 273.15 K, so frozen-lake physics is absent. The first five real SST
  remaps found that 10,773 of 84,346 target water cells (12.77%) lacked water
  support in the compact REA-L stencil and used a global same-surface fallback,
  with a maximum distance of 55.67 km. Exact fallback masks and distances are
  now retained in every forcing record; their lake/station geography must be
  assessed before scaling the campaign.
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
  RRTMGP and Noah-MP internal snow; no cumulus scheme.
- Noah-MP keeps untuned table phenology (`dveg=3`) and diagnosed albedo
  (`alb=2`), so external LAI/VEGFRA/static albedo are not campaign inputs.
- Land drag uses the published revised-MM5 option 3. Its missing modular
  Noah-MP implementation is restored by a checksum-pinned HICAR patch and
  matches archived HICAR v2 scalar results bit-for-bit. The clean final
  NVHPC/OpenACC/NCCL executable is built; its option-3 runtime fields and the
  complete-physics restart trajectory remain to be checked in the daylight
  smoke test.
- RRTMGP runs every 600 s with one block per rank. Direct, diffuse and reflected
  shortwave plus terrain longwave are enabled after HLM/SVF/slope/aspect are
  generated. The reflected-shortwave cadence defect is fixed in the selected
  HICAR source.

## Established engineering foundations

- Namelist rendering resolves NZ and density advection and rejects unresolved
  tokens or incompatible vertical geometry.
- Static construction is reproducible and includes authoritative HHL/HFL.
  Invariant terrain/mask/soil fields and the WorldCover 2021 epoch land-use
  product are kept separate and are joined explicitly during initialization.
- Forcing and sparse boundaries are jointly validated before ready markers;
  cache identity includes the complete runtime-domain hash.
- Target HFL is preserved exactly. The serialized top mass level includes the
  one-float32-ULP allowance needed to cover HICAR's runtime reconstruction.
- Land initialization uses native REA-L TERRA soil temperature/water, deep-soil
  temperature and snow amount/depth/density/bulk temperature. Frozen phase,
  glacier history and slow Noah-MP states remain approximate.
- Output/restart validators check exact expected times, selected physics,
  forcing turnover and terminal restart content.
- The 12-node/48-compute-rank one-block GPU path is qualified for A/A and
  continuous-versus-segmented daylight execution.

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

1. Finish and validate one real hourly W/SST/scalar-LBC product, including the
   spatial impact of SST global fallbacks and HICAR ingestion, before producing
   all 196 seasonal hours.
2. Repeat a daylight turnover and continuous-versus-restart proof with the
   complete final physics/static/input configuration; also verify the restored
   revised-MM5 option-3 state identities in the resulting restart.
3. Launch the four independent restart chains on `preemptible`, then evaluate
   them against all usable SwissMetNet stations and native REA-L-CH1.

Do not tune individual parameters until this reference result identifies a
specific scientific ambiguity. Do not reopen the multi-block RRTMGP mapping
investigation unless the selected topology can no longer use one block/rank.
