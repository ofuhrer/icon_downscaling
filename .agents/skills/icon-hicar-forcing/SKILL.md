---
name: icon-hicar-forcing
description: Prepare, inspect, or validate HICAR forcing from MeteoSwiss ICON with native hicarprep.
---

# ICON to HICAR forcing

Use `hicarprep`; fieldextra atmospheric preprocessing is retired.

## Source and transformation

- REA-L has one 00 UTC cycle/day. For a valid hour use that cycle plus the
  hour step; midnight uses the new step 0.
- Retrieve P/T/U/V/VN/QV/QC on 80 full levels; W/HHL on 81 half levels;
  HSURF/FR_LAND; and `SKT=502336` at every forcing time. QI is operationally
  absent and must remain visibly `source-absent-zero`.
- `decode-icon-atmosphere` creates the canonical native state.
  `prepare-hicar-forcing` applies cached RBF/vector weights to one exact runtime
  domain. Native GRIB/decoded intermediates may remain job-local.
- Convert water variables jointly to dry-air mixing ratios. Reconstruct
  positive layer thickness; interpolate terrain-adjusted W to authoritative
  target HFL. Rotate U/V into the HICAR basis for the terrain-slope dot product,
  but serialize regular U/V earth-relative.
- Require `target_w_vertical_coordinate=authoritative_static_HFL` and
  `target_w_terrain_wind_basis=HICAR_grid_relative`; older records are stale.
- Regular forcing contains P/T/QV/QC/QI/U/V/W/SST and uses
  `relax_filters=.True.`. Sparse LBC is an explicit scalar-only experiment and
  never inserts winds.

For target water, use compact same-surface SKT support where available and the
exact-time monotone local all-surface RBF baseline elsewhere. Preserve the
unsupported mask and nearest same-surface distance as diagnostics; never use a
remote candidate value. Bind exact time, grid, static checksum, mask, and SST
policy.

Conditioned RBF stencils require condition number <=`1e10` after the minimum
nugget and normalized-weight L1 <=`10`. Clip remapped earth-relative U/V to
local donor component bounds. Apply RBF in 32,768-target chunks without
changing donor order or `np.sum` arithmetic; a whole-domain gather creates an
18.88 GB temporary. Use one exclusive CPU node for each eight-worker national
record and requalify any chunk/arithmetic change end to end.

## Validation and publication

- exact target lat/lon, 80/81 bottom-to-top levels, and runtime-domain identity;
- finite/plausible P/T/moisture/wind; dry-air representation;
- finite hourly SST in 180--350 K on every water cell;
- ordered gap-free hourly records bracketing the segment;
- for sparse tests, identical indices, geometry, weights, and timestamps.

Create NetCDF atomically. Add ready markers only for overlapping campaign
readers and keep source/configuration provenance in one campaign manifest.
Maintained entry points are `scripts/hicarprep.py`, Swiss decoder/target Slurm
scripts, `case_studies/swiss_200m/validation/validate_forcing.py`, and
`preprocessing/hicarprep/boundary.py`. Run a two-hour pilot after decoder,
weights, vertical, moisture, or reader changes.
