# HICAR Swiss wind-climatology product contract

## Goal

Produce a physically documented, storage-bounded, HICAR-native wind
climatology for Switzerland at 200 m horizontal resolution that supports wind
resource screening, turbine-energy assessment, exposure and operational
applications. ICON `VMAX` is not part of the production product.

The first acceptance milestone is a reference extractor whose results can be
used as the scientific oracle for matching diagnostics implemented inside
HICAR. The production path must calculate fixed-height fields and temporal
statistics online so that the national 20-year run does not archive the full
three-dimensional atmosphere.

## User-facing priority

| Priority | User need | Supplied product |
| --- | --- | --- |
| 1 | Resource screening | Mean scalar/vector wind and variability at 10/50/75/100/125/150/200 m; density-adjusted mean wind power density at 50/75/100/125/150/200 m |
| 1 | Energy yield | Selected-height chronology for an exact user-supplied turbine curve; AEP, capacity factor and full-load hours remain derived products |
| 1 | Wind regime | Monthly/seasonal/diurnal aggregates, 12-sector wind roses, calm counts and speed-threshold exceedance hours |
| 2 | Hub-height transfer | Density, power-law shear and directional veer between 50--100, 100--150 and 100--200 m |
| 2 | Siting context | Roughness, friction velocity, bulk Richardson number, PBL height and terrain-complexity layers |
| 2 | Extremes | Resolved sample maxima, block maxima and event/POT inputs with exact sampling metadata |
| Gated | Gusts and turbulence | No production gust, turbulence intensity or IEC class until a distinct diagnostic passes observation validation |

Mean wind power density is a resource measure; it is not turbine electrical
power. AEP and capacity factor always require a stated turbine curve,
availability, and loss assumptions.

## Product layers

### Core diagnostic stream

HICAR supplies source-complete values on the HICAR mass grid at the reduction
interval:

- HICAR surface-layer `u10m` and `v10m` at 10 m AGL;
- eastward wind and northward wind at 50, 75, 100, 125, 150 and 200 m
  AGL;
- air density at the same 50--200 m heights;
- friction velocity, surface roughness, surface Richardson number and
  boundary-layer height.

HICAR's variable named `mol` is a surface-layer temperature scale in kelvin,
not Monin--Obukhov length, and is therefore excluded. The surface Richardson
number is retained as the dimensionless stability diagnostic. It is the raw
MM5-revised bulk Richardson number: the similarity-function inversion clips
its working value at 250, but the diagnostic itself can exceed 250 in calm,
stable cells. Validation therefore checks finite values and a broad sanity
range rather than incorrectly imposing the internal inversion cap.

Wind speed and wind-from direction are lossless deterministic transformations
at all seven heights. Density-adjusted wind power density
`0.5 * rho * wind_speed^3` is supplied only at 50--200 m, where HICAR emits
collocated density. The 10 m surface-layer wind is a reference wind level; the
pipeline does not invent a 10 m density or silently substitute density from a
different height. The delivery pipeline supplies these quantities as
user-ready variables or calculates them on demand; HICAR does not archive
duplicate copies.

The diagnostic stream is not automatically the national archive. It feeds two
storage tiers:

- the default Swiss climatology tier keeps counts, vector and scalar moments,
  density-weighted cubic moments, marginal wind-direction sectors, speed
  threshold counts, calendar and diurnal aggregates, and resolved block
  maxima;
- an optional assessment tier keeps hourly `u`, `v` and `rho` for selected
  heights, periods or regions where chronological reconstruction, turbine
  simulation or event analysis is required.

This split avoids multiplying the multi-decade archive by storing speed,
direction, power density and other exactly derivable copies. Every aggregate
records its sample count, reduction interval and missing-data coverage.

The delivery rule is:

- national monthly/seasonal/diurnal maps supply mean vector and scalar wind,
  variability, density, wind-power density, shear/veer, coverage and resolved
  maxima;
