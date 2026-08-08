# ICON-to-HICAR scientific R&D assessment

## Current mode and target question

The project is in **scientific R&D / strategy-discovery mode**. The target is
an evidence-backed answer to:

> What ICON-to-HICAR downscaling strategy should we use, why, under which
> conditions, and what important uncertainties remain?

Production qualification, generalized orchestration, immutable releases,
archive contracts, exhaustive provenance, and long-campaign hardening are
deferred until that strategy is scientifically convincing.

The primary application is wind downscaling from ICON REA-L-CH1 at 200 m and
possibly 100 m over Switzerland. HICAR remains fully coupled in the current
experiments; a wind-focused product is not a `wind_only` model configuration.

## Working scientific understanding

The best-supported provisional wind strategy is a sequence of independently
initialized, fully coupled 72-hour HICAR windows, discarding the first 48
hours and retaining one deterministic 24-hour core from the oldest eligible
window. Overlaps quantify origin-age uncertainty. Do not taper or blend
phase-shifted turbulent fields.

The 72/48/24-hour layout is the policy tested by the existing overlap
experiment, not a fixed production design. Window length, overlap, warm-up,
and retained-core ownership remain choices to define from contrasting-regime
evidence later.

This is supported for one summer Alpine bridge case only. It is not a valid
strategy for surface temperature, soil, snow, water budgets, precipitation,
or a general continuous coupled trajectory. A long restart-continuous HICAR
land trajectory is not the default remedy: it would serialize production and
could drift away from the ICON land climatology. The active strategy question
is whether materially better independent cold starts can retain parallel,
preemptible windows without the demonstrated slow-state reset bias.

The current configuration starting point is the discretely adjoint
variational wind solver, 80 levels, 12 km model top, and SLEVE decay 2/6 on
the validated 200 m geometry. It is a tested baseline, not a frozen scientific
choice. The 60-level version is available as a cheaper control when vertical
resolution is not part of the question.

## Meteorological preprocessing direction

The selected architectural direction is a greenfield, target-model-aware
`hicarprep`, based on source review of WPS/WRF, int2lm, ICON, and EXTPAR. It is
designed to transform native ICON profiles and HHL directly onto exact HICAR
mass and U/V columns plus HHL interfaces, perform explicit coarse/fine-terrain profile
reconstruction, hydrostatically adjust the state with HICAR's discrete
operator, apply the HICAR variational wind projection, and emit complete
target-native IC and sparse LBC products from the same transformation.

The implementation under `preprocessing/hicarprep` now provides a strict field
lifetime registry; separate immutable, epoch/climatology/time-series, and
initial-surface products; exact HICAR SLEVE HHL/HFL; cached native-grid
Gaussian-kernel RBF solves with int2lm scaling; vector-normal RBF wind
reconstruction; top-to-bottom ICON normalization; terrain-aware generalized-RH
`QV/QC` reconstruction; condensate-aware hydrostatic pressure; terrain/top W
adjustment; and same-state, valid-time-labeled sparse IC/LBC encoding. Analytic
and synthetic NetCDF tests cover those contracts. Valid-time soil/snow/skin
state and evaluated epoch/monthly/continuous external fields can now be merged
at the exact atmospheric time into the variable names HICAR actually reads;
surface preparation and assembly bind the same external-product hash.
Native-grid SMI is the provisional default,
relative saturation is a first-class alternative, and absolute VWC is retained
only as a diagnostic control. This follows the actual int2lm dimensionless
transfer policies and ICON's SMI-based nest transfer; it is not a claim that
current evidence has selected SMI over relative saturation. All water species are jointly converted
to HICAR's dry-air basis, cross-product land closure is checked, and ordered
LBC sequences have a strict offline validator. The shared cold-start core,
`--initialize-only` driver, residual diagnostics, exact-staggering loader, and
state/time/producer-bound certificate path are implemented. Independent winter,
summer, and Storm Sabine 701x701x80 REA-L states pass the real GPU-NCCL path.
The storm snapshots at 00/01/02Z have maximum hydrostatic residuals
`4.0648e-4`, `3.8800e-4`, and `3.7469e-4`; all wind-matrix and continuity gates
pass. This independently supports retaining the provisional `5e-3` gate with
roughly twelve-fold observed margin, but does not justify tightening it from
three adjacent storm snapshots. Certified storm states also pass direct
IC/LBC publication with separate mass, U-face, and V-face frames.

