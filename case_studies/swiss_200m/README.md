# Switzerland 200 m production-candidate case

This case is the national engineering baseline for dynamically downscaling
ICON REA-L-CH1 with HICAR.  It uses the discretely adjoint variational-wind
projection, 80 vertical levels, a 12 km model top, SLEVE decay 2/6, and the
validated HICAR terrain split (window 5, 10 cycles).

The current qualification target is a six-hour run from seven continuous,
hourly forcing records.  It is accepted only when all of the following pass:

- constructed-grid geometry: mass Jacobian at least 0.1 and interface
  thickness at least 5 m;
- every reported wind solve: relative residual at most `1e-5`;
- independent adjoint mass-constraint ratio at most `2e-5`;
- complete two-record engineering output with finite required fields,
  increasing height, and decreasing pressure;
- height-aware comparisons of the initial and final HICAR states against the
  matching REA-L forcing records;
- 45-minute model wall-time, 40 GiB peak task RSS, and 30 GiB two-record
  output budgets.

The input and acceptance contract is
[`config/production_6h_plan.json`](config/production_6h_plan.json).  The
reproducible Balfrin stages are:

1. `scripts/publish_rea_l_6h_series_balfrin.sbatch`
2. `scripts/run_adjoint_6h_balfrin.sbatch`
3. `scripts/validate_adjoint_output_balfrin.sbatch`
4. `scripts/compare_adjoint_6h_to_forcing_balfrin.sbatch`
5. `scripts/finalize_adjoint_6h_qualification_balfrin.sbatch`

Ready markers are publication guarantees: the corresponding data or report
has been completely written and validated.

## Terrain policy

The qualified baseline keeps the published DEM unchanged.  External terrain
filtering is a separate scientific anti-aliasing sensitivity, not a
wind-solver repair.  If used, prefer the scale-selective, land/sea-aware
static-preprocessing path documented in `static_sensitivities/`; regenerate
all terrain-derived static fields consistently and retain the unfiltered case
as the reference.  A repeated 1-2-1 filter is not the preferred default
because its attenuation is too broad in scale.

## What qualification does not establish

A six-hour run proves a bounded production-candidate workflow, not
climatological skill.  Multi-day cases, boundary-zone and terrain-class
diagnostics, and comparisons with independent observations remain necessary.
The 100 m resource figures in the qualification manifest remain estimates
until an actual constructed 100 m geometry and bounded capacity run pass.

The next scientific gate is defined in
[`config/scientific_pilot_plan.json`](config/scientific_pilot_plan.json).
It starts from a published REA-L-derived skin/soil/snow initialization, uses
the bounded `qualification` output profile, and escalates from summer and
winter events to a month and then an annual seasonal cycle. A passing
initialization converter or two-hour smoke test does not authorize the
20-year campaign by itself.