- national marginal direction sectors and speed-threshold counts supply wind
  roses and exceedance hours; selected-height or selected-region chronology
  supplies percentiles, persistence, turbine-curve AEP, capacity factor and
  full-load hours;
- observation-calibrated add-ons may later supply short-duration gust and
  turbulence products, but the first release must not infer them from the
  resolved maximum or from mean/standard-deviation fields.

For the 2,271-by-1,651 Swiss grid, the 24 source-complete float fields
(10 m `u/v`, `u/v/rho` at six heights, and four surface/PBL diagnostics) are
about 343 MiB per hourly record and 57.4 TiB for the 2005--2024 hours before
coordinates, metadata or compression. The current 52-field-equivalent
reference-statistic set would be about 5.18 TiB if retained daily everywhere,
but only about 0.170 TiB at monthly
resolution. These are arithmetic planning values, not measured compression
results. The implemented 12-sector direction counts at seven heights, six
speed-threshold counts at seven heights, and calm counts add 133
integer-field equivalents; the complete 185-field-equivalent monthly package
is about 0.606 TiB over 20 years before compression. The default national
archive should therefore retain
monthly/seasonal/diurnal sufficient statistics plus compact extreme/event
products; full hourly chronology is an opt-in regional or height-selected
tier. Both the live 701-by-701 bridge and the two-segment Swiss national
engineering gate now pass. Representative-duration observational skill,
uninterrupted-versus-segmented trajectory agreement, and the durable archive
destination remain acceptance gates.

The committed-source bridge used eight GPU compute ranks and two CPU I/O ranks
across two Balfrin nodes and completed in 2 minutes 42 seconds. Its
qualification-only file includes the source-complete wind profile,
surface/PBL diagnostics, and full-level `u`, `v`, `z`, and `density`; it was
1,207,455,751 bytes. Reducing it to one composable 30-minute interval,
including all marginal direction and threshold counts, produced 81,437,906
bytes, a 14.83-to-1 qualification-raw reduction. The surface/PBL ranges were
finite and plausible for the bridge: `ustar=0.00070--0.830 m s-1`, roughness
`0.001--1.0 m`, surface Richardson number `-12.54--344.57`, and
boundary-layer height `6.59--1438.73 m`. These measurements and hashes are
recorded in `validation/bridge_wind_surface_pbl_20100101.json`; the one-sample
compact interval verifies the pipeline and schema, not a climatological
distribution.

The Swiss 2,271-by-1,651 gate used 16 GPU compute ranks and four CPU I/O ranks
on four nodes. Its cold-start hour completed in 9 minutes 29 seconds
(`417.54 s` inside HICAR) and wrote three 30-minute records in
`1,111,223,047` bytes. The restart continuation completed its model and
output/restart validation in 10 minutes 5 seconds (`486.96 s` inside HICAR)
and wrote two new records in `750,846,487` bytes. Each 136-variable restart
was `42,368,218,828` bytes and is a rolling checkpoint, not part of the
climatology archive.

The two compact hourly intervals were `676,687,075` and `677,151,786` bytes;
their exact two-hour merge was `682,660,104` bytes and retained four source
samples, all surface/PBL statistics, direction sectors, calm counts, and
threshold counts. The nearly fixed cost per aggregate record confirms that
daily intermediate products should be composed and retired rather than
archived indefinitely: 240 similarly compressed monthly records would be
about 164 GB before seasonal/diurnal supplements. These two hours measure
engineering throughput and storage shape, not climatological representativeness.
The hash-checked retirement gate reached `READY_TO_RETIRE` without deleting
the `1,353,838,861` bytes of contributing intervals. Full evidence is in
`validation/national_wind_stream_20100101.json`.

