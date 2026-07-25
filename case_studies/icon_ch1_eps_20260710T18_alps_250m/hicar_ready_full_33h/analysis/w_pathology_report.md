# HICAR `w` Pathology Diagnostics

## Main Findings

- Output `w` has shape `(32, 40, 81, 81)` for `(time, level, y, x)`.
- The cell-wise max over all output records and levels comes from level `39` for `6561/6561` horizontal cells.
- Global max `|w|` is `40.431 m s-1` at record `23` (`2026-07-11 17:00`), level `39`, y/x `44/40`.
- This means the visually suspicious max-`|w|` image is effectively a top-level diagnostic, not a vertically mixed map of terrain-following vertical motion.
- Prepared ICON forcing `W` is small near the HICAR top height during the strongest HICAR-output hours, so the largest values are not a direct passthrough of top-boundary ICON `W`.
- The high values are not primarily outer-boundary artifacts and are only weakly associated with the expected 2x2 processor split lines.

## Level And Time Concentration

| Level | max | p99 | p99.9 | mean |
|---:|---:|---:|---:|---:|
| 0 | 2.653 | 1.394 | 1.899 | 0.350 |
| 1 | 4.313 | 1.436 | 2.448 | 0.302 |
| 10 | 6.733 | 1.812 | 3.052 | 0.435 |
| 20 | 9.168 | 2.942 | 4.721 | 0.729 |
| 30 | 16.420 | 6.054 | 9.506 | 1.480 |
| 35 | 29.181 | 9.295 | 14.878 | 2.225 |
| 36 | 31.284 | 10.183 | 16.287 | 2.432 |
| 37 | 33.286 | 11.133 | 17.951 | 2.659 |
| 38 | 37.128 | 12.233 | 19.807 | 2.909 |
| 39 | 40.431 | 13.159 | 21.300 | 3.116 |

Strongest top-level hourly maxima:
- record `23` (`2026-07-11 17:00`): `40.431 m s-1`
- record `20` (`2026-07-11 14:00`): `38.757 m s-1`
- record `24` (`2026-07-11 18:00`): `33.434 m s-1`
- record `1` (`2026-07-10 19:00`): `27.285 m s-1`
- record `25` (`2026-07-11 19:00`): `27.161 m s-1`
- record `22` (`2026-07-11 16:00`): `25.856 m s-1`
- record `4` (`2026-07-10 22:00`): `25.780 m s-1`
- record `2` (`2026-07-10 20:00`): `25.008 m s-1`

Most frequent argmax records for the cell-wise all-level maximum:
- record `20` (`2026-07-11 14:00`): `920` cells
- record `0` (`2026-07-10 18:00`): `446` cells
- record `4` (`2026-07-10 22:00`): `414` cells
- record `24` (`2026-07-11 18:00`): `404` cells
- record `23` (`2026-07-11 17:00`): `380` cells
- record `1` (`2026-07-10 19:00`): `322` cells
- record `19` (`2026-07-11 13:00`): `308` cells
- record `7` (`2026-07-11 01:00`): `280` cells

## Spatial Association Checks

| threshold for top-level max `|w|` | cells | outermost edge share | 3-cell midline share |
|---:|---:|---:|---:|
| 10 m s-1 | 1985 | 7.30% | 9.12% |
| 15 m s-1 | 583 | 6.35% | 9.95% |
| 20 m s-1 | 170 | 12.94% | 11.76% |
| 25 m s-1 | 58 | 6.90% | 10.34% |
| 30 m s-1 | 16 | 6.25% | 6.25% |

## Prepared ICON Forcing Comparison

- Mean HICAR top output height is `19642.0 m`; closest forcing half level is `79` with mean height `19808.4 m`.
| forcing hour | closest-level max `|W|` | closest-level p99 `|W|` | maximum over all forcing levels |
|---:|---:|---:|---:|
| 0 | 0.0132946 | 0.0131628 | 1.12704 |
| 20 | 0.00953685 | 0.00951145 | 7.46606 |
| 23 | 0.00235481 | 0.00229892 | 1.52884 |
| 24 | 0.00638185 | 0.00633619 | 2.48288 |

## Interpretation

The suspect map should not be interpreted as evidence for widespread near-surface vertical velocities of 20-40 m s-1. It is dominated everywhere by the highest HICAR output level. Since ICON forcing `W` is near zero at comparable heights, the most likely causes are HICAR's diagnostic vertical-wind reconstruction or upper-boundary/vertical-coordinate behavior. The next model-side test should output both `w` and `w_grid`, and should run a sensitivity with a higher model top or stronger upper damping/top-level treatment if such namelist controls are available.

Generated figures:
- `w_pathology_top_level_maps.png`
- `w_pathology_vertical_time_and_forcing.png`
