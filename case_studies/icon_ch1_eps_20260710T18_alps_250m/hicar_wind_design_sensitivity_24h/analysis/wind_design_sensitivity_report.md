# HICAR Wind-Design Sensitivity Analysis

Run window: 25 output records from 2026-07-10 18 UTC through 2026-07-11 18 UTC.

All cases use `HICAR_release`, `wind = 'variational solver'`, `Sx = .True.`, `smooth_wind_distance = 500 m`, Morrison microphysics, no PBL/LSM/radiation/surface physics, ready files, and boundary-zone topography relaxation.

## Core Diagnostics

| case | nz | top_km | sleve_decay | nt | n_median_levels_below_200m | median_first_level_agl_m | median_first_dz_m | w_abs_max | w_abs_p99 | w_grid_abs_max | w_grid_abs_p99 | lower500_w_abs_max | lower500_w_abs_p99 | temperature_min_K |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v1_auto1_n60_top12_s26 | 60 | 12 | 2/6 | 25 | 6 | 9.691 | 21.76 | 6.413 | 0.827 | 7.466 | 1.052 | 3.207 | 1.158 | 218.7 |
| v2_auto1_n80_top12_s26 | 80 | 12 | 2/6 | 25 | 7 | 7.291 | 16.41 | 6.426 | 0.800 | 7.462 | 1.032 | 3.204 | 1.156 | 218.4 |
| v3_auto1_n60_top10_s26 | 60 | 10 | 2/6 | 25 | 6 | 9.605 | 21.42 | 6.385 | 0.845 | 7.383 | 1.015 | 3.196 | 1.152 | 231.8 |
| v4_auto1_n70_top14_s26 | 70 | 14 | 2/6 | 25 | 6 | 9.749 | 21.99 | 6.417 | 0.780 | 7.434 | 1.091 | 3.215 | 1.161 | 213.4 |
| v5_auto4_n70_top12_s26 | 70 | 12 | 2/6 | 25 | 6 | 7.291 | 20.66 | 6.409 | 0.779 | 7.457 | 1.031 | 3.219 | 1.157 | 218.3 |
| s0_auto1_n60_top12_s11 | 60 | 12 | 1/1 | 25 | 6 | 9.864 | 22.45 | 6.396 | 0.813 | 7.532 | 2.095 | 3.241 | 1.162 | 218.4 |
| s1_auto1_n60_top12_s15_3 | 60 | 12 | 1.5/3 | 25 | 6 | 9.797 | 22.18 | 6.416 | 0.818 | 7.595 | 1.479 | 3.230 | 1.165 | 218.6 |
| s2_auto1_n60_top12_s24 | 60 | 12 | 2/4 | 25 | 6 | 9.713 | 21.85 | 6.399 | 0.824 | 7.523 | 1.326 | 3.222 | 1.161 | 218.7 |

## Wind-Energy Layer Diagnostics

