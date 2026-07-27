---
name: icon-hicar-forcing
description: Prepare, package, inspect, or validate structured NetCDF forcing for HICAR from MeteoSwiss ICON output. Use for ICON GRIB-to-NetCDF conversion, fieldextra regridding, vertical-level handling, required forcing variables, ready markers, forcing file lists, or diagnosing HICAR input incompatibilities.
---

# ICON to HICAR forcing

Work from the current coordinator checkout; run data-heavy steps on Balfrin
under `$SCRATCH/icon_hicar`.

## Workflow

1. Select ICON dynamic and static files with `$icon-balfrin-grib`. Prefer full-column operational `lfff` or `lffm` files; use the matching `lfff00000000c` for geometry.
2. Create the HICAR static domain first with `$icon-hicar-domain` when the target is not already frozen.
3. Regrid native ICON triangles to a structured grid covering the HICAR domain plus border. HICAR assumes structured `y,x`; do not feed native triangles directly.
4. Run the repository converter:

   ```bash
   scripts/prepare_icon_inputs.sh \
     --input "$DYNAMIC_GRIB" \
     --static "$STATIC_GRIB" \
     --domain-file "$DOMAIN_NC" \
     --output "$OUTPUT_NC" \
     --load-balfrin-modules
   ```

5. Validate schema, vertical order, coordinates, coverage, finite values, and the `.ready` marker before adding the file to a forcing list.
6. Generate all forcing times before long runs, or list planned paths up front and enable HICAR ready-file waiting for streaming production.

## Conversion rules

- Use operational fieldextra on Balfrin; set `ulimit -s unlimited` and `OMP_STACKSIZE=500M`.
- Do not load Balfrin NCO/NetCDF modules before fieldextra. `prepare_icon_inputs.sh --load-balfrin-modules` deliberately loads NCO afterward.
- Preserve `P,QV,T,U,V` on full levels and `W` on half levels.
- Preserve half-level geometry as `HHL`; derive full-level `HFL` by adjacent half-level averaging.
- Reverse ICON/fieldextra top-to-bottom full and half levels into HICAR bottom-to-top order.
- Remove singleton ensemble dimensions and stale vertical/cell-method metadata.
- Keep winds earth-relative unless a verified vector-rotation step says otherwise.
- Write NetCDF4. Create `<file>.ready` only after the final file is complete and validated; remove stale markers before rewriting.
- For REA-L-CH1, package hourly cycle-plus-step records with
  `inputinterval=3600`: geometry comes from step 0, while atmospheric fields
  and `W` come from the requested step. Record both cycle and step in the case
  manifest.
- The REA-L-CH1 production archive has one 00 UTC cycle per day. For each
  valid UTC hour, use that date's cycle and `step=hour`; at midnight use the
  new cycle's step 0, never the previous cycle's step 24. The two midnight
  representations are close but not byte-identical.
- For long production, use an immutable chunk plan plus a bounded CPU array.
  Each worker should narrowly read one atmospheric record from FDB, reuse a
  once-per-cycle `HHL/HSURF/FR_LAND` cache, regrid, validate, atomically
  publish NetCDF plus hashes, and remove native GRIB/work data. Never retain
  the physical FDB container files or an unpacked multi-year GRIB staging
  archive.
- HICAR forcing files may be retired only after the matching model output and
  exact-end restart have their validated ready publications. Recheck every
  planned forcing hash before deletion; keep plans, source coordinates,
  validation reports, and manifests.
- Independent REA-L chains can initialize land state from `SKT`, eight-level
  `T_SO`, eight-layer integrated `W_SO`, `W_SNOW`, and `RHO_SNOW`. Depth-remap
  soil temperature, convert/remap `W_SO` to volumetric content, and derive
  snow height as `W_SNOW/RHO_SNOW`. These transformations require their own
  validation before yearly chains are scientifically equivalent.
- REA-L `T_SO` is point data at
  `0/0.005/0.02/0.06/0.18/0.54/1.62/4.86 m`. `W_SO` is layer-integrated
  `kg m-2` over bounds
  `0/0.01/0.03/0.09/0.27/0.81/2.43/7.29/21.87 m`. Preserve separate
  temperature-depth and water-layer coordinates in intermediate NetCDF;
  never attach both fields to one generic level axis.
- HICAR/NoahMP uses four soil layers bounded by
  `0/0.1/0.3/0.7/1.5 m`. Interpolate `T_SO` to layer midpoints and remap
  `W_SO` by exact vertical overlap. Convert each target mass to volumetric
  water as `mass / (1000 kg m-3 * target thickness)`. This conserves the
  source water represented within the HICAR 0--1.5 m column.
- Regrid the native ICON fields once to the bounded regular fieldextra grid,
  then bilinearly sample that product on the curvilinear HICAR domain. Fail
  on missing land values. If positive `W_SNOW` has invalid `RHO_SNOW`, fail
  unless an explicit density fallback is supplied and recorded in the
  manifest.
- Publish the initialized static copy with `surface_temperature`,
  `soil_temperature`, `soil_vwc`, `swe`, and `snow_height`; enable the
  namelist `swe_var` and `snowh_var` only for this qualified product.

## Validation gate

Reject forcing when any of these fail:

- required variables or dimensions are absent;
- `z_hl = z + 1` is false;
- heights are not monotonically bottom-to-top;
- time coordinates or file-list order are inconsistent;
- the structured forcing grid does not cover the HICAR domain and relaxation border;
- NaN/Inf values occur in required fields;
- pressure, temperature, humidity, or wind ranges are physically implausible;
- the static and dynamic grids differ.

Read [references/forcing-schema.md](references/forcing-schema.md) when checking exact fields, dimensions, source families, or the frozen reference case.

## Repository tools

- `scripts/prepare_icon_inputs.sh`: production-oriented one-file conversion.
- `scripts/hicar_domain_to_fieldextra_grid.py`: derive a bordered fieldextra grid.
- `scripts/balfrin_fieldextra_icon_hicar_full_column_smoke.sh`: isolate fieldextra extraction.
- `scripts/balfrin_package_hicar_forcing_smoke.sh`: isolate packaging and level reversal.
- `scripts/balfrin_prepare_hicar_smoke_run.sh`: prepare a minimal runnable case.
- `case_studies/swiss_200m/scripts/produce_rea_l_land_state_balfrin.sbatch`:
  retrieve and fieldextra-regrid one REA-L land/snow state.
- `case_studies/swiss_200m/validation/build_rea_l_land_initialization.py`:
  spatially sample, vertically remap, validate, hash, and publish a HICAR
  static initialization copy.
