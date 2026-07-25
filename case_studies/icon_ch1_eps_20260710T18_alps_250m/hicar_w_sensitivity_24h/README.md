# HICAR upper-level vertical-wind sensitivity runs

- Case root: `/scratch/mch/olifu/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m`
- Sensitivity root: `/scratch/mch/olifu/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_w_sensitivity_24h`
- Start: `2026-07-10 18:00:00`
- End: `2026-07-11 18:00:00`
- Purpose: isolate effects of the wind solver, Sx terrain sheltering, and model-lid height on upper-level `w`.

| Case | Description |
| --- | --- |
| `none_20km` | current wind none, 20 km lid |
| `var_no_sx_20km` | variational solver, no Sx, 20 km lid |
| `var_sx_20km` | variational solver, Sx, 20 km lid |
| `var_sx_top12km` | variational solver, Sx, 12 km lid |