| case | target_agl_m | selected_level_mode | selected_agl_mean_m | speed_mean | speed_p95 | speed_max | w_abs_p99 | w_abs_max |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v1_auto1_n60_top12_s26 | 50.00 | 2 | 56.46 | 1.764 | 3.987 | 8.904 | 1.303 | 3.207 |
| v1_auto1_n60_top12_s26 | 100.0 | 3 | 96.34 | 1.773 | 4.143 | 9.217 | 1.260 | 3.165 |
| v1_auto1_n60_top12_s26 | 200.0 | 6 | 199.5 | 1.816 | 4.350 | 9.797 | 1.115 | 2.751 |
| v2_auto1_n80_top12_s26 | 50.00 | 2 | 44.87 | 1.768 | 3.918 | 8.759 | 1.306 | 3.185 |
| v2_auto1_n80_top12_s26 | 100.0 | 4 | 99.18 | 1.765 | 4.154 | 9.301 | 1.251 | 3.147 |
| v2_auto1_n80_top12_s26 | 200.0 | 7 | 198.6 | 1.810 | 4.332 | 9.799 | 1.105 | 2.794 |
| v3_auto1_n60_top10_s26 | 50.00 | 2 | 54.47 | 1.763 | 3.983 | 8.930 | 1.302 | 3.196 |
| v3_auto1_n60_top10_s26 | 100.0 | 3 | 98.62 | 1.774 | 4.148 | 9.228 | 1.253 | 3.157 |
| v3_auto1_n60_top10_s26 | 200.0 | 6 | 199.9 | 1.816 | 4.347 | 9.812 | 1.108 | 2.759 |
| v4_auto1_n70_top14_s26 | 50.00 | 2 | 58.08 | 1.765 | 3.993 | 8.888 | 1.305 | 3.215 |
| v4_auto1_n70_top14_s26 | 100.0 | 3 | 94.09 | 1.769 | 4.140 | 9.204 | 1.265 | 3.171 |
| v4_auto1_n70_top14_s26 | 200.0 | 6 | 199.9 | 1.817 | 4.348 | 9.780 | 1.119 | 2.753 |
| v5_auto4_n70_top12_s26 | 50.00 | 2 | 59.25 | 1.768 | 4.017 | 8.939 | 1.307 | 3.219 |
| v5_auto4_n70_top12_s26 | 100.0 | 3 | 98.27 | 1.764 | 4.157 | 9.314 | 1.253 | 3.149 |
| v5_auto4_n70_top12_s26 | 200.0 | 5 | 201.8 | 1.815 | 4.348 | 9.817 | 1.115 | 2.833 |
| s0_auto1_n60_top12_s11 | 50.00 | 2 | 60.44 | 1.767 | 3.996 | 8.873 | 1.305 | 3.241 |
| s0_auto1_n60_top12_s11 | 100.0 | 3 | 94.14 | 1.765 | 4.132 | 9.184 | 1.267 | 3.193 |
| s0_auto1_n60_top12_s11 | 200.0 | 5 | 182.7 | 1.813 | 4.355 | 9.743 | 1.140 | 2.915 |
| s1_auto1_n60_top12_s15_3 | 50.00 | 2 | 59.79 | 1.768 | 3.998 | 8.885 | 1.304 | 3.230 |
| s1_auto1_n60_top12_s15_3 | 100.0 | 3 | 92.97 | 1.765 | 4.131 | 9.196 | 1.267 | 3.184 |
| s1_auto1_n60_top12_s15_3 | 200.0 | 6 | 200.7 | 1.825 | 4.358 | 9.762 | 1.120 | 2.780 |
| s2_auto1_n60_top12_s24 | 50.00 | 2 | 58.90 | 1.769 | 3.999 | 8.905 | 1.304 | 3.222 |
| s2_auto1_n60_top12_s24 | 100.0 | 3 | 92.04 | 1.767 | 4.132 | 9.211 | 1.267 | 3.180 |
| s2_auto1_n60_top12_s24 | 200.0 | 6 | 201.3 | 1.818 | 4.348 | 9.781 | 1.109 | 2.760 |

## Near-Surface Deltas Relative To `v1_auto1_n60_top12_s26`

| target_agl_m | comparison | speed_mean_diff | speed_mean_abs_diff | speed_p95_abs_diff | w_abs_mean_diff |
| --- | --- | --- | --- | --- | --- |
| 50.00 | v2_auto1_n80_top12_s26 | 0.004 | 0.068 | 0.225 | 0.009 |
| 50.00 | v3_auto1_n60_top10_s26 | -0.001 | 0.014 | 0.032 | 0.001 |
| 50.00 | v4_auto1_n70_top14_s26 | 0.001 | 0.010 | 0.020 | -0.001 |
| 50.00 | v5_auto4_n70_top12_s26 | 0.004 | 0.024 | 0.075 | -0.002 |
| 50.00 | s0_auto1_n60_top12_s11 | 0.003 | 0.027 | 0.088 | -0.001 |
| 50.00 | s1_auto1_n60_top12_s15_3 | 0.004 | 0.022 | 0.073 | -0.001 |
| 50.00 | s2_auto1_n60_top12_s24 | 0.005 | 0.018 | 0.064 | -0.000 |
| 100.0 | v2_auto1_n80_top12_s26 | -0.008 | 0.027 | 0.091 | -0.004 |
| 100.0 | v3_auto1_n60_top10_s26 | 0.001 | 0.021 | 0.089 | -0.001 |
| 100.0 | v4_auto1_n70_top14_s26 | -0.005 | 0.014 | 0.072 | 0.000 |
| 100.0 | v5_auto4_n70_top12_s26 | -0.009 | 0.034 | 0.115 | -0.003 |
| 100.0 | s0_auto1_n60_top12_s11 | -0.008 | 0.025 | 0.093 | 0.000 |
| 100.0 | s1_auto1_n60_top12_s15_3 | -0.008 | 0.022 | 0.095 | 0.000 |
| 100.0 | s2_auto1_n60_top12_s24 | -0.006 | 0.019 | 0.092 | 0.001 |
| 200.0 | v2_auto1_n80_top12_s26 | -0.006 | 0.047 | 0.140 | -0.002 |
| 200.0 | v3_auto1_n60_top10_s26 | 0.000 | 0.030 | 0.113 | -0.000 |
| 200.0 | v4_auto1_n70_top14_s26 | 0.002 | 0.018 | 0.059 | 0.000 |
| 200.0 | v5_auto4_n70_top12_s26 | -0.000 | 0.065 | 0.169 | -0.001 |
| 200.0 | s0_auto1_n60_top12_s11 | -0.003 | 0.059 | 0.192 | 0.005 |
| 200.0 | s1_auto1_n60_top12_s15_3 | 0.009 | 0.041 | 0.139 | 0.001 |
| 200.0 | s2_auto1_n60_top12_s24 | 0.003 | 0.025 | 0.086 | -0.001 |

