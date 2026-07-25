---
name: icon-hicar-domain
description: Design and create HICAR static domains and matching structured ICON forcing subdomains for arbitrary Alpine regions at roughly 100-250 m resolution. Use for horizontal extent, projection, grid size, DEM, land use, soil, boundary topography relaxation, forcing borders, or static-input validation.
---

# ICON-HICAR domain preparation

Build the target static domain independently from atmospheric forcing. A HICAR domain can be finer than its driving grid, but both must be structured grids and the forcing must cover the domain plus lateral relaxation margin.

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

For public-source construction, use Copernicus DEM GLO-30, ESA WorldCover 2021, and SoilGrids with cache reuse under `$SCRATCH/icon_hicar/cache/hicar_static_public`. The script maps WorldCover to USGS categories; set HICAR/NoahMP `LU_Categories='USGS'`.

When ICON-derived `HSURF` is available, pass it as the boundary topography source and retain the generated blend diagnostics. Boundary relaxation is the default for production experiments; disable it only for a controlled sensitivity test.

## Terrain-conditioning sensitivities

Keep the unfiltered static domain as the scientific reference. When testing
grid-scale terrain conditioning, apply it outside HICAR with
`scripts/filter_static_topography.py`; do not mutate terrain at model runtime.
Use the land-aware order-8 Shapiro filter as the default sensitivity because
it targets near-grid-scale variance much more selectively than repeated
second-order 1-2-1 smoothing. Preserve water elevations by default so Alpine
lakes are not reset to sea level.

The filter must act on `topo_highres` before the existing driving-terrain
boundary blend is reapplied. Publish a complete static-file copy, retain raw
terrain and filter-delta diagnostics, verify every non-terrain variable is
bitwise unchanged, record checksums and transfer-response metrics, and create
the ready marker only after validation. Do not present DEM filtering as a
solver cure: existing Swiss one- and two-pass sensitivities worsened the
legacy nonnormal wind operator's convergence.

## Create the forcing subdomain

Derive a fieldextra target grid from the final static file:

```bash
python3 scripts/hicar_domain_to_fieldextra_grid.py \
  --domain-file "$DOMAIN_NC" \
  --border-km 10 \
  --dlon-deg 0.01 \
  --dlat-deg 0.01
```

Or let `scripts/prepare_icon_inputs.sh --domain-file ...` call the helper. Treat 10 km as a starting border, not a universal constant; it must cover HICAR interpolation and lateral relaxation needs.

## Validation gate

- Confirm projected coordinates, latitude/longitude, dimensions, spacing, and orientation.
- Require finite terrain, land-use, soil, and initialization fields.
- Inspect terrain min/max, slopes, water/land fractions, category ranges, and soil-layer dimensions.
- Verify the high-resolution terrain converges smoothly to driving `HSURF` near every boundary.
- Verify forcing coverage with no extrapolation into the HICAR domain or boundary zone.
- Estimate total cells and expected output volume before submitting large 100 m domains.
- Create `<static-file>.ready` only after validation.

Read [references/static-schema.md](references/static-schema.md) for source choices, current reference case, and scaling guidance.
