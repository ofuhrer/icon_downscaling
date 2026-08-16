---
name: icon-hicar-domain
description: Design and validate Alpine HICAR static domains and matching ICON forcing coverage at roughly 100-250 m.
---

# ICON-HICAR domain preparation

Build static domains independently of atmospheric forcing. Record region,
border, CRS, bounds/centre, `dx/dy`, and `nx/ny`; estimate cell/storage cost
before selecting 100 m. The target may be finer than ICON, but forcing must
cover the domain and relaxation zone without extrapolation.

## Static construction

Inspect `python3 scripts/prepare_static_inputs.py --help`. Public defaults are
Copernicus GLO-30, ESA WorldCover 2021, and SoilGrids, cached below
`$SCRATCH/icon_hicar/cache/hicar_static_public`.

- Aggregate WorldCover by modal source class before USGS mapping; never use one
  nearest 10 m pixel for a 100--250 m cell.
- Thickness-weight all six SoilGrids intervals onto Noah-MP's four layers.
  Write surface `soil_type`, `soil_type_layer`, bounds, and composition.
- Dominant soil (`nmp_opt_soil=1`) is the control. Option 2 is a paired runtime
  intervention requiring exactly four layers and `soiltexture_var`.
- Use high-resolution interior terrain blended toward ICON `HSURF` at the
  boundary. For land/soil attribution, copy baseline terrain fields bitwise
  with `--preserve-topography-from` and bind their checksum.

## Optional terrain sensitivity

Keep unfiltered terrain as reference. Apply land-aware order-8 filtering only
with `scripts/filter_static_topography.py`, outside HICAR, to `topo_highres`
before reapplying the driving-terrain blend. Preserve water heights, retain the
delta, and require all non-terrain variables unchanged. Do not present filtering
as a solver cure; Swiss tests worsened the legacy operator's convergence.

Terrain-radiation HLM/SVF/slope/aspect is separately audited physics input from
the exact runtime terrain. Follow `references/static-schema.md` and the national
geometry script before enabling it.

## Trust checks

- exact projected/geographic coordinates, dimensions, spacing, orientation;
- finite terrain, masks, land use, soil, and initialization fields;
- plausible terrain/slope/category/water fractions and smooth boundary blend;
- soil categories 1--13, surface/layer consistency, and 95--105% composition
  closure for every layer/cell;
- exact forcing coverage and no extrapolation;
- positive vertical geometry and estimated output/resource volume.

For shared statics: write temporary, validate, atomically rename, then create
the ready marker. Private sequential intermediates need no publication bundle.