## Interpretation

- The `v2` 80-level setup has the best near-surface resolution: 7 median mass levels below 200 m AGL versus 6 in the 60-level baseline.
- The 10 km lid case `v3` is fastest and finite, but it has the lowest median model top and is less conservative for Switzerland-wide domains with Alpine terrain near 4500-5000 m ASL.
- The 14 km lid case `v4` is also finite, but it gives no clear near-surface advantage over 12 km while adding cost and retaining a higher-altitude upper domain.
- The COSMO-like `auto4` vertical distribution (`v5`) is finite, but it provides fewer low-level levels than `v2` for the wind-energy target layer.
- Among the conservative SLEVE variants, `s0` and `s1` increase the vertical-velocity envelope relative to the default-like `2/6` baseline; `s2` is closer but still does not improve the baseline enough to justify replacing `2/6`.
- All cases stayed finite by these bulk checks. Minimum temperatures remain low enough to keep an eye on the theta-limiter behavior, but no case reproduced the severe top-level `w` pathology from the older `wind='none'` run.
- Model stderr logs were empty except for one Slurm shutdown-IO line in `v2` after successful job completion: `srun: error: eio_handle_mainloop: Abandoning IO 60 secs after job shutdown initiated`.

## Recommendation

Use `v2_auto1_n80_top12_s26` as the next default HICAR setup for wind-downscaling experiments at 250 m: `auto_level = 1`, `nz = 80`, `model_top_height = 12000 m`, `height_lowest_level = 15 m`, `stretch_fac = 0.65`, `decay_rate_L_topo = 2.0`, `decay_rate_S_topo = 6.0`, `wind = 'variational solver'`, `Sx = .True.`, and `smooth_wind_distance = 500 m`.

The rationale is practical: it is the only tested setup with clearly improved vertical resolution in the 50-200 m wind-energy layer, it completed the 24 h case cleanly, it keeps the 12 km lid that already removed the earlier top-level pathology, and the SLEVE choice remains conservative enough for larger Swiss domains.

If the 80-level setup becomes too expensive for a larger Switzerland-wide domain, use `v1_auto1_n60_top12_s26` as the cost-control fallback. Do not switch to the slower SLEVE-decay options from this batch unless a larger-domain test shows that `2/6` creates a domain-edge or terrain-following-coordinate problem.

For production-scale work, validate this setup on at least one larger Switzerland-with-border domain and one longer multi-day case before freezing it.

## Files

- Summary CSV: `/Users/fuhrer/Work/agentic/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_wind_design_sensitivity_24h/analysis/wind_design_summary.csv`
- Level statistics CSV: `/Users/fuhrer/Work/agentic/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_wind_design_sensitivity_24h/analysis/wind_design_level_stats.csv`
- Wind-energy layer CSV: `/Users/fuhrer/Work/agentic/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_wind_design_sensitivity_24h/analysis/wind_design_near_surface.csv`
- Near-surface deltas CSV: `/Users/fuhrer/Work/agentic/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_wind_design_sensitivity_24h/analysis/wind_design_near_surface_deltas.csv`
- Vertical-level plot: `/Users/fuhrer/Work/agentic/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_wind_design_sensitivity_24h/analysis/wind_design_vertical_levels.png`
- Core-metrics plot: `/Users/fuhrer/Work/agentic/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_wind_design_sensitivity_24h/analysis/wind_design_core_metrics.png`
