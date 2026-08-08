---
name: icon-hicar-domain
description: Design and create HICAR static domains and matching structured ICON forcing subdomains for arbitrary Alpine regions at roughly 100-250 m resolution. Use for horizontal extent, projection, grid size, DEM, land use, soil, boundary topography relaxation, forcing borders, or static-input validation.
---

# ICON-HICAR domain preparation

Build the target static domain independently from atmospheric forcing. A HICAR domain can be finer than its driving grid, but both must be structured grids and the forcing must cover the domain plus lateral relaxation margin.

Geometry, coordinate consistency, finite fields, forcing coverage, and
unchanged controls are required for interpretable experiments. Publication
bundles and exhaustive checksums are needed only when artifact identity,
concurrent use, or recoverability makes them material.

## Domain design

1. Define the scientific region and add explicit geographic border. Support small catchments through Switzerland-scale Alpine domains, but estimate cell count and storage before choosing 100 m.
2. Choose a projected regular grid suitable for the domain. Record center/bounds, CRS, `dx`, `dy`, `nx`, and `ny` in a case manifest.
3. Keep the HICAR grid independent of the forcing grid. Regrid ICON to a coarser structured grid that covers the target and boundary zone; do not force the atmospheric input to 100-250 m.
4. Use high-resolution terrain and land surface in the interior, blended toward ICON driving terrain at lateral boundaries.

## Create static inputs

Inspect the current CLI before running:

```bash
python3 scripts/prepare_static_inputs.py --help
```

For public-source construction, use Copernicus DEM GLO-30, ESA WorldCover 2021, and SoilGrids with cache reuse under `$SCRATCH/icon_hicar/cache/hicar_static_public`. Aggregate WorldCover to the model grid with the modal source category before mapping it to USGS; never use one nearest 10 m pixel to represent a 100-250 m cell. Set HICAR/NoahMP `LU_Categories='USGS'`.

Prepare SoilGrids sand/silt/clay from all six standard depth intervals and thickness-weight them onto Noah-MP's 0-10, 10-30, 30-70, and 70-150 cm layers. Retain `soil_type` as the surface-layer compatibility field and write `soil_type_layer` plus layer bounds and composition diagnostics. Use dominant soil (`nmp_opt_soil=1`) as the established control; test `nmp_opt_soil=2` only as an explicit paired intervention. The latter requires the HICAR `soiltexture_var` namelist entry and exactly four layers.

For a land-cover/soil-only attribution candidate on an existing grid, use
`--preserve-topography-from` with the published baseline rather than
regenerating the DEM. The candidate must copy `topo`, `topo_highres`,
`topo_driving`, and `topo_blend_weight` bitwise, bind the baseline checksum,
and pass a zero-tolerance terrain comparison. B (`nmp_opt_soil=1`) and C
(`nmp_opt_soil=2`) then share the same candidate NetCDF bytes; their physical
difference belongs in the runtime configuration and paired process test.

When ICON-derived `HSURF` is available, pass it as the boundary topography source and retain the generated blend diagnostics. Boundary relaxation is the established control; disable it only as an explicit sensitivity.

## Terrain-conditioning sensitivities

Keep the unfiltered static domain as the scientific reference. When testing
grid-scale terrain conditioning, apply it outside HICAR with
`scripts/filter_static_topography.py`; do not mutate terrain at model runtime.
Use the land-aware order-8 Shapiro filter as the default sensitivity because
it targets near-grid-scale variance much more selectively than repeated
second-order 1-2-1 smoothing. Preserve water elevations by default so Alpine
lakes are not reset to sea level.

The filter must act on `topo_highres` before the existing driving-terrain
boundary blend is reapplied. For an interpretable A/B, retain the unfiltered
terrain and filter delta and verify every non-terrain variable is unchanged.
Add checksums, a complete static copy, and a ready marker when the artifact is
shared or exact identity matters. Do not present DEM filtering as a solver
cure: existing Swiss one- and two-pass sensitivities worsened the legacy
nonnormal wind operator's convergence.

## Atmospheric target

`hicarprep` writes atmospheric forcing directly on the exact HICAR grid. The
sparse boundary width is a physical-distance setting, currently 10 km; no
separate regular forcing subdomain or target-grid text file is maintained.

## Scientific trust checks

- Confirm projected coordinates, latitude/longitude, dimensions, spacing, and orientation.
- Require finite terrain, land-use, soil, and initialization fields.
- Inspect terrain min/max, slopes, water/land fractions, category ranges, and soil-layer dimensions.
- Require soil categories 1-13, `soil_type == soil_type_layer[0]`, and sand+silt+clay closure within 95-105% at every layer/cell for the research static schema.
- Verify the high-resolution terrain converges smoothly to driving `HSURF` near every boundary.
- Verify forcing coverage with no extrapolation into the HICAR domain or boundary zone.
- Estimate total cells and expected output volume before submitting large 100 m domains.
- For shared or concurrently consumed statics, write to a temporary file,
  validate it, atomically rename it, and create `<static-file>.ready` last.
  Private sequential exploratory intermediates need not be published.

Read [references/static-schema.md](references/static-schema.md) for source choices, current reference case, and scaling guidance.