Operational REA-L soil/snow/skin and atmospheric GRIB decoding into the
canonical native-grid schemas is implemented and exercised on archived data.
The atmospheric decoder enforces the exact 561 dynamic plus 83 geometry
message inventories, parameter IDs, units, level types and inventories,
reference/valid times, step metadata, native-grid UUID, cell count, vertical
ordering, and EXTPAR identity. REA-L has no QI field: decoding fails by default
and permits zero QI only through an explicit provenance-marked
`source-absent-zero` policy; QC is never reinterpreted as ice.

HICAR now has a sparse, time-bracketing LBC runtime reader. It retains only the
two active endpoint records per rank, reads only contiguous local mass/U/V
support runs, rejects cadence, coverage, identity, support, weight, schema, and
balance-contract changes, and advances only at exact forcing events without
skipping or extrapolation. It interpolates the authoritative `T/P/U/V` and
water basis in absolute time, derives potential temperature from interpolated
T/P, keeps water nonnegative, respects native U/V staggering, applies exact
edge assignment plus the stored physical-distance shoulder, and refreshes
HICAR diagnostics after application. A two-node, two-hour Storm Sabine run at
HICAR commit `5d5574959f5c` crosses the 01Z bracket turnover, writes all 13
ten-minute records, and passes chunked finite/range checks for thermodynamics,
water, density, and U/V/W. The diagnosed vertical-wind extrema (`w` about
`-50.7..45.4 m s-1`, terrain-coordinate `w_grid` about `-69.8..77.8 m s-1`)
are finite and do not destabilize the run, but remain a scientific diagnostic
for relaxation-width and lateral-W-policy experiments rather than evidence
that the present boundary policy is physically optimal.

Do not preserve the current regular forcing-grid NetCDF or HICAR runtime
horizontal/vertical interpolation as the future public input contract. Target
static terrain, fractions, and categories remain independently compiled and
authoritative. The validated full bridge static retains 24-class subgrid
fractions and four depth-dependent soil textures; the current HICAR source can
consume the latter through `soiltexture_var` with `nmp_opt_soil=2`. Soil state
must be bounded and physically plausible, but soil
moisture conservation is not a requirement. The evidence, implementation
design, status, and remaining gate are in
`docs/icon-to-hicar-meteorological-preprocessing-design.md` and
`preprocessing/README.md`; the execution plan is in
`docs/hicarprep-blocker-implementation-plan.md`.

## Established engineering foundations

- HICAR commit `6bd302f8b97062cd43c1b8d4e59bd3cf0dc8ae07` restores application of
  the adjusted horizontal-wind tendency. Native `u`/`v` and every requested
  fixed-height wind evolve under nonstationary forcing.
- The corrected source passes the four-GPU halo tests and a cross-node
  split/restart comparison across all 193 restart variables and 13 tested
  output variables.
- The 200 m 80-level SLEVE 2/6 national geometry, four-node NCCL layout,
  forcing conversion, REA-L land initialization, fixed-height output, and core
  numerical diagnostics have representative evidence.
- Short restart-linked scheduler jobs can preserve a coupled trajectory. A
  seven-day repeated-day run completed as separate two-node jobs with all six
  handoffs valid.
- The pre-emptible controller, shared forcing cache, ready markers, rolling
  retirement, and durable manifests are available when an experiment benefits
  from them. They are infrastructure, not prerequisites for scientific
  acceptance.

## Latest experiment: chronological summer overlap

### Uncertainty and hypotheses

- H1: after 48 hours, independently initialized coupled windows reproduce the
  continuous wind/PBL trajectory closely enough for a wind product even when
  slow land state differs.
