# hicarprep blocker implementation plan

This plan is an engineering sequence, not step-wise meteorological
downscaling. Every atmospheric timestamp is transformed directly from native
1--2 km ICON cells to the final 100--200 m HICAR grid.

## Implemented in the current slice

| Blocker | Implementation | Acceptance now |
| --- | --- | --- |
| Soil-water policy was case-specific | `surface.py` provides first-class `smi` (current default) and `relative_saturation`, plus `absolute_w_so` as a diagnostic control. Both dimensionless methods are derived with native ICON `SOILTYP`, same-surface normalized, vertically overlap-mapped, and reconstructed with the exact target Noah-MP class/table | Synthetic tests reproduce the exact int2lm equations; multi-policy validation reports bounds, clipping, soil-class discontinuities, and cross-method differences without claiming that plausibility selects a winner |
| Real surface ingest and seasonal plausibility were missing | The REA-L packager combines native soil/snow/skin GRIB with matching tuned EXTPAR; bounded RBF remapping uses same-surface normalization, stencil-extrema limiting, paired nonnegative SWE/snow-volume interpolation, and distance-audited local fallbacks | Winter, summer, and late-autumn 200 m Alpine cases all pass against the definitive layered static; `hicarprep_surface_multicase_2020_v4.json` records one static hash, 2.581 km maximum fallback distance, and method sensitivity while leaving policy selection indeterminate. Its only warnings explicitly identify the 2021 WorldCover proxy used for the 2020 research cases |
| Valid-time land state was not part of the IC | `prepare-surface` writes timestamped soil/snow/skin state; `transform --surface-state --external` requires exact time/grid agreement and evaluates epoch/monthly/continuous fields at that time | Timestamp mismatch and external extrapolation fail. Direct use of raw epoch-labelled static data also fails before its valid-from time unless a research-only override records the exception |
| ICON/HICAR water representations differed | All present water species use one dry-air denominator; density is refreshed without changing physical mass | Joint `QV/QC/QI` test preserves density and converts every species |
| Static/external/initial products could drift apart | `validate_product_set` checks common source/grid identity, immutable land mask, fraction closure, complete epochs, and finite land state. `assemble-runtime-domain` merges the selected valid-time surface state into the exact variables HICAR consumes and verifies all hashes | `build-domain` validates lifetime-separated products; the runtime assembler validates and atomically publishes the actual HICAR domain file |
| Static generation used interpolation or modal classes where aggregation is required | The public generator area-averages DEM height, reclassifies every 10 m WorldCover pixel before aggregating 24 USGS fractions, derives the dominant class afterward, and overlap-averages SoilGrids texture to four Noah-MP layers | Analytic tests cover fraction closure and layer overlap; the full 701x701 Alpine bridge preserves terrain bitwise, closes fractions to 1.45e-7, contains 395,197 mixed-category cells, and changes texture below the surface in 91,396 cells by layer four |
| HICAR silently broadcast one soil class through Noah-MP | `soiltexture_var` reads exactly four target-layer classes and `nmp_opt_soil=2` requires that field; the option metadata and namelist renderer expose the contract | Minimal qualification commit `e46671bccb628c43ea87a50630c4a041bb10bc84` (baseline b514 plus the five soil-texture integration changes) produced GPU-NCCL executable SHA256 `379f26c…c30d284`; winter and summer SMI/relative arms all ingested it and completed. The equivalent reader patch is committed locally in HICAR as `bae0c94c` |
| Surface GRIB identity and semantics were implicit | The native adapter enforces grid UUID, exact valid time, instantaneous step type, surface/depth level type, units, exact soil-depth inventory, EXTPAR cell inventory, finite values, and source checksums | Three archived REA-L timestamps pass the contract; stale, duplicate, accumulated, mis-unit, mis-levelled, and physically invalid inputs fail tests |
| LBC files lacked a sequence contract | `validate-boundaries` requires two or more strictly increasing, schema-identical, finite states, optionally bounds the time gap, and writes an identity manifest | Duplicate/reversed times and excess gaps fail |
| LBC research files could bypass the balance gate | IC and LBC writers now both refuse publication unless the explicit research override is used | Direct unbalanced LBC write fails |
| Lateral W ownership was ambiguous | `--lbc-w-policy=diagnose` is the default; `relax` explicitly includes W | Product metadata records the selected policy |
| COSMO runtime evidence was missing | The COSMO guide and `tmp/cosmo/cosmo` source were audited; exact references are incorporated in the main design | The obsolete source-gap claim is removed |
| HICAR initialization was not callable or certifiable offline | Normal startup and `--initialize-only` now share `hicar_initialization_core`; HICAR emits structured wind-solver and conservation diagnostics, while hicarprep validates exact output staggering, independently gates discrete hydrostatic residual, and hashes the full state, valid time, producer commit, and residual limits into a certificate | Synthetic native-NetCDF tests cover transposition, U/V/W shapes, fingerprint binding, hydrostatic acceptance, and continuity rejection. Real 701x701x80 winter and summer REA-L states both completed on the GPU-NCCL path and certified; native-face IC/LBC publication also passed on the winter state |

