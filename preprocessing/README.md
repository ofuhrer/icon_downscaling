# hicarprep

`hicarprep` is the target-model-aware ICON-to-HICAR preprocessor. It replaces
the assumption that every slowly changing field belongs in one static file and
the assumption that coarse atmospheric profiles can be completed by runtime
interpolation inside HICAR.

## Product ownership

The field registry in `field_registry.json` assigns every geographic or land
field one lifetime:

- `invariant`: grid, terrain, SLEVE geometry, and fixed material properties;
- `epoch`: piecewise-constant fields such as land use, glacier fraction, and
  land-cover-derived root depth/roughness/vegetation parameters;
- `climatology`: cyclic fields such as monthly LAI and albedo;
- `time_series`: externally evolving fields such as SST and lake temperature;
- `initial_only`: prognostic surface state such as soil temperature/moisture
  and snow.

Unknown variables fail publication. Epoch variables receive an explicit epoch
axis. A climatological field is valid only with a 12-value month coordinate.
This keeps a 20-year run from silently freezing a field merely because a short
case once stored it in a static file.

Later geographic epochs are appended explicitly instead of overwriting the
first domain description. An update must supply every field already owned by
the epoch record; partial records and newly introduced late fields are rejected
rather than filled silently:

```bash
python scripts/hicarprep.py append-epoch \
  --external hicar_external_d01.nc \
  --source land_state_2030.nc \
  --valid-from 2030-01-01T00:00:00Z
```

The library evaluator uses step selection for epochs, cyclic interpolation
between 15th-of-month anchors for 12-month means, and bracketing linear
interpolation for continuous time series. Requests before the first epoch or
outside a continuous series fail.

## Commands

First use the existing public geographic source acquisition to create a raw
domain candidate. Then compile it into three lifetime-correct products and add
the exact HICAR vertical geometry:

```bash
python scripts/hicarprep.py build-domain \
  --source raw_domain.nc \
  --static hicar_static_d01.nc \
  --external hicar_external_d01.nc \
  --initial-surface hicar_surface_initial_d01.nc \
  --epoch-valid-from 2021-01-01T00:00:00Z
```

The default geometry is 80 levels, a 12 km top, 15 m lowest layer, and HICAR's
`auto_level=1` SLEVE formulation. Geometry is rejected if a Jacobian or layer
thickness is non-positive.

Build a reusable direct native-cell operator:

```bash
python scripts/hicarprep.py build-weights \
  --icon-grid icon_native.nc \
  --static hicar_static_d01.nc \
  --output hicar_weights.nc
```

This is the int2lm-style Gaussian kernel solve, not normalized distance
weighting: every target stencil solves the donor-to-donor RBF system and then
normalizes its coefficients to preserve constants. The scale follows int2lm's
`0.05 * grid_distance / 13000 m` rule. If ICON supplies edge-normal `VN`, cache
the separate nine-edge vector kernel, including normal-vector dot products:

```bash
python scripts/hicarprep.py build-vector-weights \
  --icon-grid icon_native.nc \
  --static hicar_static_d01.nc \
  --output hicar_vector_weights.nc
```

Prepare the valid-time land state with an explicit soil-water policy. SMI is
the provisional default: it derives a PWP-to-field-capacity index on native
ICON cells using native `SOILTYP`, remaps it with bounded finite same-surface
support, and reconstructs VWC using the exact target soil class and selected
Noah-MP table. `relative_saturation` instead preserves the fraction of pore
volume; `absolute_w_so` is retained as a diagnostic control:

```bash
python scripts/hicarprep.py prepare-surface \
  --icon-surface icon_native_surface_20200101T000000.nc \
  --static hicar_static_d01.nc \
  --weights hicar_weights.nc \
  --noahmp-table HICAR/run/NoahmpTable.TBL \
  --soil-water-method smi \
  --output hicar_surface_20200101T000000.nc

# Alternatives: --soil-water-method relative_saturation or absolute_w_so
```

All paths use exact source/target layer bounds and target Noah-MP bounds.
Soil-water conservation is not an acceptance criterion.

Skin and soil temperatures use the selectable int2lm-style climatological
height correction by default. Snow is remapped as the paired nonnegative state
SWE plus snow volume; target density is diagnosed from their ratio. This avoids
searching for a distant snowy cell merely because density is undefined where
SWE is zero. Same-surface exceptions record donor distances. Fine-grid water
cells absent from the ICON mask may use only a local in-stencil SKT donor.
Because HICAR reads full-grid soil arrays before dispatching land and water
physics, inactive water soil-temperature columns are filled with the same
valid-time SKT rather than an artificial zero-K remap result.

