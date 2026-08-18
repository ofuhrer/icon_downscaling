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
- Require every native W value to be finite. Its conservative magnitude bound
  may be evaluated on the exact fingerprint-matched scalar-RBF donor support;
  record global extrema and excluded-cell counts, and retain the independent
  all-target-cell 100 m/s forcing validator. This prevents irrelevant finite
  archive extremes outside the target stencil from blocking a domain without
  relaxing any value HICAR consumes.
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
local donor component bounds. The NumPy backend applies RBF in 32,768-target
chunks and retains `np.sum` arithmetic; a whole-domain gather creates an 18.88
GB temporary. The qualified Numba backend accumulates the same fixed donor
sequence without a gather/product array. Bit identity with NumPy is not a
requirement: require deterministic repeats, constant preservation, donor-bound
clipping, quantified field-level roundoff, production validation, and the
two-hour HICAR pilot. Requalify any other stencil/arithmetic change end to end.
Use one exclusive CPU node for each eight-worker national record.

## Validation and publication

- exact target lat/lon, 80/81 bottom-to-top levels, and runtime-domain identity;
- finite/plausible P/T/moisture/wind; dry-air representation;
- finite hourly SST in 180--350 K on every water cell;
- ordered gap-free hourly records bracketing the segment;
- for sparse tests, identical indices, geometry, weights, and timestamps.

Create NetCDF atomically. Add ready markers only for overlapping campaign
readers and keep source/configuration provenance in one campaign manifest.
Use lossless deflate level 1 for recurring regular forcing. Sparse scalar
frames store atmospheric and geometry payloads as float32 in bounded
point-major chunks; this is exactly the precision consumed by HICAR's default
`real` sparse reader. Keep relaxation weights float64, require one storage
dtype across a time sequence, and qualify storage changes against the decoded
values seen by the reader rather than whole-file byte identity.

For the national Swiss domain, use the Numba backend with eight forked column
workers and one horizontal-RBF thread.  The column path may reuse a short-lived
geometry plan after one bulk field validation, but never retain a Python plan
per domain column.  Provenance-bound source-absent-zero QI is qualified; preserve
the decoder's explicit `source_absent_zero` provenance.  Keep the reference
terrain-W-to-HFL operation order: a fused Numba kernel changed only 43 national-
domain float32 W values by one ULP but produced non-finite surface diagnostics
in the two-hour GPU HICAR gate.  Joined fixed-order RBF threads are an opt-in
small-array optimization because they were neutral on the full national grid.

Campaign producers may use the fast publication receipt only with a trusted
preflight static SHA-256.  Bind the receipt to the closed forcing/boundary
digests, exact time, schema, policies, and final paths, then create the ready
marker.  Keep full science-array validation for qualification, diagnosis, or
when no trusted static digest is available.  Reuse campaign-local compiled
Numba caches, but do not persist a separate normalized SST RBF operator unless
a persistent multi-hour process first demonstrates a net I/O benefit.
Maintained entry points are `scripts/hicarprep.py`, Swiss decoder/target Slurm
scripts, `case_studies/swiss_200m/validation/validate_forcing.py`, and
`preprocessing/hicarprep/boundary.py`. Run a two-hour pilot after decoder,
weights, vertical, moisture, or reader changes.
