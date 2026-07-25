# ICON-CH1-EPS 2026-07-10 18 UTC Alps 250 m Case Study

## Purpose

First frozen ICON-to-HICAR preprocessing experiment using the available ICON-CH1-EPS CTRL sample data for a compact Alpine 250 m domain.

## Domain

- Center latitude: `46.75`
- Center longitude: `8.15`
- Width: `20 km`
- Height: `20 km`
- HICAR grid spacing: `250 m`
- HICAR grid size: `81 x 81`
- Static source mode: public-source pragmatic static fields from `prepare_static_inputs.py`
- Boundary topography relaxation: default enabled for final static file.

## ICON Forcing

- Source cycle: ICON-CH1-EPS CTRL initialized `2026-07-10 18 UTC`
- Source member: `000`
- Source root on Balfrin: `$SCRATCH/icon_hicar/data/icon_ch1_eps_20260710T18_ctrl_oper_icon_000`
- Dynamic file family: hourly `lfff????????` files from forecast hour `0` through `33`
- Static geometry file: `lfff00000000c`
- Prepared forcing target grid:

```text
rotlatlon,356860000,-1420000,540000,1060000,20000,20000,190000000,43000000
```

- Forcing grid: compact rotated-lat/lon grid already verified with the ICON-to-HICAR converter; wider than the HICAR domain and suitable for initial experiments
- Prepared forcing horizontal spacing: `0.02 deg x 0.02 deg` on the compact rotated-lat/lon ICON-CH1 post-processing grid
- Vertical levels: ICON full model levels `1..80`, half levels `1..81`
- Output cadence: hourly, 34 files from `f000` to `f033`

## Expected Workflow

1. Create a provisional unrelaxed static/domain file to define the HICAR lat/lon grid.
2. Generate ICON/HICAR forcing files using `prepare_icon_inputs.sh` and the explicit target grid above.
3. Generate final static/domain file using `prepare_static_inputs.py --boundary-topo-source` against a prepared ICON forcing file containing `HSURF`, `lat_1`, and `lon_1`.
4. Validate static and forcing files in depth before running HICAR.

## Generated Files

- Local case root: `/Users/fuhrer/Work/agentic/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m`
- Balfrin case root: `$SCRATCH/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m`
- Native ICON manifest: `config/icon_native_manifest.tsv`
- Prepared forcing files: `forcing/hicar_forcing_f000.nc` through `forcing/hicar_forcing_f033.nc`
- Prepared forcing ready markers: `forcing/hicar_forcing_f000.nc.ready` through `forcing/hicar_forcing_f033.nc.ready`
- Final relaxed static file: `static/domain_static_relaxed.nc`
- Final relaxed static ready marker: `static/domain_static_relaxed.nc.ready`
- Validation script: `validation/validate_case_outputs.py`
- Full validation report: `validation/validation_full.md`
- HICAR ready-file smoke namelist: `hicar_ready_smoke/input/icon_hicar_250m_ready_smoke.nml`
- HICAR ready-file smoke Slurm script: `hicar_ready_smoke/run_hicar_ready_smoke.sbatch`

## Validation Summary

Validation passed on 2026-07-11.

- Static grid: `81 x 81`, `250 m` spacing, 4 soil layers.
- Static latitude range: `46.65997 .. 46.83995`.
- Static longitude range: `8.01891 .. 8.28109`.
- Final relaxed topography range: `521.047 .. 2634.76 m`.
- High-resolution source topography range: `481.288 .. 2794.05 m`.
- Driving ICON topography range over the HICAR domain: `521.047 .. 2611.2 m`.
- Topography blend weight range: `0 .. 1`; the outer boundary uses driving topography and the interior reaches high-resolution topography.
- Forcing files: 34 hourly files from `+000 h` through `+033 h`.
- Ready files: 34 forcing `.nc.ready` markers plus `domain_static_relaxed.nc.ready`.
- Forcing grid: `125 x 185`, with `80` full levels and `81` half levels.
- Forcing latitude range: `45.490215 .. 48.060001`.
- Forcing longitude range: `5.308322 .. 10.807791`.
- All checked forcing variables are finite and in plausible ranges: `P`, `QV`, `T`, `U`, `V`, `W`, `HFL`, `HHL`, `HSURF`, `FR_LAND`.

## HICAR Ready-File Smoke Test

Ready-file support was tested with the current Balfrin HICAR binary:

- Binary: `$SCRATCH/icon_hicar/HICAR/bin/HICAR_debug`
- Build job: `4831914`
- Ready-smoke run root: `$SCRATCH/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_ready_smoke`
- Namelist: `hicar_ready_smoke/input/icon_hicar_250m_ready_smoke.nml`
- Slurm script: `hicar_ready_smoke/run_hicar_ready_smoke.sbatch`
- Run job: `4831915`
- Run window: `2026-07-10 18:00:00` to `2026-07-10 18:05:00`
- Result: completed successfully.
- Output: `hicar_ready_smoke/output/domain_static_relaxed_2026-07-10_18-00-00.nc`

The namelist enables the ready-file mechanism in both places:

```fortran
&domain
  wait_for_ready_file = .True.
  ready_file_timeout = 120
/

&forcing
  wait_for_ready_file = .True.
  ready_file_timeout = 120
  relax_filters = .True.
/
```

The ready-wait path was explicitly tested by temporarily removing
`hicar_forcing_f000.nc.ready`, restoring it after a delay, and confirming that
HICAR logged `Waiting for ready file:` and then completed `--check-nml`.

Smoke output sanity:

- Output dimensions include `time = 2`, `level = 40`, `lat_y = 81`, `lon_x = 81`.
- Text checks found zero `nan`/`inf` hits in `qv`, `temperature`, `w`, `u`, `v`, and `precipitation`.

## HICAR Full 33 h Run

A full ready-file-enabled HICAR run was submitted on Balfrin after the 5-minute
smoke test succeeded.

- Run root: `$SCRATCH/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_ready_full_33h`
- Namelist: `hicar_ready_full_33h/input/icon_hicar_250m_ready_full_33h.nml`
- Slurm script: `hicar_ready_full_33h/run_hicar_ready_full_33h.sbatch`
- Slurm job: `4831916`
- Partition: `pp-long`
- Wall time: `06:00:00`
- MPI ranks: `5` total, with `HICAR_IO_PER_NODE=1`
- Run window: `2026-07-10 18:00:00` to `2026-07-12 03:00:00`
- Output interval: `3600 s`
- Final Slurm state: `FAILED`, exit code `15:0`, elapsed `00:39:25`.
- Failure mode: HICAR aborted because the forcing file list ended before the simulation end-time handling was satisfied. The log reports `Current input time = 2026/07/12 04:00:00` while the requested model end was `2026/07/12 03:00:00`; HICAR therefore appears to require one forcing file beyond the requested end time.
- Output written before abort:
  - `hicar_ready_full_33h/output/domain_static_relaxed_2026-07-10_18-00-00.nc` with `24` time records.
  - `hicar_ready_full_33h/output/domain_static_relaxed_2026-07-11_18-00-00.nc` with `8` time records.
  - Total written output records: `32`.
- The run completed hourly output through model time `2026-07-12 01:00:00`; it aborted while advancing into the final part of the requested window.
