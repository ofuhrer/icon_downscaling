# Alpine sensitivity evidence

## Upper-level vertical velocity

In the frozen 250 m case, `wind='none'` produced maximum `|w|` around `40.4 m s-1`, controlled by the highest output level in every horizontal cell. The variational solver removed the top-level control; a representative `Sx` run reduced maximum `|w|` to about `6.25 m s-1` with p99 around `0.53 m s-1`.

A 12 km lid suppressed actual top-level `w/w_grid`, but the largest remaining values occurred around 4-5.5 km AGL over high terrain. Therefore diagnose mid-level terrain response rather than declaring success from the top level alone.

## Vertical-grid/SLEVE sweep

The tested preferred case was `v2_auto1_n80_top12_s26`:

- `auto_level=1`, `nz=80`, top 12 km;
- first level 15 m, stretch 0.65;
- HICAR large-scale terrain smoothing window 5, 10 cycles;
- SLEVE large/small decay `2/6`;
- variational solver, `Sx=.True.`, smoothing distance 500 m.

The cheaper fallback was `v1_auto1_n60_top12_s26`.

On the 2271 x 1651 Switzerland 200 m domain, the former 100-cycle terrain
split produced an actually inverted coordinate despite a positive analytic
`gamma`: minimum mass Jacobian `-0.0965`, minimum adjacent mass-level spacing
`-8.34 m`, and grid-relative vertical wind near `-4914..3347 m s-1`. The
window-5/10-cycle split preserves the same external DEM and SLEVE decay but
gives minimum mass Jacobian `0.17194`, minimum interface thickness `12.257 m`,
and minimum output mass-level spacing `12.936 m`.

With that split, the four-node national adjoint/Galerkin run completed 30
minutes. Its strict initial and first-timestep wind residuals were at most
`9.337e-6`, conservation ratios at most `1.249e-5`, and the two-record output
was finite with `w=-11.04..9.15 m s-1` and
`w_grid=-23.19..16.01 m s-1`.

Order-8 scale-selective external DEM filtering changed the summit by only
about 9-13 m and left the invalid old terrain split near
`minimum_mass_jacobian=-0.09`; it also worsened the legacy nonnormal solver
diagnostics. External filtering therefore remains a separately justified
physical/numerical sensitivity, not the cure for either coordinate
invertibility or global solver conditioning.

## Follow-up order

The selected national winter initialization isolated a fork/domain-specific
dynamic-alpha failure. The 2020-01-14 00 UTC A/B test held the 4a425677
executable, domain, 13 forcing records, Sx settings and 12-node topology fixed.
Diagnosed alpha produced a 139.30 m s-1 maximum 10 m wind, p99.9 of
26.42 m s-1, and 2,101/476/30 cells above 30/50/100 m s-1; its 50 m maximum
was 34.60 m s-1. `alpha_const=1` produced a 21.085 m s-1 maximum and
11.212 m s-1 p99.9 at 10 m and a 21.262 m s-1 maximum at 50 m, with no cells
above 30 m s-1. Both solver and conservation checks passed. Evidence is under
`winter_projection_ab_4a425677/adjoint_alpha1` (model hash prefix `31a27113`,
comparison-JSON hash prefix `cca0f797`).

Use alpha 1 for the conservative R&D reference. Revisit dynamic alpha only as
a controlled mechanism experiment after the campaign; this result does not
establish fixed alpha as the published or optimal choice.

After the vertical grid, lid, and SLEVE decay are stable, investigate:

1. `wind_iterations` and, only if needed, a bounded dynamic-alpha alternative;
2. `cz_diff_order` and other numerical diffusion controls;
3. `advect_density` and wind balancing;
4. `use_agl_height` / `agl_cap` where relevant;
5. boundary width and forcing temporal/horizontal interpolation.

Do not carry old experimental `decay_rate_L_topo=1` and `decay_rate_S_topo=1` into a production baseline.

## RK3 thermodynamic caution

The current RK3/theta-reference hardening eliminated NaNs in the one-hour smoke case, and the accepted hybrid limiter avoided temperatures below 190 K there. Earlier unbounded flux-form variants produced severe local upper-level cold/theta outliers despite finite domain means. Treat the current formulation as smoke-tested rather than production-proven: retain upper-level temperature/potential-temperature extrema and longer 6-24 h stability checks in every new configuration campaign.
