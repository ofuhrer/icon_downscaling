# Balfrin quickstart

```bash
ssh balfrin
cd "$SCRATCH/icon_hicar/icon_downscaling"
. scripts/load_balfrin_site_config.sh
```

Build HICAR with `case_studies/swiss_200m/scripts/build_hicar_balfrin.sbatch`.
Prepare one valid-time land domain with
`prepare_hicarprep_land_balfrin.sbatch`. Configure the paths and four seasonal
windows in one campaign JSON, then run:

```bash
python orchestration/rd_campaign.py campaign.json --watch
```

The controller checks live access before explicit `pp-short` and
`preemptible` submissions. Inspect progress with `squeue -u "$USER"` and by
running the controller once without `--watch`. Do not run model computation on
the login node.