- H2: reset land/surface state materially contaminates wind after 48 hours, so
  the model must run as one continuous trajectory or use a different cycling
  strategy.

### Minimal discriminating experiment

On the 701 x 701 Alpine bridge, three daily-origin 72-hour windows and one
five-day restart-continuous reference used chronological 2020-07-01 through
2020-07-06 REA-L forcing, origin-specific REA-L land/snow initialization,
30-minute output, identical corrected HICAR source, and no blending. The 56
six-hour model segments all completed and published interpretable output.

### Result

H1 is supported for summer wind, while the same strategy fails as a general
coupled-state method.

- Both genuinely reset retained cores passed all predeclared wind and PBL
  thresholds. Against the continuous reference, 10 m vector RMSE was `0.0179`
  and `0.0269 m s-1`; 100 m RMSE was `0.00571` and `0.00670 m s-1`; 10 m
  timewise p99 error was `0.0467` and `0.0747 m s-1`.
- PBL-height relative RMSE was `0.0588` and `0.0631`, below the `0.15`
  diagnostic threshold. Wind seam-excess RMSE was at most `0.0207 m s-1` at
  10 m and `0.00449 m s-1` at 100 m.
- Reset slow state was material: soil-column-water mean bias was `-3.14` and
  `-7.46 kg m-2`, soil-temperature RMSE was `0.376` and `0.583 K`, and
  soil-water RMSE was `0.0111` and `0.0163 m3 m-3`.
- Surface-temperature RMSE was `0.735` and `0.947 K`; seam excess was `0.639`
  and `0.517 K`. The sensible- and latent-heat mean biases themselves remained
  small, so the causal path from reset stores to wind is not established.
- Same-valid-time origin-age uncertainty is larger than selected-core bias:
  pairwise 10 m wind RMSE spans `0.0787--0.1283 m s-1`. This is useful
  uncertainty information and argues for deterministic oldest-core ownership.
- The duplicate same-initial-state control failed an intentionally near-exact
  pointwise check, but its bulk differences were much smaller than the reset
  signal: 10 m RMSE `0.00308 m s-1`, surface-temperature RMSE `0.0278 K`, and
  soil-temperature RMSE `0.00338 K`. Rare localized nondeterministic extremes
  remain an engineering/numerical uncertainty; they do not explain the reset
  land-state differences.

The archived assessor status `PASS` means the analysis executed successfully;
its `RESET_STATE_BIAS_MATERIAL` decision correctly rejects a single method for
all coupled fields. During R&D it does not override the separate positive wind
result.

## Soil-transfer evidence and active cold-start question

The legacy initializer is now known to use native ICON GRIB -> fieldextra
nearest-neighbour 0.01-degree grid -> Python bilinear HICAR interpolation. It
interpolates absolute `W_SO` mass across different source and target soil
classes, performs no topographic temperature correction, and does not use
source land cover or root depth. This is a usable baseline, not a defensible
final cold-start method.

The first direct native-grid candidate is implemented and statically valid:

- `fdb/5.21:v1` supplies EarthKit Data `1.0.2`, EarthKit Geo `1.0.0`, MIR
  `1.29.0.21`, and current ecCodes definitions. Direct ICON -> 701 x 701 HICAR
  point interpolation is operational; one-time linear and nearest-neighbour
  MIR weights persist and subsequent complete dates take about 40--48 seconds.
- The operational tuned ICON-CH1-EPS EXTPAR file
  `/oprusers/osm/opr.inn/data/ICON_INPUT/ICON-CH1-EPS/external_parameter_icon_grid_0001_R19B08_mch_tuned.nc`
  has the exact REA-L native-grid UUID
  `17643da2574959b644d254a3cd6e2bc0` and checksum
  `30b3eb5a74b5498a8ba18709cc2a482dbb212f7496d5328836470cf1fd0cb471`.
  It supplies `SOILTYP`, `ROOTDP`, land fractions, 23-class land cover, and
  source topography. REA-L FDB supplies `W_SO` but not archived `SMI`,
  `W_SO_ICE`, or `ROOTDP` for the tested dates.
