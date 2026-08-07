# Fixed-parameter improvement goal for Swiss 200 m HICAR

## Goal and decision boundary

Produce a checksum-bound static domain whose terrain, land/water/ice mask, land-use class, and Noah-MP soil classes are demonstrably better than the current public-source domain. Promote a change only after (1) static-field validation and (2) paired HICAR response tests. Do not silently replace the national production domain or launch a national coupled run.

The first implemented candidate is deliberately narrow:

- aggregate 10 m ESA WorldCover categorically with the modal class before reclassification, instead of selecting one nearest pixel;
- use all six SoilGrids depth intervals and thickness-weight sand/silt/clay onto Noah-MP's 0–10, 10–30, 30–70, and 70–150 cm layers;
- write the four soil classes and their sand/silt/clay composition with explicit layer bounds and processing provenance;
- expose the already-present Noah-MP depth-varying-soil pathway through `soiltexture_var`, guarded by `nmp_opt_soil=2` and an explicit renderer switch.

The current production behavior remains the default (`nmp_opt_soil=1`).

## What Swiss and Alpine WRF/Noah applications did

The most directly relevant published Swiss WRF static package used 1 arc-second ASTER GDEM v003 terrain, CORINE 2006 land cover reclassified to USGS, and a spatially constant silty-clay-loam soil class. It applied one 1-2-1 terrain-smoothing cycle as a starting point, with setup-specific adjustment ([Gerber and Lehning, 2021 dataset description](https://infoscience.epfl.ch/server/api/core/bitstreams/10b4d4bb-eac9-4527-bd4d-0886b76a6266/content), [EnviDat record](https://doi.org/10.16904/envidat.233)). HICAR's published Alpine evaluation inherited that static package; HICAR upscaled the terrain without smoothing, while the companion WRF setup used repeated 1-2-1 smoothing and matched the driving topography near its boundary ([HICAR v1.1](https://gmd.copernicus.org/articles/16/5049/2023/)).

A Swiss 200 m WRF-COSMO1 study used Noah-MP and 1-2-1-smoothed terrain and reproduced local valley, katabatic, and glacier-flow behavior better than the 1 km driver in its case studies ([project report](https://infoscience.epfl.ch/record/301778/files/8168-2022.11.23_PROJECT_EPFL_final_report.pdf)). An Alpine glacier LES study nested WRF down to 48 m, used CORINE land cover, and explicitly corrected glacier outlines before running Noah-MP with multilayer snow. That explicit ice-mask correction is more important for our use case than copying its older land-cover source ([study manuscript](https://www.research-collection.ethz.ch/bitstreams/db68b01b-68d0-481d-82d9-5b845d7bdce6/download)).

Modern offline Noah-MP practice provides the more useful soil precedent: a recent HRLDAS study used SoilGrids' six depth intervals, mapped them to the four WRF soil layers, aggregated 100 m CORINE land cover to 250 m by majority class, tested both texture classes and continuous pedotransfer parameters, supplied time-varying LAI/vegetation fraction, and used a long climatological land spin-up ([HESS 2025](https://hess.copernicus.org/articles/29/2551/2025/)). Official WRF geographic data now also distribute SoilGrids products intended for Noah-MP and separate lake-depth, green-fraction, albedo, snow-albedo, and urban parameter datasets ([WRF geographic data](https://www2.mmm.ucar.edu/wrf/site/geog_data.html)).

## Assessment of our present sources

The present public domain is not uniformly poor. Its Copernicus GLO-30 terrain, WorldCover 2021, and spatial SoilGrids texture are newer and spatially richer than the historic Swiss WRF package. The weaknesses are in processing and process completeness:

1. **Terrain:** GLO-30 is a global digital surface model, whereas atmospheric terrain should ideally be bare earth. Switzerland's official `swissALTI3D` is a 0.5/2 m bare-earth DTM and is the preferred national reference ([swisstopo](https://www.swisstopo.admin.ch/en/height-model-swissalti3d)). It cannot simply be pasted into the international domain: datum/source offsets and the Swiss-border seam must first be measured and blended. The unfiltered HICAR terrain remains the reference until that test passes.
2. **Land cover:** nearest-neighbor downsampling from 10 m to 200 m is sensitive to grid registration. Modal aggregation is a clear correction. `swissTLM3D` is the best Swiss validation/correction source for forests, settlement, hydrography, and ground cover; its ill-defined features have stated 1–3 m geometric accuracy ([swisstopo](https://www.swisstopo.admin.ch/en/landscape-model-swisstlm3d)). Its detailed classes still require a documented USGS crosswalk.
3. **Glaciers:** WorldCover's permanent-snow/ice class is useful internationally, but Swiss glacier outlines should be checked and, where justified, overwritten with SGI2016/operational GLAMOS outlines. SGI2016 includes debris cover and is based on aerial imagery from 2013–2018 ([GLAMOS download](https://dev.glamos.ch/en/downloads)). The epoch must be chosen consistently with the 1991–2020 reanalysis period; a modern outline is not automatically correct for early years.
4. **Soil:** the former 0–5 cm-only class was not representative of Noah-MP's 1.5 m column. The implemented layer mapping fixes that structural error. SoilGrids remains uncertain in mountains and explains only part of observed spatial variation; its classes must be treated as an ensemble factor, not truth ([SoilGrids documentation](https://docs.isric.org/globaldata/soilgrids/index.html)). Switzerland's official 1:200,000 soil-suitability map is too coarse and old to replace 250 m SoilGrids directly, but is useful as an independent stratified check ([FOAG](https://www.blw.admin.ch/fr/carte-des-aptitudes-des-sols)).
5. **Lakes and radiation:** the static file contains a water mask but no lake depth, and the current campaign deliberately uses the simple water surface. It also contains no horizon or sky-view fields, so terrain shading correctly remains off. Both are separate model-physics changes and must not be bundled with land-cover/soil changes.

## Ranked next improvements

| Priority | Change | Confidence | Status / promotion condition |
|---|---|---:|---|
| P0 | Modal WorldCover aggregation | High | Implemented; validate category-area changes and narrow-feature behavior |
| P0 | Six-depth SoilGrids mapped to four Noah-MP layers | High for structure, medium for local truth | Implemented behind a switch; paired response test required |
| P1 | GLAMOS glacier-mask audit and epoch-aware correction | High inside Switzerland | Implement after quantifying disagreement by elevation and reanalysis decade |
| P1 | swissTLM3D audit of water, forest, urban, and bare classes | High as validation, medium as USGS replacement | Build a versioned crosswalk and confusion/area report first |
| P1 | swissALTI3D area-mean terrain sensitivity | High source quality, medium coupled benefit | Test source offsets, seam treatment, slopes, geometry, and winds on subdomains |
| P2 | Monthly LAI and vegetation fraction | High physical relevance | Treat as a land-forcing experiment, not a fixed-field-only change |
| P2 | Lake depth plus lake model | Medium | Separate lake-energy experiment; do not mix with soil/land-cover attribution |
| P2 | Horizon/SVF terrain radiation | Medium-high for valleys | Separate radiation experiment after deterministic horizon validation |

## Terrain-radiation qualification

Terrain radiation is now an independently controlled candidate, not part of
the static-parameter bundle.  The national renderer keeps it off by default
and exposes five explicit profiles: `off`, `direct`, `direct-diffuse`,
`full-local`, and `full-neighborhood`.  This ordering lets the experiment
attribute changes to horizon/slope projection, diffuse-sky obstruction, and
terrain exchange instead of comparing two inseparable physics packages.

The implementation preserves the unmodified horizontal-plane direct and
diffuse fluxes produced by RRTMG(P).  Terrain corrections are derived from
those fields independently and the raw fields are restart-persistent.  This
fixes the former causal defect in which an already-SVF-reduced diffuse field
was subtracted from total shortwave, incorrectly reclassifying obstructed
diffuse energy as direct beam.  The reflected-shortwave pathway already used
reflected irradiance (`albedo * incident shortwave`) in W m-2; it is retained
and covered by an analytic units test rather than "fixed" speculatively.

Geometry publication is deliberately strict.  HICAR currently requires 90
azimuth sectors at 4-degree spacing (`0, 4, ..., 356` degrees clockwise from
north), `hlm` as zenith angle to the horizon (flat/open = 90 degrees), SVF in
[0,1], and slope/aspect in radians.  A geometry artifact must record its DEM
checksum, vertical datum, generator/version, search distance, and convention.
The publisher validates these fields, adds them to a checksum-bound copy of
the base static file, writes a manifest, atomically renames, and only then
creates ready markers.  Horizon slabs are copied incrementally because a
Swiss 200 m, 90-sector float field is about 1.35 GB before compression (about
5.4 GB at 100 m).

Known model limitations remain material:

- diffuse shortwave is isotropic and represented only by scalar SVF;
- horizon directions are discrete and the current runtime selects one sector
  without azimuth interpolation;
- terrain-reflected SW and terrain-emitted LW use a simplified view-factor
  treatment, with a local-cell approximation at radius zero;
- the finite horizon search distance can miss distant Alpine obstruction;
- slopes/aspects, horizon, and SVF must be generated from the same audited
  terrain realization and vertical-datum convention;
- bilinear regridding can smooth horizons and does not preserve ray geometry.

### Bounded causal experiment

First run a flat synthetic domain and one compact valley tile, never the
national domain.  For the valley, use one cloud-free summer diurnal case, one
clear winter/snow case, and one weak-synoptic nocturnal drainage-flow case.
Hold forcing, initialization, executable, timestep controls, surface data,
and output cadence fixed.  Run `off`, `direct`, `direct-diffuse`, and
`full-local`; run `full-neighborhood` only after the local case passes energy
and cost checks.

Required diagnostics are horizontal direct/diffuse SW, total downward SW,
terrain-reflected SW, downward/upward LW, surface temperature, sensible,
latent and ground heat flux, PBL height, friction velocity, and 10 m plus
fixed-height wind vectors.  Score shadow onset/release by slope and horizon
azimuth, daytime and nocturnal surface-energy residuals, cold-pool timing and
strength, katabatic onset/reversal, wind speed/direction/shear, and comparison
to available radiation and wind observations.

Promotion gates are:

1. flat/open geometry reproduces `off` within numerical tolerance;
2. an obstructed synthetic horizon removes direct SW without increasing the
   diffuse or raw direct component;
3. each cumulative profile increment has the expected sign and closes the
   surface-energy budget without NaNs or range violations;
4. a split restart agrees with the uninterrupted trajectory at every saved
   field to the project's restart tolerance;
5. shadow timing agrees with geometric expectation within one 4-degree sector
   and one radiation-update interval;
6. the effect is consistent across at least two physical cases and improves
   observation-facing metrics without degrading wind physicality; and
7. measured runtime and storage overhead are acceptable before any national
   qualification is authorized.

## Bounded experimental design

### Phase 0: static-only audit

Generate the old and new static products on identical grids and report:

- SHA-256, source versions, processing options, and category crosswalk;
- area fraction and cell-transition matrix for every USGS class;
- connected-component changes for water, urban, ice, forest, and bare terrain;
- soil-class transition matrices by Noah-MP layer, plus sand/silt/clay closure and invalid-cell counts;
- elevation, slope, curvature, boundary mismatch, and HICAR coordinate-Jacobian diagnostics (unchanged for the P0 candidate).

Reject the candidate for unexplained class changes, sand+silt+clay outside 95–105%, missing soil coverage, invalid categories, or a non-atomic publication.

The implemented static audit passed these mechanical gates. B and C share
candidate SHA-256
`7b470441eecbcbc45818a8479d87d2ab33603a24d7bd1baf6db76cfad8f89144`;
their distinction is configuration-only. Relative to A, 378,506 land-use
cells (10.10%) and 112,466 surface-soil cells (3.00%) change, terrain is
bitwise unchanged, all active-land soil classes are valid, and texture
closure is 99.90-100.10%. Modal aggregation reduces the connected-component
count for every audited feature group, consistent with removing nearest-pixel
speckle, but the 10.10% categorical change still requires Phase 1 response
testing and Phase 2 Swiss reference-data checks. The decisive report is
durably published under
`/store_new/mch/msopr/olifu/icon_downscaling/qualification/static_abc_v1`.

### Phase 1: paired process cases

The former 26 h design with a fixed two-hour discard is rejected. Two hours
can remove numerical startup noise but cannot establish that Noah-MP surface
and soil states are insensitive to the arbitrary segment start. Equal numeric
soil water is also not an equal hydraulic state when the soil class changes.

Use the same checksum-bound forcing cache and the same 24 h scoring window for
one clear-sky summer valley case, one winter snow/ice case, one strong
synoptic-flow case, and one weak-wind transition case. Before scoring, evolve
each arm from its own published REA-L land state over an adaptive lead ladder
of 24, 48, 72, 120, and 168 h. Each lead and its common 24 h score window must
run in one uninterrupted HICAR process. The synthetic restart qualification
exposed a first-step land/surface transient, so this experiment must not
stitch a preconditioning run to a score run until restart continuity passes
independently. Run:

- A: current dominant 0–5 cm soil / current published static;
- B: modal WorldCover but dominant top-layer soil (`nmp_opt_soil=1`);
- C: same static as B with four depth-varying soil classes (`nmp_opt_soil=2`).

This isolates categorical aggregation from soil-depth physics while allowing
each surface parameterization to adjust consistently. Keep terrain,
atmospheric physics, forcing, scoring timestamps, output cadence, and
executable checksum identical. Land states are deliberately arm-specific;
their provenance and restart checksums are part of the experiment.

Select the shortest lead of at least 48 h whose score trajectory agrees with
the next longer lead for every predeclared state, surface-flux, and wind
threshold; confirm the next increment when available. If 120 versus 168 h
still fails, classify that event/arm as initialization-sensitive and do not
interpret its A/B/C difference. At most seven days is not a deep-soil climate
equilibration claim: this is an event-response design conditional on REA-L
initialization. Climatological production still requires continuous land-state
carry-over or a separately qualified offline Noah-MP spin-up.

Score both physicality and wind relevance: surface energy closure; skin and 2 m temperature; sensible/latent/ground heat flux; soil temperature/moisture by layer; snow; PBL height; friction velocity; 10 m and fixed-height wind speed/direction; valley-flow onset, reversal, and vertical shear. Compare against available station/profile observations and against ICON REA-L-CH1, but do not require HICAR to remain close to the driver where resolved slope flows are physically supported.

The machine-readable plan, exact seasonal cases, tile definitions, adaptive
ordering, thresholds, A/B/C configuration, and 60 uninterrupted run records are
generated by `create_static_process_case_plan.py`. Publishing that plan does
not authorize the process runs or any national experiment.

### Phase 2: national-source factors

On two small Swiss tiles (glaciated high Alps and populated valley), add one factor at a time: GLAMOS ice, swissTLM3D land/water, swissALTI3D terrain, monthly vegetation, terrain shading. Use a factorial screening design only after each field passes static validation. Promote no factor from a single event; require consistent sign/benefit across at least two contrasting events and no geometry or energy-budget regression.

## Reproducibility contract

Every candidate static file must be written to a temporary path, validated, atomically renamed, and only then receive its `.ready` marker. Its manifest must bind source collection/version or URL, cached source identity, target grid, resampling/aggregation method, class crosswalk, soil-depth mapping, boundary treatment, generating commit, and output checksum. The production manifest must also state whether `nmp_opt_soil=1` or `2` was used; the same NetCDF can support both, but the simulations are not physically equivalent.
