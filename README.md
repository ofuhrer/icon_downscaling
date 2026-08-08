# ICON-to-HICAR Alpine downscaling

Scientific R&D for dynamically downscaling MeteoSwiss ICON REA-L-CH1 from
about 1 km to 200 m over Switzerland with HICAR.

The active workflow is deliberately small:

1. decode native REA-L GRIB and transform it with `hicarprep`;
2. initialize land/soil from the native REA-L state;
3. run short restart-linked HICAR segments in Balfrin's `preemptible` queue;
4. compare the output with SwissMetNet stations and REA-L-CH1.

`memory/project-assessment.md` contains the current scientific synthesis.
`AGENTS.md` contains cluster and project working rules.

Important paths:

- `preprocessing/hicarprep/`: the only atmospheric/land preprocessor;
- `case_studies/swiss_200m/`: selected namelist, Slurm jobs, and comparisons;
- `orchestration/rd_campaign.py`: restartable campaign controller;
- `HICAR/`: the HICAR fork;
- `.agents/skills/`: Balfrin and HICAR procedures.

Generated GRIB, NetCDF, restart, output, and log files live outside Git under
`$SCRATCH/icon_hicar`. A useful experiment retains its source commit, concise
configuration, interval, and key output or derived metrics. Git history is the
archive for removed workflow code.

```bash
./scripts/bootstrap_externals.sh
python -m pip install -r requirements/dev.txt
make test
```
