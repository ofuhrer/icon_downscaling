---
name: hicar-alpine-configuration
description: Choose and validate HICAR namelist, vertical grid, terrain coordinates, wind solver, and Alpine experiment design.
---

# HICAR Alpine configuration

The authoritative national specification is
`case_studies/swiss_200m/REFERENCE_SETUP.md`; sensitivity evidence is in
`references/sensitivity-results.md`.

## Evaluated 200 m configuration

- 2061 x 1431 AEQD grid, 40 km external margin, GLO-30 surface terrain with a
  30 km blend to REA-L at the boundary.
- 80 SLEVE levels to 15 km, nominal 20 m lowest layer, stretch 0.65; split
  smoothing 5 cells/10 cycles, decay 2/6, exponent 1.35.
- Require positive Jacobians and >=12 m interface spacing everywhere; <10 m is
  forbidden. Selected minima are 0.3375 and 17.008 m.
- RK3, CFL 1.6, third-order advection, FCT 1, density transport.
- Adjoint projection with `alpha_const=1`, two passes, 2500-iteration cap,
  hourly update; bundled Sx/TPI off; thermal/linear-theory corrections off.
- Morrison, YSU, Noah-MP/revised-MM5, prescribed water temperature, terrain-
  corrected 600 s radiation, no cumulus.

With dynamic alpha, an otherwise fixed winter initialization reached 139.30
m s-1 at 10 m, versus 21.085 m s-1 for alpha 1. The campaign used alpha 1.
Its 30 m s-1 validation bound is an explicit four-event gate, not a universal
physical limit. The Sx-off comparison was event-dependent.

## Trust checks

- Verify exact static/forcing HHL/HFL, map factors, positive layer thickness,
  minimum spacing/Jacobian, and stable pressure/temperature interpolation.
- Check solver residuals/conservation, wind extrema, CFL-limiting W, upper-
  level temperature/theta, and 6--24 h stability after configuration changes.
- Separate coordinate geometry (`w_grid`) from physical W; the reference
  qualifies horizontal surface wind only.
- Keep water/lake, cold-cloud/precipitation, glacier history, and slow land
  state limitations explicit.
- Run one-factor A/B tests. Do not combine grid, solver, physics, and boundary
  changes or infer climatological skill from four events.

Terrain shortwave needs HLM, SVF, slope, and aspect from the exact runtime
terrain. Enable shading plus direct/diffuse correction; reflected shortwave and
terrain longwave remain off unless separately qualified.