The first bounded reference reducer emits, for each interval, mean `u`, mean
`v`, mean and population standard deviation of speed, mean density, mean
density-adjusted power density and the maximum sampled resolved speed at each
fixed height. It also emits the corresponding 10 m vector mean, scalar mean
and resolved maximum; means of friction velocity, roughness, bulk Richardson
number and boundary-layer height; and maxima of friction velocity and
boundary-layer height. Intervals use `(start, end]` samples, carry explicit
`time_bounds`, `sample_count`, and the source sampling interval, and are
processed in bounded horizontal blocks. Sub-second floating offsets in HICAR
timestamps are accepted only against an explicit chunk boundary and are
validated within one second of the inferred cadence before the exact regular
axis is constructed. Both the NetCDF payload and its hash-bearing JSON report
receive separate ready markers only after successful validation.

The same bounded pass emits non-calm meteorological wind-from counts in
12 sectors centred every 30 degrees, calm counts below `0.5 m s-1`, and counts
meeting or exceeding `3`, `5`, `10`, `15`, `20`, and `25 m s-1`. These are
available at 10, 50, 75, 100, 125, 150, and 200 m AGL. Direction plus calm
counts must equal `sample_count`; exceedance counts must be nested. Counts are
exactly additive across intervals and provide national wind roses and
exceedance hours without retaining chronology.

The compact products are exactly composable: counts and means are
sample-weighted, population variance is merged through its second moment, and
resolved maxima are merged by `max`. The bounded merger can form calendar
months (or a requested full period) without reopening raw HICAR files and
publishes its own hashes plus separate payload/report ready markers. Marginal
directional frequency tables and threshold counts are now implemented in the
exact merger.
Persistence state,
fine speed histograms, and joint speed-direction tables remain assessment-tier
candidates: their national storage and composition cost must be measured
before they become mandatory.

Both tiers are bound by SHA-256 to one published HICAR static-domain companion.
That companion supplies latitude/longitude, projected x/y coordinates and CRS,
terrain elevation, land/water mask and land-cover class; the compact wind
statistics supply qualified surface roughness. Slope, aspect and
terrain-complexity indicators are reproducibly derived from the companion's
projected coordinates and terrain, with the chosen neighbourhood and metric
recorded for every complexity product. The reducer verifies that the static
latitude/longitude grid exactly matches the wind grid, records the companion
path, size, hash, shape, spacing and projection in both NetCDF and JSON, and
the merger rejects products bound to different companions. Validation station
coverage, bias diagnostics and representativeness flags accompany the wind
fields; a 200 m grid value is not presented as a site measurement.

Fixed-height winds above 10 m are arithmetic mass-grid de-staggerings followed
by linear interpolation in geometric height above the matching static-domain
topography. Extrapolation is forbidden. The 10 m surface-layer diagnostic is
kept separate because it includes the land-surface similarity treatment and is
not merely a geometric interpolation of model levels.

### Derived user products

These are generated reproducibly from the diagnostic stream, compact
sufficient statistics or the optional hourly tier rather than stored as
independent model state:

- mean wind speed and vector wind, standard deviation and coefficient of
  variation;
- percentiles, exceedance hours and calm/strong-wind persistence;
- wind roses, directional frequency and directional energy;
- diurnal, monthly, seasonal and interannual climatologies;
- vertical shear exponent and directional veer between selected heights;
- mean wind power density;
- turbine power, annual energy production, capacity factor and full-load hours
  for user-supplied power and thrust curves;
- cut-in, rated and cut-out event statistics;
- peaks-over-threshold and block-maxima inputs for extreme-value analysis,
  including sample counts and uncertainty.

`scripts/derive_hicar_wind_user_products.py` implements the first compact
delivery supplement. From any published interval or merged product it provides
wind-from direction of the vector mean, speed coefficient of variation,
power-law shear of scalar mean speed for 50--100, 100--150 and 100--200 m,
and clockwise directional veer of vector means for the same pairs. Those names
deliberately distinguish direction/veer of a vector mean from a scalar circular
mean over samples. Calm or undefined ratios are masked rather than invented.

