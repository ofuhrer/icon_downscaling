# Project: ICON-to-HICAR Alpine downscaling

## Purpose and mode

Determine whether and how roughly 1 km MeteoSwiss ICON should be downscaled
with HICAR to 100--250 m over Alpine domains. The 20-year REA-L-CH1 application
is not selected; this project is in scientific R&D. The current decision and
next admissible questions are in `memory/project-assessment.md`.

Optimize for learning rate: state the uncertainty, run the smallest controlled
experiment or existing-data analysis that distinguishes the hypotheses,
analyze it immediately, and update the synthesis. Prefer A/B interventions,
short representative cases, and reusable outputs. Generalize infrastructure
only when a defect threatens interpretation or repeatedly blocks useful work.

Required provenance is normally the source commit, relevant input/config
delta, executed case, and key result. Add immutable runtimes, exhaustive
manifests, or archive machinery only when identity, cost, or concurrent use
makes them material.

## Authoritative state

- Coordinator: `/Users/fuhrer/Work/agentic/icon_hicar`; remote
  `git@github.com:ofuhrer/icon_downscaling.git`
- HICAR: `HICAR`; production `feature/icon_downscaling` at `0b9b0cb6`; remote
  `git@github.com:ofuhrer/HICAR.git`
- Balfrin scratch: `$SCRATCH/icon_hicar`
- Durable root: `/store_new/mch/msopr/olifu/icon_downscaling`
- Long-run controller: `orchestration/rd_campaign.py`

Keep current synthesis in `memory/project-assessment.md` and exact run details
in concise case manifests. Do not create diaries, command transcripts, or
general evidence ledgers; replace superseded text.

## Routing

Use the smallest project skill in `.agents/skills/`:

- `balfrin-user-environment`: SSH, modules, partitions, storage, cleanup
- `icon-balfrin-grib`: archive/FDB and GRIB inspection
- `icon-hicar-forcing`: REA-L decoding, hicarprep, forcing validation
- `icon-hicar-domain`: static domain and forcing subdomain
- `hicar-alpine-configuration`: grid, physics, solver, experiment design
- `hicar-balfrin-runtime`: builds, Slurm runs, debugging, benchmarks

For code discovery use codebase-memory first; use `rg`/direct reads for scripts,
configs, literals, or incomplete graph coverage. Projects are `icon_hicar` and
`Users-fuhrer-Work-agentic-icon_hicar-HICAR`.

## Non-negotiables

- Preserve unrelated work and never store credentials.
- On Balfrin, follow `balfrin-user-environment`. Never compute on login nodes
  or `balfrin-ln001`; explicitly name and live-validate an authorized partition.
- Keep large transient data in scratch and project-owned durable data only
  below `/store_new/mch/msopr/olifu`.
- Validate a small representative case before increasing area, duration, or
  GPU count. Use release builds for throughput and debug builds for diagnosis.
- Publish shared artifacts by write/validate/atomic rename, then create the
  ready marker. Before deletion, audit jobs and retained evidence; delete only
  validated exact targets through a CPU partition, then re-audit.
- Update a skill only for reusable procedure/invariant changes and project
  assessment only when the conclusion, priority, or capability changes.
