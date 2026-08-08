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
| Atmospheric GRIB ingest stopped at a canonical adapter contract | `decode-icon-atmosphere` strictly decodes the complete REA-L pressure, temperature, wind, water-vapour/cloud-water, W, HHL, terrain, and land-fraction inventories with exact time, step, level, unit, grid, and EXTPAR checks | Two consecutive archived Storm Sabine timestamps pass the operational FDB/ecCodes path with exact 561+83 message inventories; missing QI fails unless the explicit source-absence policy is selected |
| LBC files lacked a sequence contract | `validate-boundaries` requires two or more strictly increasing, schema-identical, finite states, optionally bounds the time gap, and writes an identity manifest | Duplicate/reversed times and excess gaps fail |
| HICAR could not consume target-native sparse LBC sequences | The runtime holds only the active two-record bracket, reads contiguous rank-local mass/U/V support runs, advances at exact forcing events, interpolates the authoritative basis in absolute time, and applies stagger-aware physical relaxation | Synthetic contract tests, compiler/device checks, and the bounded 2020-02-10 bracket-crossing integration qualify ingestion and turnover without reverting to the legacy full-domain forcing contract |
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

## Model-readiness milestones and remaining science

### 1. Completed: extend the real-state envelope and qualify the hydrostatic threshold

The shared core, initialization-only driver, diagnostics, certificate issuer,
and certified writer path are implemented. Two independent retained
ICON REA-L-CH1 states now pass end to end:

- 2020-01-15 SMI: hydrostatic residual `4.1517e-4`, wind-matrix relative
  residual `7.8683e-6`, and mass-continuity relative residual `3.4249e-6`;
- 2020-07-02 SMI: hydrostatic residual `4.4217e-4`, wind-matrix relative
  residual `5.9105e-6`, and mass-continuity relative residual `6.5628e-6`.

Both used the exact same 701x701x80 initialization-only path and passed the
fixed wind gates. The independent Storm Sabine case on 2020-02-10 adds three
certified timestamps. Its 00/01/02Z hydrostatic residuals are `4.0648e-4`,
`3.8800e-4`, and `3.7469e-4`; wind-matrix residuals are
`3.9323e-6`, `4.0481e-6`, and `3.7367e-6`; mass-continuity residuals are
`4.1677e-6`, `3.7992e-6`, and `4.0024e-6`. All pass. The same states publish
exact-staggered IC/LBC products with distinct mass, U-face, and V-face sparse
frames.

This independently supports retaining the provisional `5e-3` hydrostatic
gate with about twelve-fold observed margin. It does not justify tightening
the gate from three adjacent storm snapshots or calling the path
production-qualified. Continuous Python hydrostatic integration remains
provisional input to HICAR's own cold start.

### 2. Completed: implement the sparse two-record HICAR reader

The HICAR reader keyed by `sparse_lbc_file_list` now:

- keeps exactly the lower and upper valid-time records in memory;
- reads only contiguous rank-local runs from separate mass, U-face, and V-face
  supports rather than materializing full boundary frames;
- rejects gaps, duplicates, grid/static/support/weight/schema changes, skipped
  forcing events, and extrapolation;
- interpolates only the declared independent basis (`T,P,U,V` and water
  species; projected W only under `relax`);
- refreshes water/EOS/dependent diagnostics after interpolation;
- applies relaxation on stagger-specific physical-width frames and defines any
  full-domain upper-sponge fields separately.

The reader and renderer contracts cover endpoint selection, absolute-time
linearity, nonnegative water, no-extrapolation failure, and exact forcing-list
cadence. The bounded two-hour Storm Sabine integration is the end-to-end
turnover qualification. Restart equivalence at a turnover remains useful
production hardening rather than a blocker for the scientific R&D path.

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

### 4. Completed: atmospheric ICON GRIB adapter

`decode-icon-atmosphere` now performs strict operational REA-L decoding into
the canonical NetCDF interface. It requires the exact 561-message dynamic
inventory (`P/T/U/V/QV/QC` on 80 full levels and W on 81 half levels), the
83-message geometry inventory (HHL plus HSURF and FR_LAND), exact parameter
IDs, units, level types and inventories, reference/valid times and forecast
step, grid UUID and 1,147,980-cell count, and coordinate identity with the
tuned ICON EXTPAR. It reverses archive top-to-bottom levels into HICAR order
and validates pressure/HHL monotonicity and the lowest HHL against HSURF.

REA-L does not archive QI. The decoder fails by default and allows zero QI only
through the explicit provenance-marked `source-absent-zero` policy; it never
reinterprets QC. Optional QR/QS/QG are not invented. Two consecutive archived
Storm Sabine timestamps (00 and 01Z) pass the real FDB/ecCodes path with the
same UUID `17643da2574959b644d254a3cd6e2bc0` and exact time/step metadata.

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