The final audit also closed three file-contract bugs found by end-to-end use:
runtime assembly now uses a buffered fallback after GPFS `sendfile` errors;
inactive nonland soil temperatures carry valid-time SKT instead of zero-K
placeholders; and any external epoch/climatology product is hash-identical
between surface preparation and runtime assembly. The final audit additionally
closed the raw-static epoch-validity bypass and made the coupled smoke runner
materialize a fresh two-record list from retained ready-marked forcing cache
records rather than stale retired segment paths.
The concise full-domain static evidence is retained in
`case_studies/swiss_200m/validation/hicarprep_static_fractional_200m_v1.json`.
The provenance-checked evolved-state smoke evidence is retained in
`case_studies/swiss_200m/validation/hicarprep_coupled_smoke_2020_v1.json`.
All four 10-minute outputs pass active-land bounds and retain distinct SMI and
relative-saturation states. Each winter arm also contains one
nearly method-independent urban-cell sensible-heat-flux tail near
`-505 W m-2`; the assessment preserves this as a warning that must be followed
through the 6--24 hour relaxation experiment. This establishes reader
integration and short numerical plausibility only; it does not rank the
transfer hypotheses.

## Remaining model-readiness work

### 1. Extend the real-state envelope and qualify the hydrostatic threshold

The shared core, initialization-only driver, diagnostics, certificate issuer,
and certified writer path are implemented. Two independent retained
ICON REA-L-CH1 states now pass end to end:

- 2020-01-15 SMI: hydrostatic residual `4.1517e-4`, wind-matrix relative
  residual `7.8683e-6`, and mass-continuity relative residual `3.4249e-6`;
- 2020-07-02 SMI: hydrostatic residual `4.4217e-4`, wind-matrix relative
  residual `5.9105e-6`, and mass-continuity relative residual `6.5628e-6`.

Both used the exact same 701x701x80 initialization-only executable and passed
the fixed wind gates. The winter state also passed exact-staggered IC/LBC
publication with distinct mass, U-face, and V-face sparse frames. The next
controlled experiment is to add an independently retained autumn/storm state
and establish:

1. the observed discrete hydrostatic residual distribution and a defensible
   tolerance (the current `5e-3` default is provisional, not a scientific
   conclusion);
2. wind solver status zero, matrix relative residual at most `1e-5`, and mass
   continuity relative residual at most `2e-5`;
3. bitwise atmospheric identity between the certified full state and the IC,
   plus exact equality of IC/LBC values on each extracted support;
4. failure on a changed value, missing staggered face, stale output, or
   mismatched valid time.

The two cases close the engineering execution blocker but do not justify
tightening the provisional hydrostatic threshold or calling the path
production-qualified. Continuous Python hydrostatic integration remains
provisional input to HICAR's own cold start.

### 2. Implement the sparse two-record HICAR reader

Add a reader keyed by the sequence manifest. It must:

- keep exactly the lower and upper valid-time records in memory;
- reject gaps, duplicates, grid/static identity changes, and extrapolation;
- interpolate only the declared independent basis (`T,P,U,V` and water
  species; projected W only under `relax`);
- refresh water/EOS/dependent diagnostics after interpolation;
- apply relaxation on stagger-specific physical-width frames and define any
  full-domain upper-sponge fields separately.

Acceptance is endpoint identity, midpoint linearity for the independent
basis, nonnegative water, no-extrapolation failure, restart equivalence at an
LBC turnover, and a short forced run crossing at least two turnovers.

### 3. Extend static aggregation only for selected physics

The fields consumed by the current HICAR configuration now have explicit
aggregation and lifetime policies. Add further EXTPAR-style products only when
a selected HICAR parameterization consumes them, principally:

- DEM mean/min/max/variance, slope/aspect, subgrid orographic statistics, and
  roughness scales;
- additional ordinary-soil texture fractions or pedotransfer inputs beyond the
  current four-layer dominant USDA class;
- lake-depth or urban parameters if the corresponding physics is enabled.

Do not add unused static fields merely to imitate EXTPAR's inventory. Any
extension must retain the existing fraction closure, epoch axes, analytic
aggregation tests, and the rule that terrain cannot change after geometry
build.

### 4. Complete the atmospheric ICON GRIB adapter

The surface adapter is operational. Complete the same strict decoding for
atmospheric field semantics, units, native grid UUID, HHL ordering, and valid
time into the canonical NetCDF interface.
Require `QC/QI`; preserve optional hydrometeors; never invent missing upper
profiles or frozen soil water. Winter liquid/ice partition needs an explicit
target-physics diagnosis or a declared fallback because REA-L lacks
`W_SO_ICE`.

Acceptance is byte-stable canonical output for one archived timestamp,
field/unit inventory checks, and equality of native coordinates with cached
operator fingerprints.

### 5. Scientific qualification

The 10-minute winter/summer ingestion A/B now passes. Continue with controlled
6--24 hour A/B experiments rather than adding production machinery:

- SMI versus relative saturation, with direct absolute `W_SO` retained only as
  a control, separately by warm/winter/wet regimes;
- int2lm-style one-anchor versus ICON-style bracketed pressure reconstruction,
  both followed by the identical HICAR discrete adjustment;
- 5/10/20 km relaxation widths;
- diagnose versus relax projected lateral W;
- 100 versus 200 m only after the 200 m contracts pass.

Evaluate startup pressure tendency, divergence, W noise, wind, surface fluxes,
soil/snow evolution, and boundary-turnover artifacts. Input plausibility is a
prerequisite, not evidence that one soil-transfer policy is scientifically
superior. Soil-water conservation is reported but is not an acceptance gate.