Wind power density is a resource metric, not turbine electrical production.
Turbine output must use the requested turbine curve, hub height, availability
and loss assumptions. Exact AEP, capacity-factor, persistence, percentile and
joint wind-rose products require the optional chronology or a subsequently
qualified speed-direction accumulator; they cannot be reconstructed from
means and standard deviations alone.

### Gust and turbulence policy

ICON `VMAX` is neither copied nor treated as HICAR truth. The first production
release will provide resolved interval maxima of HICAR wind speed with explicit
interval bounds and cell methods. A short-duration gust estimate may be added
only after a HICAR-native surface-layer or turbulence-based diagnostic has been
implemented and validated against Swiss observations. It must have a distinct
name from the resolved maximum and document its assumed gust duration.

A future gust candidate must pass all of these gates:

- diagnose gustiness from HICAR state rather than ICON `VMAX`, and publish it
  separately with CF standard name `wind_speed_of_gust`;
- state the averaging duration and aggregation interval explicitly; the WMO
  observing definition is the maximum 3-second running-average wind speed
  during the observing cycle;
- compare like-for-like interval maxima with quality-controlled Swiss gust
  observations and, for hub-height use, independent mast or lidar data across
  seasons, elevations, ridge/valley exposure and boundary distance;
- report bias, RMSE, upper-quantile error, threshold reliability and event
  ranking on a held-out validation period before any calibration is frozen.

The existing SwissMetNet event feed retrieves hourly mean 10 m wind
(`fkl010h0` and `dkl010h0`) and is already suitable for the mean-wind gate; it
does not currently retrieve gust. MeteoSwiss's operational catalogue
identifies `fkl010h1` as the hourly maximum of one-second station wind. A
future gust-validation feed must retrieve that quantity separately, retain
station measurement/exposure metadata, and state the one-second support.
Because it is not the same temporal support as a WMO-style three-second gust,
the comparison must either produce a like-for-like HICAR one-second
diagnostic or apply and validate an explicit duration harmonization. It must
never compare the two silently.

Hourly or 30-minute HICAR sample maxima cannot satisfy this gate by relabeling:
they remain `resolved_wind_speed_max`.

The current YSU configuration does not expose a qualified turbulence kinetic
energy diagnostic. Therefore turbulence intensity, IEC design class and
site-suitability claims are outside the initial contract. These may be supplied
later only after a turbulence diagnostic and resolution-aware validation have
passed.

## Acceptance criteria

1. Fixed-height reference fields pass analytic tests for de-staggering,
   interpolation and density-adjusted power.
2. Every requested height is bracketed by HICAR mass levels in every cell; no
   vertical extrapolation or silent missing values are allowed.
3. The reference implementation processes bounded horizontal blocks and
   publishes NetCDF, validation JSON and a ready marker atomically.
4. Online HICAR results agree with the reference extractor within documented
   floating-point tolerances on the 81-by-81 engineering case.
5. Metadata are CF-compatible and state height, time interval, reduction,
   source variables and gust semantics.
6. A 24-hour representative case passes physical comparisons with stations
   and available mast or lidar observations before national production.
7. Storage, throughput, restart continuation and exact composition pass first
   on the 701-by-701 bridge and then on the Swiss 200 m grid before enabling
   the profile for the long-duration archive.

## Staged implementation

1. Reference extractor and product contract (implemented and tested).
2. HICAR fixed-height online diagnostics (implemented and independently
   matched on the 81-by-81 case).
3. Bounded interval-statistics reduction and exact calendar-month merger
   (implemented in the streaming post-model path, including surface/PBL
   means/maxima, direction sectors and threshold counts); qualify persistence
   state only if its user value justifies the archive cost.
4. Surface/PBL and national engineering qualification (metadata, reduction
   and physical 701-by-701 bridge gate plus two-segment Swiss
   restart/merge/retirement gate pass).
5. Observational validation and a separately gated HICAR-native gust method.
6. Integration as a distinct wind-climatology streaming profile without
   changing the existing land/energy `qualification` profile (implemented).
