# Contributing

This repository controls scientific workflow and promotion decisions, so a
change is complete only when its affected contract has been validated.

## Before editing

1. Read `AGENTS.md` and `memory/project-state.md`.
2. Select the smallest relevant procedure from `.agents/skills/`.
3. Check both the outer worktree and the affected external submodule for
   unrelated changes.
4. Coordinate ownership before editing files used by a live Slurm chain.

Never cancel, replace, or mutate a live campaign merely to simplify local
development.

## Change boundaries

- Reusable multi-case logic belongs in `scripts/`.
- Case-specific configuration, launchers, and scientific gates stay in their
  case directory.
- HICAR changes belong in the HICAR repository. Commit and push them there
  before updating the outer submodule pointer.
- fieldextra is an optional private, commit-locked source reference; normal
  workflow work must not compile or modify it.
- Large data and generated runtime products must not be committed.

Preserve unrelated work in every worktree. Avoid broad formatting and staging
operations when another task is active.

## Validation

Run:

```bash
make check
make test
```

When a change depends on active HICAR source development, also run:

```bash
make test-hicar-contract
```

The source-contract tests may lead the pinned production HICAR revision during
development. Do not advance the public gitlink merely to make them pass: the
matching HICAR commit must first be pushed and complete its own qualification.

Then run the smallest relevant scientific or target-stack regression:

- forcing changes: schema, level order, source identity, and coverage;
- domain changes: static schema, forcing coverage, boundary relaxation, and
  constructed vertical geometry;
- configuration changes: representative short run plus numerical and physical
  gates;
- runtime changes: local tests followed by the relevant Balfrin CPU/GPU,
  transport, output, and benchmark gates;
- promotion changes: focused negative fixtures proving the gate fails closed.

Document validated source paths, commits, options, checksums, and results in a
case manifest. Record only future-relevant milestone changes in project state.

## Commits and reviews

Keep external-source commits separate from the coordinating gitlink update.
Reviewers should be able to distinguish:

1. the scientific or workflow change;
2. its regression evidence; and
3. any external revision advance.

A ready marker is created last and means the corresponding payload has been
fully written and validated.
