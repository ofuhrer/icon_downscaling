# Swiss 200 m scientific reference setup

This is the untuned reference for four-season wind downscaling. It is a
scientific specification, not a claim that HICAR has demonstrated added value
over REA-L-CH1. The complete setup has now been evaluated on identical
SwissMetNet/native-REA-L station-time pairs with bundled Sx/TPI both on and
off. Neither fixed setting meets the preregistered added-value gate; the
Sx/TPI-off form below is retained as the bounded research reference, while
native REA-L/interpolation-only remains the operational baseline.

The closest published national precedent is HICAR v1.1 over the Swiss Alps
([Reynolds et al., 2023](https://doi.org/10.5194/gmd-16-5049-2023)): a roughly
280 x 170 km, 250 m simulation for January 2017, evaluated primarily through
monthly precipitation patterns and qualitative flow examples. It is not an
all-season national wind validation. Later work ran a 1 km parent from
October 2021 to May 2022 but the nested 50 m atmospheric experiment covered
only 25 April--10 May over a much smaller domain
([Reynolds et al., 2024](https://doi.org/10.3389/feart.2024.1388416)). HICAR
also supplied 50--250 m forcing for a catchment-scale 2016/2017 snow-season
study
([Berg et al., 2024](https://doi.org/10.3389/feart.2024.1393260)). These
studies constrain process choices, but none establishes a national,
all-season 100--200 m wind configuration or a published multi-year wind
climatology. A targeted search of the primary HICAR literature through August
2026 found no published five-year or multi-year atmospheric HICAR integration.
The longest explicitly documented continuous integration found was the 1 km
October 2021--May 2022 parent run; the longer 2016/2017 snow study establishes
season-spanning use of HICAR-derived forcing, but does not state that the
atmospheric fields came from one continuous HICAR trajectory.

### Published evidence boundary

| Study | Atmospheric scope | Relevant precedent | What it does not establish |
| --- | --- | --- | --- |
| Reynolds et al. (2023), HICAR v1.1 | January 2017, 250 m, roughly 280 x 170 km; COSMO1 forcing | National Alpine scale; SLEVE and variational wind; TPI/Sx; Noah, Morrison, YSU and RRTMG | Quantitative national station-wind skill, other seasons, Noah-MP or this fork's wind operator |
| Reynolds et al. (2024) | 1 km parent from 1 October 2021--10 May 2022; nested 50 m run from 25 April--10 May over the Davos region | Noah-MP, revised-MM5 surface exchange, YSU, RRTMG, terrain direct/diffuse radiation and high-resolution wind/temperature observations | Seasonal 50 m atmospheric integration or national-domain behavior |
| Berg et al. (2024) | HICAR-derived 50, 100 and 250 m forcing supplied to snow simulations for 1 October 2016--31 July 2017 over a 14 x 12.5 km Davos domain; continuity of the atmospheric HICAR integration is not specified | Season-spanning downstream use and snow-pattern value | One continuous atmospheric integration, direct atmospheric wind verification or national transferability |
| This reference | Four independent 48 h, 200 m national events driven by REA-L-CH1; second 24 h scored at SwissMetNet sites | Controlled national wind added-value test across four seasons | A climatology, multi-year stability or optimal parameter tuning |

The selected setup therefore preserves published components only where they
remain compatible with direct evidence from this domain. Differences are
deliberate and visible: REA-L rather than COSMO forcing, 80 vertical levels,
the current adjoint projection with fixed alpha 1, Noah-MP option 1, regular
full-domain relaxation, and bundled Sx/TPI disabled as the bounded research
reference after the four-event comparison.

## Domain and terrain

- 200 m azimuthal-equidistant grid, 2061 x 1431 cells (412 x 286 km), centred
  at 46.815 N, 8.225 E. The 40 km external buffer includes every station in
  the broad Switzerland-covering SwissMetNet query used by this project.
- Copernicus GLO-30 elevation is area-aggregated to 200 m. The physical
  surface is not smoothed: published HICAR upscaled a roughly 30 m DEM without
  smoothing. The SLEVE terrain split changes coordinate deformation, not the
  surface elevation.
- A 30 km cosine blend makes the high-resolution terrain equal REA-L-CH1
  `HSURF` at the outer edge and fully high-resolution inward. This is the
  topographic sponge. It is intentionally broader than the 10 km atmospheric
  boundary shoulder.
- Land inputs are WorldCover-to-USGS land use, land/water mask and four
  depth-dependent SoilGrids texture classes. Invariant mask/soil fields live in
  the static domain; the 2021-epoch dominant land use and category fractions
  remain in the matching lifetime-partitioned external product and are
  materialized at each initialization time. Back-extrapolating that 2021 map to
  the 2020 cases, glaciers and unresolved sub-grid lakes remain
  representativeness uncertainties.

## Vertical grid

- 80 SLEVE levels, `auto_level=1`, 15 km ASL top, nominal 20 m first layer and
  stretch factor 0.65. The first six flat-terrain layers are approximately
  20, 26, 31.9, 37.8, 43.6 and 49.3 m.
- Every generated national grid must retain positive Jacobians and at least
  12 m interface spacing everywhere; values below 10 m are never accepted.
  On the selected 2061 x 1431 terrain, the 15 km construction achieves a
  17.008 m minimum interface spacing and a 0.3375 minimum SLEVE Jacobian.
- SLEVE parameters are window radius 5, 10 smoothing cycles, large/small
  decay 2/6 and exponent 1.35. Map factors are applied. Forcing interpolation
  uses AGL height below an 800 m transition and ASL above it.
- The 15 km top follows the process-based ICAR recommendation to encompass the
  full troposphere; a real-mountain case stabilized near 15.2 km
  ([Horak et al., 2021](https://doi.org/10.5194/gmd-14-1657-2021)). The old
  12 km top is not retained for an all-season Alpine reference.

## Meteorological preprocessing

- Decode hourly native REA-L-CH1 pressure, temperature, water vapour, cloud
  water, cloud ice when available, horizontal and vertical wind, interface
  height, surface height and skin temperature. Units, grid identity and valid
  time are checked before transformation.
- Horizontally remap with the existing bounded/conditioned local RBF weights.
  Reconstruct positive source-layer thicknesses before vertical interpolation;
  use target HHL/HFL from the exact HICAR SLEVE geometry.
- Supply pressure, temperature and dry-air mixing ratios. Preserve resolved
  supersaturation (`limit_rh=false`). QI is explicitly zero only when absent
  in REA-L; QR/QS/QG are not invented, so precipitation and cold-cloud spin-up
  remain limitations.
- Regular full-domain forcing contains P, T, QV, QC, QI, earth-relative U/V,
  vertical velocity W interpolated to target HFL, and hourly water-surface
  temperature SST. The terrain-following W correction rotates U/V into the
  HICAR grid basis before taking the slope dot product; serialized U/V remain
  earth-relative. HICAR rotates U/V to its staggered grid before applying the
  diagnostic wind solver. Available REA-L W is the published-2023-like initial
  guess; it is never inserted as a sparse boundary value.
- Use HICAR's established full-domain forcing relaxation (`relax_filters=true`)
  and do not configure a sparse-LBC list in the untuned reference. This is the
  boundary path used by published long HICAR integrations and it relaxes the
  same regular P/T/QV/QC/QI/U/V/W state used for initialization and hourly
  turnover. The newer sparse target-grid path remains available for controlled
  experiments, but its 10 km shoulder, 3600 s time scale and scalar-only state
  are not literature-qualified national defaults.
- Prescribed SST is used only on water cells. The former blank `sst_var`
  silently held every lake at 280 K and is not scientifically acceptable.
  HICAR's simple-water lower bound of 273.15 K means frozen-lake physics is
  still not represented.

## Dynamics and diagnostic wind

- RK3, CFL reduction 1.6, third-order horizontal and vertical advection,
  FCT option 1, density-weighted transport and no extra constant-z diffusion.
  These match established HICAR real-data examples; no cumulus scheme is used
  at 200 m.
- Use the fork's adjoint variational mass-conserving projection. It has exact
  algebraic/restart tests but is not the same published operator and must be
  identified as such in interpretation.
- Use fixed `alpha_const=1` as the conservative bounded R&D reference. In the
  exact winter initial state, this fork's adjoint projection coupled to the
  diagnosed dynamic alpha produced localized high-terrain 10 m winds up to
  139.30 m s-1 (2,101 cells above 30 m s-1 and 476 above 50 m s-1), despite
  bounded forcing and a converged conservative solve. Holding everything else
  fixed, alpha 1 reduced the maximum to 21.085 m s-1 and left no cell above
  30 m s-1; the 50 m maximum fell from 34.60 to 21.262 m s-1. This rejects that
  fork/domain coupling for the selected campaign. Fixed alpha is the stable
  end member, not the published dynamic HICAR reference or a claimed optimum.
- Disable the bundled Sx/TPI wind modification in the selected reference.
  Published HICAR v1.1 used a 4 km TPI exposure term and stability-dependent
  3-D Sx sheltering, while explicitly describing its correction values as a
  sparse, partly empirical parameter sample. In exact +24 h sensitivities,
  disabling this project's former 600 m/30 degree Sx, 4 km/200 m TPI and
  500 m smoothing materially improves full-event speed RMSE in DJF/MAM/JJA,
  but worsens SON vector RMSE by 0.454 m s-1 and removes the Sx-on autumn
  advantage. The Sx-off campaign is materially neutral against REA-L in all
  four events. This is an evidence-based bounded research choice, not a claim
  that terrain exposure is unimportant or that the change generalizes to
  production. Use two wind-adjustment passes, a
  2500-iteration solver cap and one update per hourly input interval. Thermal
  and linear-theory corrections remain off: the former was too strong in the
  published small-domain evaluation, and the latter would duplicate
  mountain-wave structure already present in REA-L.
- Set the segment validator's physical-core 10 m/50 m wind bound to 30 m s-1
  for these four bounded events. The dynamic-alpha initialization violated this
  gate by a wide margin, whereas every qualified alpha-one/Sx-off result so far
  remains below it. The threshold is explicit experiment acceptance, not a
  claim that all physically possible Alpine winds are below 30 m s-1.

## Physical parameterizations

- Morrison two-moment microphysics, YSU PBL, Noah-MP land, revised-MM5
  atmospheric surface layer, prescribed simple-water SST, RRTMG radiation
  and Noah-MP's internal snow model. Microphysics and YSU run every timestep
  on all levels; YSU top-down radiative mixing remains explicitly off.
- Noah-MP uses USGS classes, four soil layers and a 300 s update. The untuned
  option vector is `dveg=3`, `crs=1`, `btr=2`, `runsrf=1`, `runsub=1`,
  `infdv=1`, `tksno=1`, `scf=1`, `compact=1`, `frz=1`, `inf=1`, `rad=3`,
  `alb=2`, `wet=0`, `snf=4`, `tbot=2`, `stc=1`, `gla=1`, `rsf=1`,
  `soil=2`, `pedo=0`, `crop=0`, `irr=0`, `irrm=0`, `tdrn=0`, with the soil
  process timestep equal to the Noah-MP call and standard output. USGS class
  24 activates glacier treatment.
- `dveg=3` uses the class/date-interpolated Noah-MP LAI table and diagnoses
  FVEG. `alb=2` diagnoses soil/snow albedo. External LAI, VEGFRA and static
  ALBEDO are therefore not campaign requirements under this reference.
- Retain HICAR's revised-MM5 atmospheric surface-layer scheme and use the
  pinned modular Noah-MP's supported surface-exchange resistance option 1.
  The restored option-3 implementation is useful as an explicit sensitivity,
  but it is not required by the published national reference and is removed
  from the campaign baseline.

## Radiation

- Use RRTMG at 600 s, UTC, cloud-fraction option 3 and maximum-random overlap.
  This is the frozen physics used by the completed national campaign; changing
  it would define a new trajectory. Independent 12-node RRTMG daylight
  replicas were exact for all seven output states and all terminal-restart
  variables. The one-hour jobs, including 34 GB restart publication, took less
  than 19 minutes. RRTMG is a physics substitution, not a numerically
  equivalent implementation of RRTMGP. The corrected-SST two-hour campaign
  test established numerical rather than bitwise restart reproducibility; its
  small, decaying land/PBL perturbation is accepted for the completed
  wind-focused R&D campaign and remains visible in segment diagnostics.
- Subsequent performance work qualified GPU RTE-RRTMGP v1.9.3 on the same
  full-Swiss 12-node topology. That path requires HICAR commits `c4e0aa81`
  (reset Noah-MP's hidden energy workspace) and `cd94b79b` (select RRTMGP's
  existing sequential tropopause `minmaxloc` helper for NVHPC OpenACC), in
  addition to the v1.9.3 API/build changes. The latter avoids the NVHPC 24.5
  device `MINLOC`/`MAXLOC` scalar-live-out code generation that caused
  topology-sensitive 32-column radiation differences.
  Its two-hour continuous and restarted trajectories are bitwise identical at
  every retained output and across all 199 join and terminal restart variables.
  This qualification is an acceleration option for new experiments; it does
  not retroactively change the completed RRTMG campaign.
- Enable terrain shading plus direct and diffuse shortwave corrections after
  HLM, SVF, slope and aspect have been generated and checked. Keep reflected
  shortwave and terrain longwave off in this first RRTMG baseline so their
  less-established neighbourhood terms do not confound the radiation change.

## Runs and verification

- Each season is one 48 h continuous physical trajectory split into 12 h
  restart segments. Run these on `normal`, at most one 12-node segment at a
  time, with the controller's 50% partition-capacity guard. The first 24 h are
  spin-up; the following 24 h reproduce the selected winter, spring, summer
  and autumn evaluation events. A final daylight turnover and
  continuous-versus-restart check is required after the complete setup
  changes, not for tuning it.
- The pinned campaign executable predates serialization of
  `domain.height_lowest_level`. Set the manifest-recorded
  `allow_missing_restart_domain_provenance=true` compatibility option: it
  accepts only that absent 20 m attribute, while wrong or non-finite values and
  every other mismatch remain fatal. The rendered namelist and validated
  static geometry independently retain the exact 20 m configuration. Future
  builds use HICAR `5bee3c92`, which serializes `auto_level`, lowest level,
  model top and stretch factor directly and therefore does not need this
  compatibility exception; the completed campaign remains immutable at its
  original `4a425677` executable.
- Each segment lists only the hourly regular-forcing records bracketing that
  segment. Ready records for the next segment can therefore be generated while
  the current segment runs; the shared endpoint is reused. A daylight 2 x 1 h
  local-list segmented trajectory must match the full-list segmented reference
  exactly before this streaming mode is used for the campaign. This was shown
  in the corrected-SST smoke; it is distinct from uninterrupted-versus-restart
  equivalence, which is assessed numerically.
- Generate each hourly record with eight deterministic column workers on an
  exclusive CPU node. This is a throughput choice, not a physics change: the
  selected real regular record is bit-exact with serial preparation and reduces
  wall time from about 80 to 16 min. Keep streaming at segment granularity to
  preserve the bounded, restartable campaign workflow.
- Write the essential two-dimensional station-comparison and surface-process
  fields every 600 s. For each ending civil hour, aggregate the six HICAR
  ten-minute samples to the SwissMetNet `h0` definitions; inverse-rotate HICAR
  wind components before vector verification. Approximate the corresponding
  REA-L interval mean by the trapezoidal mean of its consecutive hourly
  endpoints, and retain endpoint semantics for snow and interval differences
  for precipitation. An hourly instantaneous model value is not compared with
  an hourly observation aggregate.
- Use every usable SwissMetNet site and identical station/time pairs for HICAR
  and native REA-L-CH1. RMSE remains the headline. Bias, MAE, variability ratio
  and correlation diagnose why scalar RMSE changes; wind additionally uses
  speed bias/MAE and vector RMSE. Stratify only by season, elapsed time and
  elevation/terrain class, with station-level inspection for exposed ridges.
  Four events support diagnosis, not a climatological ranking.
- SwissMetNet automatic stations are terrestrial sites. Sample HICAR at the
  nearest land cell rather than an unconstrained centre that may fall in a
  200 m river or lake polygon. Report every surface override and displacement
  and reject shifts above 1 km. This corrects land/water mismatch but does not
  make an exposed ridge station representative of its surrounding model cell.