Merge the selected surface product into the file HICAR actually reads:

```bash
python scripts/hicarprep.py assemble-runtime-domain \
  --static hicar_static_d01.nc \
  --surface hicar_surface_20200101T000000.nc \
  --external hicar_external_d01.nc \
  --output hicar_runtime_domain_20200101T000000.nc
```

The assembler writes HICAR's exact `surface_temperature`,
`soil_temperature`, `soil_vwc`, `soil_deep_temperature`, `swe`, and
`snow_height` names, preserves a 12-month `VEGFRA` axis when supplied, checks
the source hashes and physical ranges, and publishes a ready marker only after
the merged runtime file validates. If external epoch, climatology, or time
series fields were used to decide the surface state, preparation and assembly
must use the exact same external-product hash; land use or glacier ownership
cannot change between those stages.

For depth-varying hydraulics, render HICAR with `soiltexture_var =
'soil_type_layer'` and `nmp_opt_soil = 2`. The corresponding HICAR reader
requires exactly four layers and passes them to Noah-MP's existing per-layer
texture inputs. The provenance-bound winter/summer coupled smoke report is
`case_studies/swiss_200m/validation/hicarprep_coupled_smoke_2020_v1.json`;
it establishes evolved 10-minute ingestion and bounded state only, not a
scientific choice between SMI and relative saturation. The five-file reader
implementation is committed in the local HICAR repository as `bae0c94c`; the
report retains the exact isolated qualification-build source identity.

Transform one canonical native-grid ICON timestamp:

```bash
python scripts/hicarprep.py transform \
  --icon-state icon_native_20200101T000000.nc \
  --static hicar_static_d01.nc \
  --weights hicar_weights.nc \
  --vector-weights hicar_vector_weights.nc \
  --surface-state hicar_surface_20200101T000000.nc \
  --external hicar_external_d01.nc \
  --initial hicar_input_d01_20200101T000000.nc \
  --boundary hicar_bdy_d01_20200101T000000.nc \
  --manifest hicarprep_manifest.json \
  --research-unprojected-wind
```

For model-ready publication, configure HICAR's time-zero output to include
`u`, `v`, `w_grid`, `temperature`, `potential_temperature`, `pressure`, `qv`,
`qc`, `qi`, `density`, `z`, `z_i`, `lat`, and `lon`; include `qr`, `qs`, and
`qg` when present. Then run the same cold-start path the model uses without
taking a time step:

```bash
python scripts/hicarprep.py run-hicar-initialization \
  --hicar-executable HICAR/bin/HICAR \
  --namelist hicar_initialization.nml \
  --initialized-state hicar_out_2020-01-01_00-00.nc \
  --diagnostics hicar_initialization_diagnostics.json \
  --certificate hicar_balance_certificate.json
```

`run-hicar-initialization` invokes `HICAR --initialize-only`, captures the
shared initialization core's structured diagnostics, rejects stale output,
checks exact native U/V/W staggering, independently evaluates the moist
discrete hydrostatic residual (including the loading of every present water
species), and enforces the wind matrix and continuity relative-residual
limits. The resulting certificate hashes the complete canonical atmospheric
state. Publish that exact state directly, without
repeating the provisional ICON transform:

```bash
python scripts/hicarprep.py publish-certified-initialization \
  --initialized-state hicar_out_2020-01-01_00-00.nc \
  --balance-certificate hicar_balance_certificate.json \
  --static hicar_static_d01.nc \
  --weights hicar_weights.nc \
  --initial hicar_input_d01_20200101T000000.nc \
  --boundary hicar_bdy_d01_20200101T000000.nc \
  --manifest hicarprep_publication.json
```

The publisher revalidates the full-state fingerprint and every residual gate,
preserves native U and V face staggering in both the IC and separate sparse
face frames in the LBC, and writes the IC/LBC pair plus an atomic identity
manifest. `certify-initialization` is also available when HICAR was launched
separately. The older `transform --balanced-state --balance-certificate` route
remains available when one combined source-to-publication manifest is needed.

The native adapter contract uses `cell`, `level`, and `half_level` dimensions;
`clat`, `clon`, `HHL`, `T`, `P`, `QV`, `W`; and either earth-relative `U/V` or
`VN` with edge coordinates and edge-normal basis vectors. `HHL.level_order`
may declare `top_to_bottom` or `bottom_to_top`; otherwise ordering is inferred
and validated from HHL. All fields are normalized to HICAR's bottom-to-top
convention. This adapter boundary
is where operational GRIB decoding and ICON field-name normalization belong.
Canonical ICON `QV/QC/QI/...` are tracer mass fractions (specific humidity for
`QV`); saturation and hydrostatic calculations retain that representation.

