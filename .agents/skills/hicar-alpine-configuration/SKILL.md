---
name: hicar-alpine-configuration
description: Choose and validate HICAR namelist, vertical grid, terrain coordinates, wind solver, and physics for Alpine downscaling.
---

# HICAR Alpine configuration

Treat configuration choices as scientific hypotheses. Change one mechanism at
a time on the smallest representative case and inspect fields, not just model
termination.

## Selected Swiss 200 m baseline

- 80 levels, 12 km model top, 20 m lowest layer, stretch 0.65, with a 12 m
  hard minimum after terrain compression;
- SLEVE decay 2/6, terrain smoothing radius 5 for 10 cycles;
- RK3, third-order horizontal and vertical advection, flux correction;
- discretely adjoint variational wind, `alpha_const=1`, 2500 iterations;
- Sx on with 500 m smoothing and density advection on;
- Morrison microphysics, YSU PBL, Noah-MP, revised MM5 surface, RRTMGP;
- four-layer depth-varying soil texture with SMI land initialization;
- terrain radiation off.

Atmospheric forcing is hicarprep P/T/U/V/QV/QC/QI in dry-air mixing ratios.
Set `qv_is_spec_humidity=.False.`, `wvar=''`, and
`relax_filters=.False.`. Sparse target-grid LBC is the lateral boundary path.

## Required interpretation checks

- positive finite mass/interface geometry and adequate top clearance;
- finite P/T/moisture/winds, with upper-level extrema inspected;
- converged wind solve and materially reduced continuity residual;
- no discontinuity at restart joins;
- water/precipitation stores interpreted with cumulative versus interval
  semantics explicitly handled;
- station and REA-L comparisons use identical times/sites and report sampling
  and height-adjustment choices.

Do not equate a stable short run with scientific skill. Extend duration or add
regimes only after the current experiment distinguishes the competing
hypotheses.

## Terrain radiation

Keep it off in the seasonal baseline. The current source had two independent
problems: restart cadence state and a reflected-shortwave component that was
recomputed only on full-radiation steps but removed from total SW between
updates. A source fix now preserves the cached reflected component between
updates, but the feature still needs a focused cadence test and segmented
restart comparison before use in science runs.

## Useful references

Read `references/vertical-grid.md`, `references/wind-solver.md`, and
`references/sensitivity-results.md` only when that topic is under study.
