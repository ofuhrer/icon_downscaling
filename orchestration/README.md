# Restartable R&D campaigns

`rd_campaign.py` prepares hourly hicarprep forcing records and runs each
season as serial restart-linked segments. Independent seasons may run in
parallel. Heavy jobs use `preemptible`; input concurrency and CPU placement are
bounded by the campaign configuration.

State is only what restart recovery needs:

- `<record>.ready` means an hourly forcing file is complete; sparse experiments
  additionally publish `<record>.lbc.nc.ready`;
- `attempt-N.job` records the Slurm job ID for a submitted attempt;
- `segment.complete` and `segment.json` mean a model segment finished and
  identify its terminal restart.

Interrupted or failed attempts use a new directory and are retried up to the
configured bound. A chain advances only from a completed predecessor restart.

Run once to submit currently eligible work, or watch continuously:

```bash
python orchestration/rd_campaign.py campaign.json
python orchestration/rd_campaign.py campaign.json --watch
```

The JSON configuration supplies `root`, `repo_root`, `forcing_dir`,
`hicar_executable`, `hicar_support_dir`, `hicar_build_provenance`, `python`,
RBF weights, and `seasons` with `name`, `start`, `end`, and the valid-time
runtime-domain file. `use_sparse_lbc` defaults to true for old experiment
configs; the selected regular-relaxation reference sets it false.
`radiation_scheme` selects `rrtmgp` or `rrtmg` and is passed through to both
namelist rendering and restart validation. Each season has its own forcing
cache because records embed the runtime-domain identity.

The controller refuses dirty coordinator/HICAR trees and refuses executables
built from an uncommitted HICAR patch. Each completed segment must contain the
exact expected output times, an exact-time terminal restart, the selected
physics recorded in that restart, and every hourly forcing bracket (plus LBC
when selected). A
separate 2 h continuous-versus-1 h segmented comparison is required before a
seasonal campaign.