For ICON REA-L-CH1, `decode-icon-atmosphere` is the strict operational GRIB
adapter. It requires the exact full inventory of pressure, temperature, U, V,
QV, QC, W, HHL, HSURF, and FR_LAND; validates parameter identifiers, units,
vertical types and levels, valid time, reference cycle, native-grid UUID and
cell count; and reverses the archive's top-to-bottom levels into HICAR's
bottom-to-top convention. The REA-L archive does not provide QI. The decoder
therefore fails by default and permits zero QI only through the explicit
`--missing-qi-policy source-absent-zero` provenance-marked policy. The bounded
Balfrin retrieval entry point is
`case_studies/swiss_200m/scripts/decode_rea_l_atmosphere_balfrin.sbatch`.

Horizontal remapping is direct to the exact HICAR target locations. Profiles
and HHL use the same ten-donor Gaussian RBF kernel solve. The vertical
transform works in geometric height, explicitly handles target valleys and
mountains (including removal of source levels buried by target mountains),
reconstructs `QV+QC` through generalized relative humidity, includes
condensate loading in hydrostatic integration, and blends W toward the target
terrain condition through the lowest 4 km with a quiet upper boundary. IC and
sparse physical-width LBC fields are extracted from the same transformed
state. Each LBC snapshot carries its valid time for runtime bracketing.
ICON water tracers are converted together to HICAR dry-air mixing ratios only
after all water species have been assembled. The IC merge requires the exact
same valid time for soil/snow/skin state and evaluates epoch, monthly, and
continuous external fields at that time.

Validate a sequence before making it visible to a runtime reader:

```bash
python scripts/hicarprep.py validate-boundaries \
  --boundary hicar_bdy_d01_20200101T000000.nc \
  --boundary hicar_bdy_d01_20200101T010000.nc \
  --maximum-interval-seconds 3600 \
  --allow-unbalanced-research \
  --manifest hicar_bdy_sequence.json
```

Omit `--allow-unbalanced-research` for model input; the default requires every
snapshot to carry a matching HICAR balance certificate.

The default lateral-W policy is `diagnose`, matching the common COSMO
free-slip treatment. Use `--lbc-w-policy=relax` only after HICAR owns and
validates its native projected W at the sparse boundary.

## Publication gate

The Python-only transform constructs a continuously hydrostatic provisional
state and the terrain lower condition for physical W. It deliberately does
not duplicate HICAR's pressure-adjustment or variational wind solver. Normal IC
and LBC publication therefore requires the time-zero HICAR state and its
matching certificate. The explicit `--research-unprojected-wind` option exists
only for analytic and integration testing; files produced with it carry
`hicar_pressure_adjustment=NOT_APPLIED_RESEARCH_PRODUCT` and
`wind_balance=NOT_APPLIED_RESEARCH_PRODUCT`.
They are not model-ready products.

`build-domain` is a lifetime-aware compiler, not a full EXTPAR replacement.
The public static generator now supplies the target-grid policies needed by
the fields HICAR currently consumes: area-mean terrain, per-source-pixel
WorldCover-to-USGS reclassification followed by category-fraction aggregation,
and thickness-overlap aggregation of SoilGrids texture to all four Noah-MP
layers. It retains the fractions before deriving the dominant category and
labels the 2021 land-cover epoch explicitly. Additional EXTPAR-style
orographic statistics remain optional until a selected HICAR parameterization
actually consumes them. The compiler enforces shared grid/source identity,
land-mask/fraction closure, complete epochs, and finite initial land state.
Epoch fields are selected by validity time without silent back-extrapolation;
a 20-year run therefore needs an epoch valid at its first timestamp (or an
explicitly documented representative backcast product), not merely the latest
land-cover map. `prepare-surface` applies the same rule when it is pointed
directly at an unsplit static file. The research-only
`--allow-static-epoch-back-extrapolation` switch is required for a dated proxy
map used before its validity epoch, and records that exception in both the
surface and assembled HICAR runtime products.
HICAR's reader has not yet been changed to bracket the timestamped sparse LBC
snapshots. That reader remains the model-readiness gate for multi-time forcing;
the certificate proves initialization of an individual state, not runtime LBC
ingestion or interpolation.

Soil-water conservation is deliberately not an acceptance criterion. Soil
state remains initial-only and must instead be finite, mask-consistent, and
within the target soil model's bounds.