- Source SMI is derived on the native grid with the exact ICON TERRA
  soil-class wilting-point/field-capacity tables, land-normalized across
  sea-mask boundaries, remapped vertically by layer overlap, and reconstructed
  with the target HICAR soil class and the exact production NoahMP STAS table
  checksum `211649cf09e11081637003e0f185172e90d362cdde701ed4bae1d324c6c4b541`.
  Land cover affects rooting and later evolution, but neither ICON nor NoahMP
  defines plant wilting point from land cover; that hydraulic property is soil
  class dependent.
- This is soil-type-aware but not yet land-cover-aware initialization. The
  direct intermediate carries ICON dominant land cover and `ROOTDP`, but the
  initializer does not use them; HICAR retains its ESA WorldCover -> USGS
  target vegetation class. With the current default `nmp_dveg=3`, NoahMP uses
  its USGS monthly LAI table and computes vegetation fraction from LAI/SAI.
  REA-L's documented native-grid time-constant `LAI` and `PLCOV` fields and
  the tuned EXTPAR `LAI_MX`/`PLCOV_MX` are authoritative source candidates,
  but are not ingested by the current cold start.
- The candidate initializes only bulk surface temperature, four-layer soil
  temperature/total water, SWE, and snow height. NoahMP partitions soil
  liquid/ice and reconstructs snow layers from those bulk inputs. Canopy
  interception, snow history/age and impurities, groundwater/aquifer stores,
  and vegetation biomass/carbon stores still take NoahMP cold-start defaults;
  a restart-continuous trajectory preserves these additional stores.
- The 2 and 3 July candidate statics pass finite/range checks on 474,994 land
  cells without a second spatial interpolation. They are about `58.48` and
  `59.09 kg m-2` wetter in mean active-soil column water than the legacy
  absolute-water statics. This large intervention is a warning as well as a
  test: the SMI transform is not promoted until the coupled response is known.
- Source and HICAR topography differ by `100.9 m` RMS and `215.8 m` at the
  absolute p95 over land. A fixed `-6.5 K km-1` surface correction would have
  `0.656 K` RMS, `1.403 K` absolute p95, and isolated corrections up to
  `7.24 K`. The capability is implemented but deliberately excluded from the
  current SMI experiment; it requires a separate bounded A/B rather than being
  silently combined with the soil intervention.

Campaign `native-land-cold-start-july-v1` completed all 24 six-hour segments
for the 2 and 3 July origins with the corrected production HICAR pin. It found
that its specific direct-native SMI initialization was less compatible with a
legacy-initialized continuous HICAR trajectory:

- At model age 48--72 h, mean soil-column-water bias increased from
  `3.14/7.46` to `42.41/38.90 kg m-2`, soil-water RMSE from
  `0.0111/0.0163` to `0.0624/0.0624 m3 m-3`, and soil-temperature RMSE from
  `0.376/0.583` to `0.797/1.066 K` for the 2/3 July origins.
- Surface-temperature RMSE increased from `0.735/0.947` to `1.260/1.483 K`.
  Wind and PBL remained inside the frozen absolute limits and wind evolved
  normally, but all wind-height RMSEs and PBL RMSE also worsened; both selected
  seams failed only the `0.5 K` surface-temperature limit.
- At 72 h the candidate remained farther than the legacy reset from the
  continuous reference in liquid soil water, SWE, snow layers, canopy stores,
  groundwater/aquifer stores, and canopy/snow temperatures. This reference is
  a legacy-initialized continuous HICAR trajectory, not observational truth,
  but it is the correct measure of reset compatibility for this experiment.

This is useful compatibility evidence, but it is not an adequate general
rejection of SMI. The reference trajectory inherited the legacy absolute-water
initializer, so closeness to it partly rewards legacy-like water states; it is
not observational truth or an independent land-state constraint. The test also
did not compare int2lm's other policy, relative saturation. Retain the exact
result and provenance, but withdraw the earlier universal production
conclusion.

