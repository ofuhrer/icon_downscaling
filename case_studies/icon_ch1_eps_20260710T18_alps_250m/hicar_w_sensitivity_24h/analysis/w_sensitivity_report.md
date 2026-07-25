# HICAR `w` Sensitivity Result Analysis

Run window: 25 hourly records from 2026-07-10 18 UTC through 2026-07-11 18 UTC.

## Core Diagnostics

| case | nt | z_top_median_m | n_median_levels_below_200m_agl | w_max | w_p99 | w_argmax_dominant_level | w_argmax_dominant_cell_fraction | w_grid_max | w_grid_p99 | upper5_w_max | upper5_w_grid_max | top_density_mean_kgm3 | min_temperature_K |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| none_20km | 25 | 19642.7 | 1 | 40.43 | 6.934 | 39 | 1.000 | 41.97 | 7.705 | 40.43 | 41.97 | 0.100 | 207.0 |
| var_no_sx_20km | 25 | 19642.7 | 1 | 6.261 | 0.561 | 0 | 0.268 | 6.850 | 2.473 | 1.462 | 1.591 | 0.100 | 211.8 |
| var_sx_20km | 25 | 19642.7 | 1 | 6.245 | 0.532 | 1 | 0.248 | 6.836 | 2.441 | 1.518 | 1.652 | 0.100 | 211.8 |
| var_sx_top12km | 25 | 11798.9 | 1 | 6.344 | 0.643 | 1 | 0.217 | 7.533 | 2.160 | 0.651 | 1.640 | 0.340 | 218.6 |

## Wind-Energy Layer Diagnostics

| case | target_agl_m | selected_level_mode | selected_agl_mean_m | speed_mean | speed_p95 | w_abs_p99 | w_abs_max | w_grid_abs_p99 | w_grid_abs_max |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| none_20km | 50.00 | 0 | 144.8 | 1.721 | 3.330 | 1.360 | 2.653 | 0.783 | 1.445 |
| none_20km | 100.0 | 0 | 144.8 | 1.721 | 3.330 | 1.360 | 2.653 | 0.783 | 1.445 |
| none_20km | 200.0 | 0 | 144.8 | 1.721 | 3.330 | 1.360 | 2.653 | 0.783 | 1.445 |
| var_no_sx_20km | 50.00 | 0 | 144.8 | 1.661 | 3.289 | 1.146 | 2.129 | 0.813 | 2.392 |
| var_no_sx_20km | 100.0 | 0 | 144.8 | 1.661 | 3.289 | 1.146 | 2.129 | 0.813 | 2.392 |
| var_no_sx_20km | 200.0 | 0 | 144.8 | 1.661 | 3.289 | 1.146 | 2.129 | 0.813 | 2.392 |
| var_sx_20km | 50.00 | 0 | 144.8 | 1.548 | 3.132 | 1.069 | 2.048 | 0.800 | 2.325 |
| var_sx_20km | 100.0 | 0 | 144.8 | 1.548 | 3.132 | 1.069 | 2.048 | 0.800 | 2.325 |
| var_sx_20km | 200.0 | 0 | 144.8 | 1.548 | 3.132 | 1.069 | 2.048 | 0.800 | 2.325 |
| var_sx_top12km | 50.00 | 0 | 85.87 | 1.569 | 3.156 | 1.104 | 2.219 | 0.577 | 1.816 |
| var_sx_top12km | 100.0 | 0 | 85.87 | 1.569 | 3.156 | 1.104 | 2.219 | 0.577 | 1.816 |
| var_sx_top12km | 200.0 | 1 | 256.2 | 1.776 | 4.261 | 1.102 | 2.813 | 0.970 | 2.825 |

## Main Conclusions

- `wind = 'none'` keeps the original pathology: its global max `|w|` is `40.43 m s-1`, and the cell-wise max-`|w|` is dominated by level `39` in `100.0%` of cells.
- The variational wind solver sharply reduces physical vertical velocity at 20 km lid height: `var_sx_20km` has global max `|w| = 6.25 m s-1` versus `40.43 m s-1` for `none_20km`; `w` p99 falls from `6.93` to `0.53 m s-1`.
- The 12 km lid does not reduce the global physical-`w` maximum relative to the 20 km variational/Sx case (`6.34` versus `6.25 m s-1`), but it does remove the model-top pathology: at the top model level, max `|w|` falls from `1.52` to `0.44 m s-1`, and top-level max `|w_grid|` falls from `1.55` to `0.45 m s-1`.
- In the variational runs, the largest residual `w` is no longer at the lid. It occurs around 4-5.5 km AGL over high terrain, so the next tests should distinguish physically plausible mountain-wave/orographic motion from remaining coordinate or solver artifacts.
- Sx has a modest effect on the 20 km-lid run in this small case: it slightly lowers the `w_grid` envelope and near-surface wind speeds, but the difference between `var_no_sx_20km` and `var_sx_20km` is much smaller than the difference between `wind='none'` and the variational solver.
- The current vertical grid remains unsuitable for wind-energy-focused production runs: the 20 km grid has only one median mass level below 200 m AGL, and the 12 km grid also has only one. The future experiment design should therefore prioritize the higher-resolution near-surface vertical grids already proposed.
- The best provisional choice from these four runs is `wind = 'variational solver'`, `Sx = .True.`, and a lower lid near 12 km for suppressing the specific top-level pathology. This is not yet a production setup because the vertical grid is too coarse below 200 m AGL and SLEVE decay is still at the current extreme `1/1` setting rather than a better-tested value such as the HICAR default `2/6`.

## Files

- Summary CSV: `/Users/fuhrer/Work/agentic/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_w_sensitivity_24h/analysis/w_sensitivity_summary.csv`
- Level maxima CSV: `/Users/fuhrer/Work/agentic/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_w_sensitivity_24h/analysis/w_sensitivity_level_maxima.csv`
- Wind-energy layer CSV: `/Users/fuhrer/Work/agentic/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_w_sensitivity_24h/analysis/w_sensitivity_near_surface_wind.csv`
- Near-surface speed deltas CSV: `/Users/fuhrer/Work/agentic/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_w_sensitivity_24h/analysis/w_sensitivity_near_surface_speed_deltas.csv`
