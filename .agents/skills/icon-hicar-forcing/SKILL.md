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
  levels, plus HSURF and FR_LAND.
- QI is absent in operational REA-L. Use `source-absent-zero` and keep that
  policy visible in the decoded product.
- Native GRIB and the decoded unstructured state may remain job-local.

## Transformation

`decode-icon-atmosphere` creates the canonical native state.
`prepare-hicar-forcing` uses cached RBF/vector weights and one HICAR runtime
domain to create a regular target-grid forcing/clock record and a sparse
lateral-boundary frame from the same state.

Water variables are converted jointly from moist-air mass fractions to
dry-air mixing ratios. HICAR reads P/T/QV/QC/QI/U/V and HFL/HHL. W is omitted
so HICAR diagnoses a dynamically compatible field. Sparse LBC is the only
lateral-relaxation authority; set `relax_filters=.False.`.

Scalar RBF caches must come from the conditioned builder: each stencil solve
has condition number at most `1e10` after the smallest needed diagonal nugget,
and cached normalized weights have L1 amplification at most `10`. Rebuild any
older cache that violates this invariant. Earth-relative U/V remapping is
clipped to the local donor component range; it must not create new wind extrema.

## Checks that matter

- exact target latitude/longitude equality with the runtime domain;
- 80 full and 81 half levels in bottom-to-top order;
- finite fields and plausible P/T/QV/wind ranges;
- target horizontal-wind speed no greater than the gross `200 m s-1` guard;
- dry-air moisture representation;
- identical sparse schema, point indices, geometry, and relaxation weights;
- strictly ordered, gap-free hourly forcing and LBC times that bracket the
  model segment.

Use atomic NetCDF creation. Add `<file>.ready` only because campaign readers
may overlap preprocessing. One campaign manifest records source and
configuration; do not create per-frame certificates or promotion reports.

Maintained entry points are `scripts/hicarprep.py`, the Swiss decoder and
target-record Slurm scripts, `validation/validate_forcing.py`, and
`preprocessing/hicarprep/boundary.py`.

Run a two-hour end-to-end pilot before a longer campaign whenever the decoder,
weights, vertical reconstruction, moisture conversion, or HICAR reader changes.
