---
name: hicar-alpine-configuration
description: Choose and validate HICAR namelist, vertical grid, terrain coordinates, wind solver, and physics for Alpine downscaling.
---

# HICAR Alpine configuration

Define an internally coherent untuned reference from published HICAR practice
before tuning individual mechanisms. Inspect physical fields and observations,
not just model termination. Use bounded A/B tests only after the reference
result identifies a specific scientific ambiguity.

## Selected Swiss 200 m baseline

- 80 levels, 15 km ASL model top, 20 m lowest layer, stretch 0.65, with a 12 m
  hard minimum after terrain compression and no value below 10 m;
- SLEVE decay 2/6, terrain smoothing radius 5 for 10 cycles;
- RK3/CFL 1.6, third-order horizontal and vertical advection, FCT option 1,
  and density advection;
- discretely adjoint variational wind, dynamic `alpha_const=-1` bounded by
  0.1--1, two passes and a 2500-iteration cap;
- Sx on with 600 m search, 30 degree scale and 500 m smoothing; TPI search
  4 km and scale 200;
- Morrison microphysics, YSU PBL, Noah-MP surface-exchange option 1 with the
  revised-MM5 atmospheric surface layer, prescribed simple-water SST, and RRTMG
  every 600 s, with YSU top-down radiative mixing explicitly off;
- four-layer depth-varying soil texture with SMI land initialization;
- terrain shading and direct/diffuse shortwave corrections on, using validated
  HLM/SVF/slope/aspect geometry; reflected shortwave and terrain longwave off.

Atmospheric forcing is hicarprep P/T/U/V/W/QV/QC/QI plus SST in dry-air mixing
ratios. W is supplied on the authoritative target HFL levels; its terrain
correction uses grid-relative U/V even though serialized U/V stay
earth-relative. SST is hourly REA-L skin temperature over water support. Set
`qv_is_spec_humidity=.False.`,
`wvar='W'`, `sst_var='SST'`, and `relax_filters=.True.`. Use the regular
full-domain forcing path for the selected reference. Sparse target-grid LBC is
an optional experiment only; when used it contains mass-grid T/P/QV/QC/QI and
must not insert sparse U/V/W after wind projection.
The RRTMG candidate must pass exact A/A and segmented-restart checks and meet
the segment wall-time bound before a seasonal launch.

## Required interpretation checks

- positive finite mass/interface geometry and adequate top clearance;
- finite P/T/moisture/winds, with upper-level extrema inspected;
- converged wind solve and materially reduced continuity residual;
- no discontinuity at restart joins;
- water/precipitation stores interpreted with cumulative versus interval
  semantics explicitly handled;
- station and REA-L comparisons use identical ending-hour intervals/sites;
  aggregate 600 s HICAR output to the SwissMetNet definition, approximate
  REA-L interval means from consecutive hourly endpoints, inverse-rotate HICAR
  grid-relative wind before vector metrics, and report sampling and
  height-adjustment choices.

Do not equate a stable short run with scientific skill. Extend duration or add
regimes only after the current experiment distinguishes the competing
hypotheses.

## Terrain radiation

The reference uses terrain shading and direct/diffuse shortwave corrections
after HLM/SVF/slope/aspect are generated from the exact static terrain and
validated. Reflected shortwave and terrain longwave remain off. Before seasonal
launch, a daylight continuous-versus-segmented proof must exercise repeated
radiation calls and show exact restart/output equivalence.

## Useful references

Read `references/vertical-grid.md`, `references/wind-solver.md`, and
`references/sensitivity-results.md` only when that topic is under study.