Fresh source audit and input-only tests establish a stronger starting point:

- int2lm transfers either `max((theta-PWP)/(FC-PWP),0)` or
  `theta/porosity`, then reconstructs with target soil hydraulics; it does not
  transfer direct absolute VWC across soil classes;
- ICON nesting converts parent soil water to SMI before interpolation and
  reconstructs it with child-grid hydraulics; external initialization prefers
  SMI when present and treats `W_SO` as fallback;
- canonical REA-L native-surface generation and direct ICON-to-200 m HICAR
  interpolation pass winter (2020-01-15), summer (2020-07-02), and late-autumn
  (2020-11-15) plausibility tests for SMI, relative saturation, and the
  absolute diagnostic control. The report is
  `case_studies/swiss_200m/validation/hicarprep_surface_multicase_2020_v4.json`;
- the definitive full static uses one hash for all cases, preserves the
  selected 200 m terrain bitwise, closes 24-class fractions to `1.45e-7`, has
  395,197 mixed-category cells, and contains four-layer SoilGrids texture
  changes in 22,400/67,404/91,396 cells below the surface layer;
- relative saturation clipped 0--0.0142% of active target-soil layers, SMI
  clipped 2.37--2.99%, and the absolute control clipped 0.257--0.708%. SMI
  produced a wetter state than relative saturation, with 0.0646--0.0665
  m3 m-3 VWC RMSE between them. Every method passed numerical, full-grid
  temperature, snow, hydraulic-range, provenance, and fallback-distance
  checks; the maximum fallback distance is 2.581 km. The only warnings now
  make explicit that the 2021 WorldCover epoch is a representative proxy for
  these 2020 research cases rather than silently back-extrapolated land cover.

These diagnostics establish sensitivity and input validity only. They do not
select a preferred method. SMI remains the provisional default because it has
the clearest cross-model precedent and preserves plant-available hydraulic
state; relative saturation must remain equally easy to select until controlled
HICAR-response evidence distinguishes them.

The actual HICAR reader path is now qualified with an isolated minimal build:
commit `e46671bccb628c43ea87a50630c4a041bb10bc84` is b514 plus the five
depth-varying-soil integration changes, and GPU-NCCL executable SHA256
`379f26c2d3eafd168bbf5c4db271e179ea4cf0d948c68ab46bb0081f3c30d284`
advertises and reads `soiltexture_var`. Winter and summer SMI/relative arms all
completed and produced evolved 10-minute output. On active non-glacier soil,
median VWC was 0.3283 versus 0.2969 in winter and 0.3330 versus 0.2991 in
summer. Temperature, snow, soil-water, checksum, timestamp, and
source-identity screens passed. Both winter arms have one nearly
method-independent urban-grid-cell sensible-heat-flux tail of about
`-505 W m-2`; it is retained as an explicit startup-plausibility warning even
though all hard bounds pass. The evidence is
`case_studies/swiss_200m/validation/hicarprep_coupled_smoke_2020_v1.json`.
This is ingestion and short numerical-plausibility evidence, not a method
selection or equilibration test. The next discriminator is a matched 6--24 h
response experiment with independent flux/state constraints, including a
test of whether that isolated winter flux tail relaxes or persists.

The first matched discriminator, `hicarprep-land-response-6h-v1`, is now
complete for summer 2020-07-02. It used the same forcing, executable, model
settings, 30-minute diagnostics, and symmetric source-consistency gates for
SMI and relative saturation. Both arms passed all solver/conservation,
temperature, liquid/ice, snow, flux-tail, and REA-L source-consistency checks.
No `hfss` or `hfls` value exceeded `500 W m-2` after hour four; final
source-consistency RMSEs were about `1.64 K` for height-adjusted 2 m
temperature, `7.5e-4` for 2 m specific humidity, and `0.747 m s-1` for 10 m
wind speed in both arms. The methods remained materially distinct: at hour
six SMI minus relative saturation had soil-water RMSE `0.0590 m3 m-3`, mean
soil-column-water difference `42.24 kg m-2`, surface-temperature RMSE
`0.138 K`, and 2 m-temperature RMSE `0.0317 K`.

