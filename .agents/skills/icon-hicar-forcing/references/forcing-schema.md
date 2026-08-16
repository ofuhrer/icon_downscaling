# Forcing schema and verified sources

## Required output schema

Use these normalized fields:

| Field | Dimensions | Meaning |
|---|---|---|
| `P` | `time,z,y,x` | pressure |
| `QV` | `time,z,y,x` | water-vapor mixing ratio/specific humidity as configured |
| `T` | `time,z,y,x` | temperature |
| `U`, `V` | `time,z,y,x` | horizontal earth-relative wind |
| `W` | `time,z_hl,y,x` | optional but preferred half-level vertical velocity |
| `HFL` | `z,y,x` | full-level height |
| `HHL` | `z_hl,y,x` | half-level height |
| `HSURF` | `y,x` | driving-grid terrain |
| `FR_LAND` | `y,x` | land fraction |
| latitude, longitude | 1-D or 2-D consistent with grid | geographic coordinates |

HICAR minimally needs 3-D `U,V,P,T,QV`. Retain `W` and geometry for ICON-driven Alpine work.

## Verified ICON file families

- `lfff????????`: hourly full-column dynamics; verified with `U,V,T,P,QV` on 80 levels.
- `lffm????????`: ten-minute full-column dynamics; also contains `W` on 81 half levels and surface fields.
- `lfff00000000c`: static geometry including `HHL` levels 1-81, `HSURF`, and `FR_LAND`.
- Plain `i1eff..._000k` archive products may contain only a few low model levels. Inspect before use; do not assume full-column coverage.

The former scratch sample and frozen forcing case were cleanup artifacts, not
durable references, and have been removed. Recreate inputs from the archive and
record their provenance in the case manifest.

## Ready-file behavior

- The forcing list must exist before HICAR starts and may contain future paths.
- With `wait_for_ready_file=.True.`, HICAR waits for `<forcing-file>.ready`.
- Set a finite `ready_file_timeout` appropriate to the producer cadence.
- Publish by writing to a temporary path on the same filesystem, validating, renaming atomically, and finally creating `.ready`.
