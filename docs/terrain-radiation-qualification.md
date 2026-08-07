# HICAR terrain-radiation qualification

This implementation is a non-production causal candidate for topographic
radiation at Alpine resolution. The national HICAR configuration keeps terrain
radiation off by default.

## Implementation

HICAR now preserves the unmodified horizontal-plane direct and diffuse
shortwave components produced by RRTMG(P). Terrain corrections derive from
those fields independently, avoiding the former causal defect in which an
already sky-view-factor-reduced diffuse field was subtracted from total
shortwave and reclassified as direct radiation.

Four controls can be enabled independently under the existing
`terrain_shading` master switch:

- `terrain_direct_sw`: horizon visibility and slope/aspect projection;
- `terrain_diffuse_sw`: isotropic diffuse reduction by sky-view factor;
- `terrain_reflected_sw`: terrain-reflected shortwave;
- `terrain_longwave`: sky/terrain-view partition of downward longwave.

The renderer exposes cumulative profiles `off`, `direct`, `direct-diffuse`,
`full-local`, and `full-neighborhood`. Any enabled profile requires an audited
static file containing 90 horizon sectors at 4-degree spacing, HLM as zenith
angle to the horizon (flat/open is 90 degrees), SVF in `[0,1]`, slope and
aspect in radians, and provenance for the DEM, vertical datum, generator, and
horizon-search distance.

## Qualification evidence

The staged HICAR file contents are byte-identical to qualification source
commit `43c636be24fb920cafebac17ab0ce0bab7f8343c`. That source produced GPU-MPI
executable SHA-256
`d10362a7946ddec4fe549bcad55a291c29c37863fed1688fe86d9a13a7e50bca`.
The equivalent implementation is committed locally in HICAR as `274f1c08`.

The seven-case synthetic gate established:

- flat direct radiation identity within `6.103515625e-05 W m-2`;
- bitwise identity of diffuse radiation when its correction is disabled;
- the expected direct-beam shadow/release transition for a single blocked
  horizon sector;
- the analytic diffuse response to SVF; and
- preserved irradiance units for reflected shortwave.

The same experiment failed restart continuity for ten saved variables. The
largest first-post-restart discrepancies include about `53.356 W m-2` latent
heat flux, `46.074 W m-2` sensible heat flux, and `0.8736 K` surface
temperature. The differences decay but remain outside the declared tolerance.

The resulting decision is therefore
`TERRAIN_COMPONENT_PASS_RESTART_GATE_FAIL`. This authorizes neither a valley
experiment nor production use. Until restart equivalence is fixed, any later
causal experiment must keep preconditioning and scoring in one uninterrupted
run.

The durable complete artifact is
`/store_new/mch/msopr/olifu/icon_downscaling/qualification/terrain_radiation_model_gate_v4`.
The committed concise evidence includes the assessment and run manifest under
`case_studies/swiss_200m/fixed_parameters/validation/`.

## Remaining limitations

- Diffuse shortwave assumes an isotropic sky and uses scalar SVF.
- Runtime horizon selection uses one 4-degree sector without azimuthal
  interpolation.
- Reflected shortwave and terrain-emitted longwave use simplified view-factor
  treatments.
- A finite horizon-search distance can miss distant Alpine obstruction.
- All geometry fields must derive from the same terrain realization and
  vertical-datum convention.
- Bilinear regridding of horizons does not preserve ray geometry.
