# Contributing

This repository exists to produce a scientifically useful downscaling
capability. A change is complete when it makes the intended behavior work and
provides evidence proportionate to its risk.

## Before editing

1. Read `AGENTS.md` and `memory/project-assessment.md`; consult the legacy
   state ledger only for evidence relevant to the selected goal.
2. Select the smallest relevant procedure from `.agents/skills/`.
3. Check both the outer worktree and the affected external submodule for
   unrelated changes.
4. Coordinate ownership before editing files used by a live Slurm chain.

Never cancel, replace, or mutate a live campaign merely to simplify local
development.

## Change boundaries

- Reusable multi-case logic belongs in `scripts/`.
- Case-specific configuration, launchers, and scientific validators stay in their
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

The source-contract tests may lead the coordinator's recorded HICAR revision during
development. Do not advance the public gitlink merely to make them pass: the
matching HICAR commit must first be pushed and complete the validation needed
for its intended use.

Then run the smallest relevant regression needed to trust the change. The
following are risk-based examples, not a fixed promotion ladder:

- forcing changes: schema, level order, source identity, and coverage;
- domain changes: static schema, forcing coverage, boundary relaxation, and
  constructed vertical geometry;
- configuration changes: representative short run plus relevant numerical and
  physical evidence;
- runtime changes: local tests followed by the relevant Balfrin CPU/GPU,
  transport, output, and benchmark gates;
- guardrail changes: focused negative fixtures proving the material risk still
  fails safely.

Record provenance proportionate to the inference or engineering risk. Shared,
costly, or production-candidate artifacts normally need a case manifest and
checksums; a small exploratory change may need only its source revision,
configuration delta, case, and key evidence. Update the assessment only when
the evidence changes current scientific understanding or the next question.

## Commits and reviews

Keep external-source commits separate from the coordinating gitlink update.
Reviewers should be able to distinguish:

1. the scientific or workflow change;
2. its regression evidence; and
3. any external revision advance.

A ready marker is created last and means the corresponding payload has been
fully written and validated.
