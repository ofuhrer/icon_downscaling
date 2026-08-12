---
name: icon-hicar-forcing
description: Prepare, inspect, or validate HICAR forcing from MeteoSwiss ICON with the native hicarprep path.
---

# ICON to HICAR forcing

Use `hicarprep`; there is no maintained fieldextra atmospheric path.

## REA-L source policy

- REA-L has one 00 UTC cycle per day. For a valid hour, use that date's cycle
  and `step=hour`; at midnight use the new cycle's step 0.
- Retrieve P, T, VN/U/V, QV, QC on 80 full levels, W and HHL on 81 half
  levels, plus HSURF and FR_LAND. Retrieve `SKT=502336` at every requested
  forcing step for valid-time lake/water temperature; do not reuse the 00 UTC
  land-initialization field throughout the day.
- QI is absent in operational REA-L. Use `source-absent-zero` and keep that
  policy visible in the decoded product.
- Native GRIB and the decoded unstructured state may remain job-local.

## Transformation

`decode-icon-atmosphere` creates the canonical native state.
`prepare-hicar-forcing` uses cached RBF/vector weights and one HICAR runtime
domain to create a regular target-grid forcing/clock record and a sparse
lateral-boundary frame from the same state.

Water variables are converted jointly from moist-air mass fractions to
dry-air mixing ratios. HICAR reads P/T/QV/QC/QI/U/V/W and HFL/HHL. Source W is
terrain-adjusted and interpolated to the exact target HFL mass levels before
the variational projection. Rotate earth-relative U/V into HICAR's target-grid
basis before taking the terrain-slope dot product, but keep serialized regular
U/V earth-relative. Require forcing attributes
`target_w_vertical_coordinate=authoritative_static_HFL` and
`target_w_terrain_wind_basis=HICAR_grid_relative`; older records without both
are stale. Sparse LBC contains only T/P/QV/QC/QI and mass-grid geometry; it
never inserts winds. REA-L SKT is remapped separately and written as hourly
regular-forcing `SST`: use compact same-surface support where available and
the exact-time monotone local all-surface RBF baseline for unsupported target
water. Retain the unsupported-water mask and nearest same-surface candidate
distance as diagnostics, but do not insert the remote candidate value. Require
exact time, target grid, static checksum, water mask, and SST policy version. Set
`relax_filters=.True.` for the selected regular-forcing reference. Sparse LBC
is retained only as an explicit experimental output.

Scalar RBF caches must come from the conditioned builder: each stencil solve
has condition number at most `1e10` after the smallest needed diagonal nugget,
and cached normalized weights have L1 amplification at most `10`. Rebuild any
older cache that violates this invariant. Earth-relative U/V remapping is
clipped to the local donor component range; it must not create new wind extrema.
RBF application is chunked over 32,768 target points while preserving each
target's donor order and `np.sum` arithmetic. Do not restore a whole-domain
gather: on the Swiss 80-level/ten-donor grid it creates an 18.88 GB temporary,
whereas the selected chunk bounds it near 210 MB. A full national record was
exact for all 2,383,027,129 values and all variable attributes. On its matched
timestamp, chunking reduced a pathological horizontal-remap stage from 1,830
to 262 seconds and total transformation from 2,225 to 659 seconds; other
unchunked records already ran near the chunked end-to-end time, so treat this
as a worst-case latency improvement rather than a general speedup factor.
Continue to use one exclusive CPU node per eight-worker record: Slurm's
copy-on-write aggregate RSS remained about 238 GB, and the benchmark does not
qualify two records per node. Re-qualify any different chunk size or arithmetic
with one bounded end-to-end record before deployment.

## Checks that matter

- exact target latitude/longitude equality with the runtime domain;
- 80 full and 81 half levels in bottom-to-top order;
- finite fields and plausible P/T/QV/wind ranges;
- finite hourly SST and 180--350 K on every static-domain water cell;
- target horizontal-wind speed no greater than the gross `200 m s-1` guard;
- dry-air moisture representation;
- for sparse experiments, identical schema, point indices, geometry, and
  relaxation weights;
- strictly ordered, gap-free hourly forcing times that bracket the model
  segment; sparse experiments require the same LBC timestamps.

Use atomic NetCDF creation. Add `<file>.ready` only because campaign readers
may overlap preprocessing. Publish a sparse companion only when the run selects
that path. One campaign manifest records source and
configuration; do not create per-frame certificates or promotion reports.

Maintained entry points are `scripts/hicarprep.py`, the Swiss decoder and
target-record Slurm scripts, `validation/validate_forcing.py`, and
`preprocessing/hicarprep/boundary.py`.

Run a two-hour end-to-end pilot before a longer campaign whenever the decoder,
weights, vertical reconstruction, moisture conversion, or HICAR reader changes.
