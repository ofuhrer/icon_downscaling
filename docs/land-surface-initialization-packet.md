# Land-surface initialization status

The former integration packet is fully incorporated in HICAR production
`feature/icon_downscaling` (`0b9b0cb6`). Historical packet commits were
coordinator `8de5bf32`, HICAR `7de55a39`, and integrated HICAR `e89b3f0c`; no
cherry-pick or migration procedure remains active.

Retained behavior:

- Decode/remap ICON bulk snow temperature where snow exists, fall back to skin
  temperature capped at freezing when absent, and initialize active Noah-MP
  snow layers through optional `snow_temp_var` without altering restart state.
- Preserve caller-provided glacier SWE/snow depth through the pinned,
  idempotent, fail-closed Noah-MP source patch.
- Range-check and normalize optional VEGFRA, LAI, ALBEDO, and maximum vegetation
  fraction. Preserve monthly VEGFRA; materialize other climatologies at the
  initial valid time.
- `--require-land-climatology` optionally requires VEGFRA and LAI; legacy
  runtime domains remain valid without it.

The project does not synthesize vegetation climatology or elevation-dependent
snow redistribution. Those require sourced inputs and a controlled A/B test.
Any future land-state experiment should hold atmosphere/domain/options fixed
and introduce, in order: glacier snow preservation, bulk snow temperature,
then sourced vegetation climatology, checking first-hour snow/soil state,
surface fluxes, and 2 m temperature by surface class.
