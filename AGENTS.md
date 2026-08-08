# Project: ICON-to-HICAR Alpine downscaling

## Objective and current mode

Determine how MeteoSwiss ICON output at roughly 1 km should be dynamically
downscaled with HICAR to 100--250 m over Alpine domains. The long-term
application is 20 years of ICON REA-L-CH1 over Switzerland, initially focused
on wind, but the production strategy is not yet selected.

The project is currently in **scientific R&D / strategy-discovery mode**.
Production qualification resumes only after experiments support a concise
answer to: what strategy should be used, why, under which conditions, and with
which important uncertainties still open?

## Working model

Optimize for scientific learning rate and information gain. Existing
production-quality infrastructure and validated engineering results are
useful foundations, not constraints on the scientific conclusion and not
prerequisites for a trustworthy exploratory result.

For each open question:

1. State the scientific uncertainty or competing hypotheses.
2. Choose the smallest controlled experiment or existing-data analysis that
   can distinguish them.
3. Execute it directly.
4. Analyze the result immediately.
5. Update the working scientific understanding.
6. Choose the next experiment from what was learned.

Prefer A/B tests, causal interventions, existing simulations, small domains,
short periods, and representative regimes. A local transparent workaround is
acceptable when it unblocks an experiment. Generalize or harden infrastructure
only when a defect threatens scientific interpretation, repeatedly blocks
high-value experiments, or is itself under study.

Continuously separate established engineering foundations, open scientific
choices, and deferred production work in `memory/project-assessment.md`.
Treat blockers as local. Do not create new gates or tasks for observations
that do not change the next scientific decision.

Hard requirements during R&D are limited to scientific validity, cluster and
data safety, and avoiding unreasonable compute waste. Use enough provenance
to reproduce and interpret an experiment: source commit, relevant input and
configuration differences, executed case, and key outputs normally suffice.
Immutable runtimes, exhaustive manifests, publication bundles, archive
contracts, generalized orchestration, and production-grade validators are
optional unless they materially affect those requirements.

## Workspace

- Coordinating repository: `/Users/fuhrer/Work/agentic/icon_hicar`
- Coordinating remote: `git@github.com:ofuhrer/icon_downscaling.git`
- HICAR fork: `/Users/fuhrer/Work/agentic/icon_hicar/HICAR`
- Fieldextra source reference: `/Users/fuhrer/Work/agentic/icon_hicar/fieldextra`
- Balfrin project root: `$SCRATCH/icon_hicar`
- Durable online root: `/store_new/mch/msopr/olifu/icon_downscaling`
- HICAR remote: `git@github.com:ofuhrer/HICAR.git`
- Validated engineering branch: `feature/icon_downscaling`
- Current experimental HICAR baseline:
  `5d5574959f5c62feb183d184ab6ef99d2adfce80`
- Available long/costly-run workflow: short segments managed by
  `orchestration/preemptible_campaign.py`
- Retired solver-research source: checksum-bound bundles and manifests under
  `recovery/` and the durable online root; do not recreate active branches or
  scratch worktrees for it.

Use `memory/project-assessment.md` for the current synthesis, decisions, branch
closure, and ranked goals. Exact experiment details belong in concise case
manifests. Do not add dated investigation diaries or general evidence ledgers.

## Skill routing

Project skills live under `.agents/skills/` and apply only when Codex is working in this workspace. Use the smallest relevant skill:

- Project-local `$balfrin-user-environment`: modules, partitions, SSH, scratch, cluster safety.
- Project-local `$icon-balfrin-grib`: ICON archive/FDB discovery and GRIB inspection.
- Project-local `$icon-hicar-forcing`: fieldextra regridding, NetCDF packaging, forcing schema and validation.
- Project-local `$icon-hicar-domain`: static domain, public land data, forcing subdomain, boundary topography relaxation.
- Project-local `$hicar-alpine-configuration`: vertical grid, wind solver, SLEVE, experiment and physical-quality design.
- Project-local `$hicar-balfrin-runtime`: HICAR builds, Slurm execution, CPU/GPU transport, debugging and benchmarking.

Skills contain durable procedures and defaults. `memory/project-assessment.md`
contains the active synthesis and priorities.

## Code discovery

Prefer codebase-memory MCP tools over broad text search:

1. `index_status` for the selected project.
2. `search_graph` for symbols and concepts.
3. `trace_path` for callers/callees/data flow.
4. `get_code_snippet` after resolving an exact symbol.
5. `query_graph` for structural questions.
6. `search_code` for graph-enriched exact text.

Projects:

- `icon_hicar`: root scripts and cross-component workflow.
- `Users-fuhrer-Work-agentic-icon_hicar-HICAR`: HICAR source.
- `Users-fuhrer-Work-agentic-icon_hicar-fieldextra`: fieldextra source.

Fall back to `rg` or direct reads for scripts/configuration, exact strings, or files reported as partial/skipped. Re-index a missing or stale project in full persistent mode before broad discovery.

## Cluster rules

- Use `ssh balfrin`; retry transient refusal twice, then ask the user to restore passwordless access.
- Do not run heavy computation on login nodes or use `balfrin-ln001`.
- Select partitions explicitly: `debug`/`short`/`normal` for GPU; `pp-short`/`pp-long` for CPU/post-processing.
- Use `preemptible` for controller-managed heavy campaign attempts; legacy
  long `normal` jobs are not pre-emption-safe.
- Submit only to reviewed partitions whose live `AllowGroups` contains `ALL`
  or exact group `s83`. Never use supplemental-group access such as `s83opr`
  or `s83disp`; every programmatic `sbatch` must name one explicit partition
  and validate it immediately before submission.
- Initialize the MCH module path in every non-interactive shell and Slurm job.
- Keep large data/builds in `$SCRATCH/icon_hicar`; use `/tmp` only for small transient payloads.
- Use `/store_new/mch/msopr/olifu` for project-owned durable online storage.
  This namespace is authoritative and must not be rewritten to another storage
  prefix.
- Verify live Confluence/Rovo guidance when behavior depends on current operational configuration.

## R&D engineering rules

- Prefer existing repository scripts and conventions.
- Keep changes narrowly scoped and preserve unrelated user work.
- Never store credentials, keys, or tokens.
- Do not compile fieldextra unless explicitly requested; use the verified operational executable for workflow runs.
- Use HICAR release builds for throughput and debug builds for diagnosis.
- Validate on a small representative case before scaling area, resolution, duration, or GPU count.
- Use ready markers only where concurrent readers or shared/published artifacts
  need a completion guarantee: write, validate, atomically rename, then create
  `<file>.ready`.
- For costly or pre-emptible runs, bound node use, retries, and retained data;
  reuse the shared forcing cache and recoverable short-segment controller when
  it helps. Small exploratory jobs need not adopt the full campaign machinery.
- Record the minimal provenance needed to interpret and reproduce each useful
  scientific result. Add checksums and full manifests when artifact identity
  is actually material.

## Memory policy

After each prompt, persist only information that changes future decisions:

- Update a skill when a reusable procedure, invariant, schema, or default changes.
- Update `memory/project-assessment.md` when evidence changes the current
  synthesis, ranked goals, or next step.
- Record exact commits, validated artifacts, and reproducible run details in
  concise case manifests.
- Update this file only for project-wide routing, locations, or operating rules.
- Do not preserve command transcripts, failed guesses, job-by-job narratives, or facts superseded by the current validated state.
