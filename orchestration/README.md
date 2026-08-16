# Restartable R&D campaigns

`rd_campaign.py` prepares hourly hicarprep forcing records and runs each
season as serial restart-linked segments. Independent seasons may run in
parallel only when allowed by `max_active_models`. Model partition, global
model concurrency, input concurrency and CPU placement are bounded by the
campaign configuration. `model_max_partition_fraction` fails closed when that
configured concurrency could reserve more than the selected share of a live
partition.

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
runtime-domain file. A season may also carry a preverified lowercase
`static_sha256`; when present it enables receipt-bound publication validation
without rehashing and rereading the static/science arrays for every hour.
Without it, input generation retains the full validator. `use_sparse_lbc`
defaults to false because regular forcing
owns production relaxation; sparse-LBC experiments must opt in explicitly.
`input_rbf_backend` (`numpy` or `numba`) makes the qualified accelerated path
explicit in campaign provenance.
`radiation_scheme` selects `rrtmgp` or `rrtmg` and is passed through to both
namelist rendering and restart validation. `defer_uploads` defaults to false;
when enabled it opts the NVHPC OpenACC runtime into deferred data uploads with
a one-byte threshold. It cannot be combined with `acc_synchronous` and should
be enabled only after a topology-matched output/restart A/B gate. Each season
has its own forcing cache because records embed the runtime-domain identity.

The selected national campaign uses `model_partition=normal`,
`max_active_models=1` and `model_max_partition_fraction=0.5`: one qualified
12-node/60-rank segment at a time, or 27.3% of the current 44-node partition.
This avoids spending bounded attempts under repeated priority pre-emption while
preserving the qualified model topology.

The controller refuses dirty coordinator/HICAR trees and refuses executables
built from an uncommitted HICAR patch. Each completed segment must contain the
exact expected output times, an exact-time terminal restart, the selected
physics recorded in that restart, and every hourly forcing bracket (plus LBC
when selected). A
separate 2 h continuous-versus-1 h segmented comparison is required before a
seasonal campaign. Acceptance is based on finite fields, timing continuity and
perturbation magnitude relative to uninterrupted evolution; bit identity is
not required.
