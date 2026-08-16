# Swiss 200 m scientific reference setup

This file records the untuned configuration evaluated in two four-season,
identically sampled campaigns. Quantitative results are in
`memory/project-assessment.md`.

Published HICAR work supports Alpine SLEVE/variational-wind physics, but not a
national all-season 100--200 m wind climatology: Reynolds et al. (2023) studied
one January at 250 m; Reynolds et al. (2024) documented a 1 km October--May
parent and a short 50 m Davos nest; Berg et al. (2024) used HICAR-derived
forcing for one snow season without establishing one continuous national
atmospheric run. See DOI
[`10.5194/gmd-16-5049-2023`](https://doi.org/10.5194/gmd-16-5049-2023),
[`10.3389/feart.2024.1388416`](https://doi.org/10.3389/feart.2024.1388416), and
[`10.3389/feart.2024.1393260`](https://doi.org/10.3389/feart.2024.1393260).

## Domain and coordinates

- AEQD 200 m, 2061 x 1431 cells (412 x 286 km), centre 46.815 N/8.225 E,
  40 km external margin.
- Area-aggregated Copernicus GLO-30 surface; no physical-surface smoothing.
  Blend to REA-L `HSURF` over the outer 30 km.
- WorldCover-to-USGS land use, land/water mask, and four SoilGrids texture
  classes. Keep invariant static fields separate from the 2021 land-use epoch;
  glaciers, sub-grid lakes, and temporal back-extrapolation are uncertainties.
- 80 SLEVE levels, 15 km ASL top, nominal 20 m first layer, stretch 0.65,
  smoothing radius/cycles 5/10, decay 2/6, exponent 1.35. Use AGL interpolation
  below an 800 m transition and ASL above.
- Require positive Jacobians and >=12 m spacing everywhere (<10 m forbidden).
  Selected minima are 0.3375 and 17.008 m.

## Meteorological input

- Decode hourly native REA-L pressure, temperature, QV/QC/QI when available,
  U/V/W, interface height, surface height, and skin/water temperature through
  maintained `hicarprep`; fieldextra is retired.
- Use bounded/conditioned local RBF remapping, positive source-layer thickness,
  and exact target HHL/HFL. Preserve supersaturation. Set missing QI to zero;
  do not invent QR/QS/QG.
- Regular forcing contains P/T/dry QV/QC/QI, earth-relative U/V, target-HFL W,
  and valid-time SST. Rotate U/V into the HICAR basis only for terrain-W and
  model staggering; serialized U/V remain earth-relative.
- Use regular full-domain relaxation with `relax_filters=true`. Sparse LBC is
  experimental, scalar-only, and not the reference.
- On water, use compact same-surface remapping where supported and otherwise
  the exact-valid-time monotone local all-surface RBF baseline. Reject the old
  remote nearest-water fallback. HICAR's 273.15 K floor means frozen-lake
  physics is absent.

## Dynamics and physics

- RK3, CFL 1.6, third-order horizontal/vertical advection, FCT 1, density-
  weighted transport, no extra constant-z diffusion, and no cumulus.
- Fork adjoint mass-conserving projection with `alpha_const=1`, two passes,
  2500-iteration cap, hourly update; Sx/TPI off; thermal and linear-theory
  corrections off. Dynamic alpha produced 139.30 m s-1 winter initialization
  winds versus 21.085 m s-1 for alpha 1 and is rejected for this reference.
- Morrison, YSU (top-down mixing off), Noah-MP with revised-MM5 atmospheric
  surface layer and supported exchange option 1, prescribed simple-water SST,
  and Noah-MP internal snow.
- Noah-MP uses USGS/four soil layers, 300 s updates, `dveg=3`, `alb=2`, and the
  integrated option vector in the namelist. External LAI/VEGFRA/static albedo
  are not reference inputs; frozen phase, glacier history, and slow state are
  approximate.
- The completed campaign used RRTMG every 600 s, UTC, cloud-fraction option 3,
  maximum-random overlap, terrain shading, and direct/diffuse shortwave
  correction; reflected shortwave and terrain longwave were off.
- New experiments may use qualified GPU RTE-RRTMGP v1.9.3 from production
  `0b9b0cb6`. It is bitwise reproducible on the one-node and 12-node restart
  gates but defines a new trajectory relative to the completed RRTMG campaign.

## Experiment and verification contract

The completed design used four independent 48 h seasonal trajectories split
into 12 h segments; the first 24 h were spin-up and the second 24 h scored.
The immutable campaign executable was `4a425677`; only its missing 20 m restart
provenance may use the explicit compatibility exception. Production `0b9b0cb6`
must pass the full provenance check.

- Require exact output times, expected physics/forcing turnover, successful
  termination, terminal restart, finite fields, residuals, and continuity.
- Write essential surface/station fields every 600 s. Aggregate six ending
  ten-minute samples to each SwissMetNet civil-hour mean and inverse-rotate
  model wind. Use consecutive REA-L endpoints for its interval mean; preserve
  snow endpoint and precipitation-interval semantics.
- Compare identical usable station/time pairs. Sample terrestrial stations at
  the nearest land cell, report overrides/displacement, and reject >1 km.
- Wind speed/vector RMSE are co-primary; bias, MAE, variability ratio, and
  correlation are diagnostic. Four events support mechanism diagnosis, not
  climatological ranking.
- The physical-core 30 m s-1 wind bound is specific to these events. Claims are
  limited to horizontal surface wind; terrain W, cloud, precipitation, lake
  thermodynamics, and land-state equilibration remain unqualified.
