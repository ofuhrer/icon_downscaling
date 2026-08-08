# ICON-to-HICAR land-surface initialization packet

This backward-compatible improvement to HICAR cold-start land initialization
was integrated on coordinator commit
`8de5bf32fb41b7dee60944739fb80b0911c84fca` and the selected HICAR simulation
baseline `5fc3c71b91b368ec0a71831d86359eb51e68d8d9`. The pinned Noah-MP dependency is
`8b9892a8a4137ad89eca803c1abf45969e5e686e`.

The integrated HICAR land-state commit is
`e89b3f0c2fd18012042569841015ebeafbb21edb` on
`feature/icon_downscaling`. It follows explicit revert commit
`e0fde9f0f1014de0fc58af3475024bac4ffcdacf`, which keeps the rejected
restart-wind intervention out of the simulation baseline. The original packet
commit remains `7de55a3992ec7122ea29cebbe7d69b6172fd3032`.

## What changes

1. ICON bulk snow temperature (`T_SNOW`, parameter 500170) is requested,
   validated only where `W_SNOW` is positive, remapped on snow support, bounded
   against the remapped skin temperature, and written as
   `snow_temperature_initial`. If `T_SNOW` is absent from a generic input,
   preprocessing falls back to skin temperature capped at freezing.
2. HICAR accepts the optional `snow_temp_var` domain setting and uses the bulk
   value for each active Noah-MP snow layer during a cold start. Restarts retain
   their prognostic snow-layer temperatures.
3. The pinned Noah-MP HICAR driver no longer zeroes caller-provided SWE and snow
   depth on glacier cells. The exact-source CMake patch is idempotent and fails
   closed if the pinned dependency changes.
4. Optional `VEGFRA`, `LAI`, `ALBEDO`, and `vegetation_fraction_max` fields are
   range checked, normalized to HICAR units, and wired into the generated
   namelist. Twelve-month `VEGFRA` is preserved and enables
   `monthly_vegfrac`; the other climatologies are materialized at the initial
   valid time because HICAR only has native monthly handling for vegetation
   fraction.
5. `render_hicar_namelist.py --require-land-climatology` provides an opt-in
   production guard requiring both `VEGFRA` and `LAI`. Without that option, old
   runtime-domain files remain valid and HICAR's existing defaults remain in
   effect.

The packet deliberately does not synthesize a vegetation climatology or apply
an elevation-dependent snow redistribution. Those require a chosen source and
a controlled scientific experiment rather than a silent preprocessing default.

## Integration order

Integrate the HICAR commit first, then the coordinator commit that advances the
HICAR submodule pointer. Do not cherry-pick the coordinator commit without its
HICAR prerequisite.

If either target branch has advanced, cherry-pick the HICAR commit first and
record the resulting HICAR commit ID. Apply the coordinator commit with
`git cherry-pick --no-commit`, set the `HICAR` submodule to that recorded ID,
stage the submodule pointer, rerun the checks below, and then commit. This avoids
retaining the packet's old gitlink when the HICAR cherry-pick receives a new ID.

After integration, rebuild HICAR from a fresh build directory so FetchContent
applies the pinned Noah-MP patch. Existing populated dependency trees should not
be reused for the first verification build.

## Verification performed in the packet

```text
PYTHONPATH="$PWD" pytest -q
89 passed

cmake -DNOAHMP_SOURCE_DIR=<exact pinned checkout> \
  -P HICAR/cmake/patch_noahmp_glacier_snow.cmake
# applied successfully; a second invocation reported already applied

# A fresh local CPU/debug configure and full HICAR target build succeeded.
# GNU Fortran 16 required -fno-range-check for a pre-existing SNOWPACK
# 1.0e200_dp constant; the packet's changed Fortran sources compiled cleanly.
```

`git diff --check` passes in both repositories. No Balfrin job or simulation was
started while preparing the packet.

## Smallest useful follow-up experiment

Use one existing two-hour Swiss 200 m winter case and keep atmospheric forcing,
static domain, executable options, and start time fixed:

- A: current integrated baseline.
- B: preserve SWE over glaciers, without enabling `snow_temp_var`.
- C: B plus ICON `T_SNOW`.
- D: C plus sourced `VEGFRA` and `LAI` climatologies.

Compare the initialized and first-hour SWE, snow depth, snow-layer temperature,
skin temperature, sensible/latent heat flux, and 2 m temperature over glacier,
snow-covered non-glacier, and snow-free land masks. B isolates the confirmed
glacier reset defect; C isolates snow thermal initialization; D measures the
climatology effect. Promote each step only if it remains finite and materially
reduces the relevant initialization discontinuity without degrading the other
surface classes.