The frozen report is interpretable and records
`RELATIVE_SATURATION_NOT_VIABLE_IN_SUMMER_6H`, but that decision has a narrow
robustness envelope. Its only failures are total soil water below
`DRYSMC - 1e-5 m3 m-3` at hours 5/5.5/6. Counts grow from 2 to 6 to 14, all in
the top layer of loam cells initialized exactly at `DRYSMC=0.066`; the final
14 are `0.00316%` of active columns (`0.000789%` of all active soil-layer
states), in small southwestern patches near `46.003--46.142 N` and
`7.724--7.800 E`. The maximum deficit is `1.956e-5 m3 m-3`, only 1.96 times
the frozen tolerance, and no cell fails at a diagnostic tolerance of `2e-5`.
SMI also trends slightly below `DRYSMC`, reaching `-5.16e-6` at hour six, but
does not cross the frozen threshold.

Do not rewrite that predeclared decision after seeing the result. Also do not
interpret it as broad physical invalidity: the exact STAS table labels
`DRYSMC` a dry threshold, sets it equal to `WLTSMC` for all 19 categories, and
the pinned NoahMP source does not use `SoilMoistureDry` as a lower state clamp.
The result establishes that relative saturation puts 252 top-layer loam cells
at the dry/wilting threshold and 14 evolve marginally below it. Before a
winter escalation, define a new, source-supported distinction between a hard
admissible water bound and a dry/wilting response diagnostic, then re-read the
existing summer output under that independently justified contract. The
frozen and post-hoc reports are under
`/scratch/mch/olifu/icon_hicar/qualification/hicarprep-land-response-6h-v1/runs-v3/`.

The next direct-native absolute-`W_SO` candidate is implemented and statically
bounded for both origins. It uses support-normalized native-grid interpolation
of layer-integrated ICON water followed by the same exact vertical-overlap
remap, while holding temperature, snow, target land cover, and model settings
fixed. Relative to the legacy statics its mean active-soil column difference is
only `+0.401/+0.405 kg m-2` for 2/3 July, compared with the rejected SMI
candidate's `+58.48/+59.09 kg m-2`. The spatial column RMSE is still about
`30.6 kg m-2`, so static mean agreement is necessary but not decisive.

Preparation attempt `native-land-absolute-cold-start-july-v1` was stopped with
zero model attempts after it regenerated byte-different forcing and initially
used the stale runtime-release watcher. Its 38 ready records and logs remain as
recoverable incident evidence; both controller capacities are zero and its
watchers are canceled.

Clean checksum-bound campaign `native-land-absolute-cold-start-july-v2` was
stopped at user request before completion. Before controller execution it
verified and hardlinked all 97 exact hourly forcing payloads used by the
unchanged chronological reference and recorded by the completed SMI campaign.
The reuse report covers 2--6 July, and a dry reconciliation proposed six
forcing finalizers and zero forcing producers. Seven of 24 six-hour segments
completed; two interrupted segments remain retryable and 15 were not started.
Both controller capacities are zero, all watcher/model jobs are canceled, and
a final dry reconciliation proposed no actions. Published segments, exact
forcing evidence, and restart state remain available for a deliberate resume;
the incomplete run has no causal scientific result.

## Open scientific choices

| Choice | Current evidence | Smallest next discriminator |
| --- | --- | --- |
| Independent 72 h windows for wind | Supported in one summer bridge case | Repeat the same causal comparison in a stable-winter regime |
| Continuous versus reset coupled state | Reset soil and surface temperature are material. A matched summer SMI/relative test is numerically and source-consistent, but its frozen rejection of relative saturation rests on 14 marginal crossings of a `DRYSMC` gate that is not NoahMP's hard state floor | Define the admissible hydraulic bound independently of this result, re-assess the existing six-hour output, then use winter only if method uncertainty remains or ice/SWE adds a new discriminator |
| Warm-up length | 48 h is sufficient in repeated-summer and chronological-summer wind tests; not universal | Bracket 48 h only in a regime where the winter/strong-wind comparison fails or shows age dependence |
| 200 m versus 100 m | 200 m engineering foundation exists; 100 m has geometry evidence only | One matched representative 100/200 m skill-and-cost A/B after the wind method survives contrasting regimes |
| Surface/PBL physics | Warm/dry V29 evidence and terrain/land candidates exist, but no wind-relevant causal benefit is shown | Intervene only after a regime comparison identifies a specific wind/PBL deficit |
| Observational added value | Not established | Compare retained HICAR and REA-L with identical observation times, masks, and sampling operators |

