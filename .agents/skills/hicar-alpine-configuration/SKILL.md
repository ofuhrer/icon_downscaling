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
- discretely adjoint variational wind with conservative `alpha_const=1`, two
  passes and a 2500-iteration cap. On the selected national domain, coupling
  this fork's projection to diagnosed dynamic alpha produced localized
  high-terrain 10 m spikes (139.30 m s-1 and 2,101 cells above 30 m s-1 at
  winter initialization); alpha 1 bounded the maximum at 21.085 m s-1 with no
  cells above 30 m s-1. This is the bounded R&D reference, not a claim that
  fixed alpha is the published or scientifically optimal choice;
- bundled Sx/TPI wind modification off. Controlled +24 h restart sensitivities
  show that the former Sx 600 m/30 degree/500 m smoothing plus TPI 4 km/200 m
  setup materially over-damps national speed in both spring and autumn. Turning
  it off improves speed without material vector degradation and retains the
  autumn vector advantage over REA-L. The complete first scored winter 12 h
  segment independently shows a broad but bounded response: across 204,787,800
  core-land samples, Sx/TPI-off is faster in 90.6% of 10 m and 89.9% of 50 m
  samples, by +0.318 and +0.268 m s-1 on average. Every predeclared elevation
  band responds positively and neither case exceeds 30 m s-1. At +24 h it reduces
  full-domain RMSE against interpolated forcing from 1.021 to 0.643 m s-1 at
  10 m and 0.907 to 0.579 m s-1 at 50 m, while remaining finite and bounded.
  This selects the next R&D reference, but forcing agreement is not station
  skill: the fresh four-event station evaluation remains required before
  production use;
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
The selected 12-node RRTMG path is exact across independent one-hour daylight
replicas and terminal restarts and meets the projected segment wall-time bound.
With the final corrected SST products, full-list and streaming segmented runs
are bit-identical. Continuous versus restarted execution is numerically rather
than bitwise reproducible: the land/PBL perturbation decays and the one-hour
10 m vector-wind RMSE is 8.7e-9 m s-1. This is accepted for the wind-focused
R&D campaign and must remain visible in segment diagnostics.

## Required interpretation checks

- positive finite mass/interface geometry and adequate top clearance;
- finite P/T/moisture/winds, with upper-level extrema inspected;
- converged wind solve and materially reduced continuity residual;
- no discontinuity at restart joins;
- the selected bounded R&D configs set `max_wind_speed_ms=30`; each completed
  segment must scan all physical-core 10 m and 50 m winds and fail before its
  completion marker if either exceeds that bound. This is a domain-specific
  rejection gate for the diagnosed-alpha pathology, not a universal Alpine
  wind climatology limit;
- water/precipitation stores interpreted with cumulative versus interval
  semantics explicitly handled;
- station and REA-L comparisons use identical ending-hour intervals/sites;
  aggregate 600 s HICAR output to the SwissMetNet definition, approximate
  REA-L interval means from consecutive hourly endpoints, inverse-rotate HICAR
  grid-relative wind before vector metrics, and report sampling and
  height-adjustment choices;
- an interpolation-only control comparison must use exactly the national
  postprocessor's preregistered four-event/two-metric station cohort. Require
  exact cohort-key equality and independently reconstructed REA-L station RMSE
  parity before interpreting HICAR-versus-control differences.

Do not equate a stable short run with scientific skill. Extend duration or add
regimes only after the current experiment distinguishes the competing
hypotheses.

## Terrain radiation

The reference uses terrain shading and direct/diffuse shortwave corrections
after HLM/SVF/slope/aspect are generated from the exact static terrain and
validated. Reflected shortwave and terrain longwave remain off. The daylight
continuous-versus-segmented proof must exercise repeated radiation calls,
remain finite, preserve exact segment-list equivalence, and quantify any
uninterrupted-versus-restart perturbation rather than assuming bitwise identity.

## Useful references

Read `references/vertical-grid.md`, `references/wind-solver.md`, and
`references/sensitivity-results.md` only when that topic is under study.
