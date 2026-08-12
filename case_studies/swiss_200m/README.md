# Switzerland 200 m R&D case

The selected baseline is explicit in `config/hicar_swiss_200m.nml.in`:

- 80 levels, nominal 20 m lowest layer, 15 km ASL top, SLEVE 2/6; the selected
  terrain compresses the minimum interface spacing to 17.008 m and the builder
  rejects anything below 12 m;
- native hicarprep P/T/U/V/W/QV/QC/QI forcing in dry-air mixing ratios,
  including terrain-adjusted W on the exact HFL mass levels;
- hourly valid-time REA-L SKT as water-only `SST` in regular forcing;
- literature-established regular full-domain forcing relaxation; sparse LBC is
  retained only for controlled experiments;
- adjoint variational wind, Sx on, density advection, and conservative fixed
  alpha 1. The diagnosed dynamic-alpha coupling produced rejected localized
  high-terrain spikes in this fork/domain and remains an explicit sensitivity;
- Noah-MP with SMI initialization and four depth-varying soil textures;
- ICON SWE, snow depth/density, and bulk snow temperature on cold starts;
- RRTMG every 600 s, with terrain shading and direct/diffuse shortwave
  corrections on; reflected shortwave and terrain longwave are off.

Independent one-hour daylight RRTMG replicas were bit-identical in every
output and terminal-restart variable and completed in under 19 minutes. The
final corrected-SST setup is numerically, not bitwise, restart reproducible;
the small land/PBL perturbation decays over the following hour and is accepted
for the wind-focused seasonal R&D campaign.

The regular atmospheric forcing file keeps mass-grid U/V and W for HICAR's
normal initialization, hourly relaxation and wind-projection path. Each record
is validated before its ready marker is created.

The maintained scripts build HICAR, prepare atmospheric and land input, render
the namelist, run one restartable segment, retrieve SwissMetNet data, and
compare HICAR with stations and REA-L. Experimental alternatives should be
small explicit diffs from this baseline, not additional workflow profiles.

The renderer automatically wires optional `VEGFRA`, `LAI`, `ALBEDO`, and
maximum vegetation-fraction fields found in the runtime domain. Use
`--require-land-climatology` when those inputs are intended to be mandatory.
See `docs/land-surface-initialization-packet.md` for the integration order and
the staged A/B pilot for these cold-start changes.

The two `alpine_bridge_2h_*.json` files are the minimal continuous and 1 h +
1 h restart pilot for Storm Sabine (10 February 2020). They intentionally use
the 701x701 Alpine bridge before any national-domain resource commitment.
