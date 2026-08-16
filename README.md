# ICON-HICAR Alpine experiments

Scientific R&D with HICAR and MeteoSwiss ICON REA-L-CH1 over a 200 m Swiss
domain. See `memory/project-assessment.md` for current evidence, limitations,
and retained state.

Core components:

- `preprocessing/hicarprep/`: native REA-L atmospheric/land transformation
- `case_studies/swiss_200m/`: reference configuration and validation
- `orchestration/rd_campaign.py`: restart-linked campaign controller
- `HICAR/`: production fork
- `.agents/skills/`: Balfrin and scientific procedures

Generated GRIB, NetCDF, restarts, outputs, builds, and logs stay outside Git in
`$SCRATCH/icon_hicar`; the current scratch tree is intentionally empty. Useful
results retain a source commit, concise configuration, case/interval, and key
metrics or outputs.

```bash
./scripts/bootstrap_externals.sh
python -m pip install -r requirements/dev.txt
python -m pytest -q
```
