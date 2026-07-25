# HICAR Wind-Downscaling Design Sensitivity Runs

- Case root: `/scratch/mch/olifu/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m`
- Sensitivity root: `/scratch/mch/olifu/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_wind_design_sensitivity_24h`
- Start: `2026-07-10 18:00:00`
- End: `2026-07-11 18:00:00`
- Executable: `/scratch/mch/olifu/icon_hicar/HICAR/bin/HICAR_release`
- Purpose: explore vertical grid, lid height, and conservative SLEVE decay choices for wind downscaling using the validated release executable.
- Fixed physics baseline: `wind = 'variational solver'`, `Sx = .True.`, `smooth_wind_distance = 500 m`.
- SLEVE choices deliberately avoid aggressive decay rates so candidate settings remain plausible for larger domains, including an all-Switzerland domain with border and terrain up to roughly 4500-5000 m ASL.

| Case | Description |
| --- | --- |
| `v1_auto1_n60_top12_s26` | primary vertical grid: auto1 n60 top12 km, SLEVE 2/6 |
| `v2_auto1_n80_top12_s26` | high near-surface resolution: auto1 n80 top12 km, SLEVE 2/6 |
| `v3_auto1_n60_top10_s26` | lower lid: auto1 n60 top10 km, SLEVE 2/6 |
| `v4_auto1_n70_top14_s26` | higher lid: auto1 n70 top14 km, SLEVE 2/6 |
| `v5_auto4_n70_top12_s26` | alternative COSMO-like vertical distribution: auto4 n70 top12 km, SLEVE 2/6 |
| `s0_auto1_n60_top12_s11` | slow/conservative SLEVE decay control: auto1 n60 top12 km, SLEVE 1/1 |
| `s1_auto1_n60_top12_s15_3` | conservative compromise SLEVE decay: auto1 n60 top12 km, SLEVE 1.5/3 |
| `s2_auto1_n60_top12_s24` | moderate SLEVE decay: auto1 n60 top12 km, SLEVE 2/4 |
