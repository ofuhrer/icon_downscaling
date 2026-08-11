---
name: icon-balfrin-grib
description: Find and inspect MeteoSwiss ICON-CH1-EPS or ICON REA-L-CH1 source data on Balfrin, decode local GRIB parameters, identify full-column dynamic/static file families, and select inputs for ICON-to-HICAR downscaling. Use for archive paths, FDB access, ecCodes setup, file naming, variables, levels, timestamps, or GRIB metadata validation.
---

# ICON source data on Balfrin

Use `$balfrin-user-environment` first. Perform only light discovery on login nodes; submit scans and extraction to the correct Slurm partition.

## Choose a source

1. Prefer ICON REA-L-CH1 through its documented FDB view when access is available. It is the target deterministic 1 km reanalysis-light dataset for this project.
2. If REA-L-CH1 access is blocked, use an ICON-CH1-EPS control forecast as a workflow-development fallback. Do not mistake it for the final reanalysis source.
3. For HICAR, require full atmospheric columns plus static geometry. Inspect actual levels before copying data.

## Current paths

Historical ICON archive root:

```text
/store_new/mch/msopr/osm/ICON-CH1-EPS/FCST<YY>
```

Run layout:

```text
FCST<YY>/<YYMMDDHH>_<archive-version>/grib/<file>
```

Verified workflow-development sample:

```text
/store_new/mch/msopr/osm/ICON-CH1-EPS/FCST26/26071018_639/grib
$SCRATCH/icon_hicar/data/icon_ch1_eps_20260710T18_ctrl_oper_icon_000
```

## File-family selection

- `lfff????????`: hourly full-column output; verified `U,V,T,P,QV` on 80 levels.
- `lffm????????`: ten-minute full-column output; verified `U,V,T,P,QV`, `W` on 81 half levels, and surface fields.
- `lfff00000000c`: static `HHL`, `HSURF`, and `FR_LAND`.
- `i1eff..._000`: mainly surface/single-level fields.
- `i1eff..._000k`: may contain only selected low levels. Never assume it is a full column.

Use `scripts/balfrin_prepare_icon_ch1_eps_sample.sh` for the known fallback sample.

## ecCodes setup

```bash
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"
. ~osm/.opr_setup_dir
module use "$OPR_SETUP_DIR/modules"
module load eccodes/2.36.4-gcc
module load modulefiles/eccodes_cosmo_resources/2.36.0.3
export GRIB_DEFINITION_PATH="$ECCODES_DEFINITION_PATH"
```

Source `~osm/.opr_setup_dir` before enabling `set -u`, or temporarily disable nounset. Correct MeteoSwiss resources decode names such as `T_2M`, `TOT_PREC`, `ASWDIR_S`, and `ASWDIFD_S`.

Inspect one file before any broad operation:

```bash
grib_ls -p shortName,paramId,name,units,typeOfLevel,level,stepType,stepRange,\
dataDate,dataTime,validityDate,validityTime,numberOfPoints "$GRIB"
```

Validate message counts, vertical-level coverage, reference/valid times, grid point count, member, and units.

## REA-L-CH1 FDB

- Use the `rea-l-ch1` FDB uenv/view and `scripts/balfrin_rea_l_ch1_fdb_smoke.sh`.
- Known FDB uenvs include `fdb/5.18:v1`, `fdb/5.18:v3`, and
  `fdb/5.19:v2`; `fdb/5.18:v3` is verified for the ten-minute surface-wind
  probe.
- REA-L access is available to `olifu`; still use a narrow probe before a
  production extraction. Atmospheric forcing records used by this project
  are hourly cycle-plus-step data, while selected surface diagnostics are
  also available at ten-minute steps. Always use cycle time plus `step` as
  the valid timestamp, not a daily timestamp.
- Retrieve geometry (`HSURF`, `FR_LAND`, `HHL`) at step 0. Retrieve the
  requested atmospheric full levels, `W`, and `SKT=502336` at the requested
  step. `HSURF` and `SKT` use `type=cf,levtype=sfc`; `HHL` requires an explicit
  `levelist`. The forcing-time `SKT` is distinct from the step-0 land cold-start
  state even though it uses the same REA-L variable.
- Keep MARS/FDB requests narrow by date/time/parameter/level and validate a one-record retrieval before scaling.

### Verified REA-L surface fields

- Surface pressure `PS=500000`, 2 m temperature `T_2M=500011`, 2 m dew
  point `TD_2M=500017`, 10 m winds `U_10M=500027` and `V_10M=500029`,
  total precipitation `TOT_PREC=500041`, snow water equivalent
  `W_SNOW=500044`, snow height `H_SNOW=500045`, snow density
  `RHO_SNOW=500147`, source terrain `HSURF=500007`, and skin temperature
  `SKT=502336`.
- Do not confuse `W_SNOW=500044` with `SKT=502336`; verify both `paramId`
  and `shortName` in a narrow inventory when adding a surface product.
- `TOT_PREC` is cumulative from the 00 UTC cycle. Form an interval from two
  endpoints in the same cycle; across 00 UTC use steps 21 and 24 of the
  previous cycle for the preceding three-hour interval. NetCDF float
  quantization can create isolated negative differences of order
  `1e-3 kg m-2`; record and clip them only within a documented
  `0.01 kg m-2` envelope, and fail on a larger decrease.

### Ten-minute surface wind and gust diagnostics

- REA-L exposes `U_10M` (`param=500027`) and `V_10M`
  (`param=500029`) as instantaneous 10 m wind components, and `VMAX_10M`
  (`param=500164`) as a scalar 10 m gust maximum.
- These fields are available at ten-minute steps such as `step=10m`,
  `20m`, ..., `50m`, and hourly steps. Validate the actual GRIB interval:
  at `step=10m`, `VMAX_10M` has `stepType=max` and `stepRange=0m-10m`;
  at `step=1`, it has `stepRange=50m-60m`. The hourly record is therefore
  only the final ten-minute maximum, not the maximum over the full hour.
- Retrieve all six ten-minute `VMAX_10M` records when constructing an hourly
  gust maximum. Keep the instantaneous component fields and interval-maximum
  gust semantics distinct in manifests and derived products.
- Do not use `VMAX_10M` as a HICAR wind component or dynamical forcing. It
  has no direction and represents a time maximum. Preserve it as a sidecar
  diagnostic for gust validation, calibration, or statistical downscaling.

## Safety and reproducibility

- Avoid broad `find` over `/store_new`; probe specific years and cycles.
- Never write indexes, temporary GRIBs, or outputs into `/store_new`.
- Put copied source and reduced products under `$SCRATCH/icon_hicar/data`.
- Make large copy/extraction jobs restartable with manifests and atomic output publication.
- Record source path, cycle, member, lead/valid time, grid, fields, levels, and checksum in the case manifest.

After selecting files, use `$icon-hicar-forcing` for structured regridding and packaging.
