# Project: ICON-to-HICAR Alpine downscaling

## Purpose and mode

Determine whether and how roughly 1 km MeteoSwiss ICON output should be
dynamically downscaled with HICAR to 100--250 m over Alpine domains. The
20-year REA-L-CH1 application is not selected: current work is scientific R&D,
and `memory/project-assessment.md` contains the active conclusion and next
question.

Optimize for learning rate. State the uncertainty, run the smallest controlled
experiment or existing-data analysis that distinguishes the hypotheses,
analyze it immediately, and update the synthesis. Prefer A/B interventions,
short representative periods, small domains, and reusable existing outputs.
Generalize infrastructure only when a defect threatens interpretation or
repeatedly blocks valuable experiments.

Hard requirements are scientific validity, cluster/data safety, reasonable
compute use, and enough provenance to reproduce a useful result: source
commit, relevant input/config differences, executed case, and key output.
Production-grade manifests, immutable runtimes, and archive machinery are
optional unless artifact identity or concurrent use makes them material.

## Authoritative locations and refs

- Coordinator: `/Users/fuhrer/Work/agentic/icon_hicar`, remote
  `git@github.com:ofuhrer/icon_downscaling.git`
- HICAR fork: `HICAR`, remote `git@github.com:ofuhrer/HICAR.git`
- HICAR production branch/commit: `feature/icon_downscaling` at
  `0b9b0cb682c261e5fe8224500c64ccf16a2b83c7`
- Balfrin scratch: `$SCRATCH/icon_hicar`
- Durable online root: `/store_new/mch/msopr/olifu/icon_downscaling`
- Recoverable long-run controller: `orchestration/rd_campaign.py`

Put current synthesis, decisions, and ranked follow-up in
`memory/project-assessment.md`; put exact run provenance in concise case
manifests. Do not add diaries, command transcripts, or general evidence
ledgers.

## Routing and discovery

Use the smallest relevant project skill in `.agents/skills/`:

- `balfrin-user-environment`: SSH, modules, partitions, storage, cleanup
- `icon-balfrin-grib`: ICON archive/FDB and GRIB inspection
- `icon-hicar-forcing`: REA-L decoding, hicarprep, forcing validation
- `icon-hicar-domain`: static domain, land data, forcing subdomain
- `hicar-alpine-configuration`: grid, solver, configuration, experiment design
- `hicar-balfrin-runtime`: builds, Slurm runs, CPU/GPU debugging, benchmarks

For code discovery prefer codebase-memory in this order: `index_status`,
`search_graph`, `trace_path`, `get_code_snippet`, `query_graph`, `search_code`.
Projects are `icon_hicar` and
`Users-fuhrer-Work-agentic-icon_hicar-HICAR`. Fall back to `rg` or direct reads
for configs, scripts, literals, or incomplete graph coverage; re-index stale
graphs before broad discovery.

## Balfrin safety

- Use `ssh balfrin`; retry transient refusal twice. Never compute on
  `balfrin-ln001` or run heavy work on a login node.
- Name one explicit reviewed partition per submission: GPU
  `debug`/`short`/`normal`, CPU/post-processing `pp-short`/`pp-long`, or
  controller-managed heavy work `preemptible`. Immediately before submission,
  require live `AllowGroups=ALL` or exact group `s83`; supplemental-group
  access is forbidden.
- Initialize the MCH module path in every non-interactive shell/job.
- Keep large data/builds in scratch. Use only
  `/store_new/mch/msopr/olifu` for project-owned durable data; never rewrite
  that prefix. Check live Confluence/Rovo guidance when current operational
  behavior matters.
- Before cleanup, audit jobs/controllers and prove retained source/evidence.
  Delete only validated exact targets through a CPU partition, then re-audit.

## Engineering and memory

- Preserve unrelated user work; prefer existing scripts and conventions.
- Use release builds for throughput and debug builds for diagnosis. Validate a
  small representative case before increasing size, duration, or GPU count.
- Bound costly/preemptible retries and retained data; reuse forcing caches and
  the segmented controller when useful.
- For shared publication: write, validate, atomically rename, then create
  `<file>.ready`. Otherwise avoid unnecessary ready markers and manifests.
- Never store credentials. Update skills only for reusable procedures or
  invariants; update project assessment only when evidence changes the
  conclusion, priority, or capability. Remove superseded details instead of
  appending history.