## Ranked next goals

1. Close the hydraulic-bound interpretation exposed by the completed summer
   six-hour SMI/relative experiment: distinguish a non-negotiable physical
   state bound from NoahMP's dry/wilting response thresholds using source and
   table semantics, then re-assess the existing output without changing its
   frozen report. If both methods remain viable, run the same matched response
   in winter to exercise ice/SWE and the isolated startup-flux tail; retain
   absolute VWC only as a diagnostic control.
2. After selecting a viable cold start, or explicitly retaining uncertainty
   between candidates, repeat the independent-window
   comparison in one stable-winter or strong-wind regime. Bracket warm-up
   length only if errors show a useful model-age decay.
3. Test observational wind added value at matched sampling before scaling to
   100 m, national multi-year, or 20-year production.

Scientific decision rule:

- Do not choose a transfer because it resembles the legacy absolute-water
  trajectory. Reject a method only for physical invalidity or reproducible
  adverse coupled response against independent constraints.
- Compare SMI and relative saturation symmetrically; retain absolute water only
  as a sensitivity control, not the presumed scientific baseline.
- If neither independent cold start is viable, evaluate an offline ICON-driven
  land spin-up as a parallelizable initializer before accepting a serialized
  continuous HICAR trajectory.

Do not launch a national, month, annual, 20-year, or 100 m campaign merely
because this bridge experiment finishes.

## Deferred production work

Defer production promotion gates, immutable runtime releases, exhaustive
manifests, archive/restore contracts, generalized annual orchestration,
checksum closure, production validators, cleanup campaigns, and throughput
scaling. Preserve the validated implementations, but repair or generalize them
only when they repeatedly block high-value experiments, threaten scientific
interpretation, or become part of the selected strategy.

## Evidence locators

- Corrected-wind qualification:
  `/store_new/mch/msopr/olifu/icon_downscaling/qualification/wind-tendency-fix-b514/v1/manifest.json`
- Corrected repeated-day equilibration:
  `/store_new/mch/msopr/olifu/icon_downscaling/qualification/repeated-day-summer-windfix-b514/v2/manifest.json`
- Chronological summer comparison:
  `/store_new/mch/msopr/olifu/icon_downscaling/qualification/chronological-overlap-summer-202007/v3/manifest.json`
- Prior direct-SMI compatibility artifacts and completed campaign:
  `/scratch/mch/olifu/icon_hicar/qualification/native_land_init_v1` and
  `/scratch/mch/olifu/icon_hicar/qualification/native-land-cold-start-july/v1`
- Direct-absolute statics, comparisons, and active causal campaign:
  `/scratch/mch/olifu/icon_hicar/qualification/native_land_absolute_v1` and
  `/scratch/mch/olifu/icon_hicar/qualification/native-land-absolute-cold-start-july/v2`
- Strict atmospheric decoding, Storm Sabine balance certificates, certified
  sparse LBC sequence, and bracket-crossing runtime qualification:
  `case_studies/swiss_200m/validation/hicarprep_storm_sparse_lbc_20200210_v1.json`
  and `/scratch/mch/olifu/icon_hicar/qualification/remaining-milestone-8051f4c`
- Historical detail and superseded decisions: `memory/project-state.md`

The legacy ledger is evidence, not a work queue. Results produced before the
horizontal-wind fix may inform infrastructure debugging but must not decide
the wind strategy.
