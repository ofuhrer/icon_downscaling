# Project: ICON-to-HICAR Alpine downscaling

## Objective

Develop and operate a reproducible workflow that dynamically downscales MeteoSwiss ICON output at roughly 1 km to 100-250 m with HICAR over an arbitrary Alpine domain, from small study areas up to Switzerland plus a boundary margin.

The workflow must cover source-data discovery, structured forcing conversion, static-domain creation, numerically stable HICAR configuration, Balfrin execution, and physical/output validation.

## Workspace

- Coordinating repository: `/Users/fuhrer/Work/agentic/icon_hicar`
- Coordinating remote: `git@github.com:ofuhrer/icon_downscaling.git`
- HICAR fork: `/Users/fuhrer/Work/agentic/icon_hicar/HICAR`
- Fieldextra source reference: `/Users/fuhrer/Work/agentic/icon_hicar/fieldextra`
- Balfrin project root: `$SCRATCH/icon_hicar`
- Durable online root: `/store_new/mch/msopr/olifu/icon_downscaling`
- HICAR remote: `git@github.com:ofuhrer/HICAR.git`
- Production-performance branch: `feature/icon_downscaling`
- Retired solver-research source: checksum-bound bundles and manifests under
  `recovery/` and the durable online root; do not recreate active branches or
  scratch worktrees for it.

Use `memory/project-state.md` for current milestones, validated artifacts, and unresolved constraints. Do not add dated investigation diaries.

## Skill routing

Project skills live under `.agents/skills/` and apply only when Codex is working in this workspace. Use the smallest relevant skill:

- Project-local `$balfrin-user-environment`: modules, partitions, SSH, scratch, cluster safety.
- Project-local `$icon-balfrin-grib`: ICON archive/FDB discovery and GRIB inspection.
- Project-local `$icon-hicar-forcing`: fieldextra regridding, NetCDF packaging, forcing schema and validation.
- Project-local `$icon-hicar-domain`: static domain, public land data, forcing subdomain, boundary topography relaxation.
- Project-local `$hicar-alpine-configuration`: vertical grid, wind solver, SLEVE, experiment and physical-quality design.
- Project-local `$hicar-balfrin-runtime`: HICAR builds, Slurm execution, CPU/GPU transport, debugging and benchmarking.

Skills contain durable procedures and defaults. `memory/project-state.md` contains only mutable project state.

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
- Initialize the MCH module path in every non-interactive shell and Slurm job.
- Keep large data/builds in `$SCRATCH/icon_hicar`; use `/tmp` only for small transient payloads.
- Use `/store_new/mch/msopr/olifu` for project-owned durable online storage.
  This namespace is authoritative and must not be rewritten to another storage
  prefix.
- Verify live Confluence/Rovo guidance when behavior depends on current operational configuration.

## Engineering rules

- Prefer existing repository scripts and conventions.
- Keep changes narrowly scoped and preserve unrelated user work.
- Never store credentials, keys, or tokens.
- Do not compile fieldextra unless explicitly requested; use the verified operational executable for workflow runs.
- Use HICAR release builds for throughput and debug builds for diagnosis.
- Validate on a small representative case before scaling area, resolution, duration, or GPU count.
- Treat ready markers as publication guarantees: write, validate, atomically rename, then create `<file>.ready`.
- Record reproducible source paths, options, checksums, and validation results in case manifests rather than chat-style memory.

## Memory policy

After each prompt, persist only information that changes future decisions:

- Update a skill when a reusable procedure, invariant, schema, or default changes.
- Update `memory/project-state.md` for current commits, validated artifacts, blockers, or next milestones.
- Update this file only for project-wide routing, locations, or operating rules.
- Do not preserve command transcripts, failed guesses, job-by-job narratives, or facts superseded by the current validated state.
