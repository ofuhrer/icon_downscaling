# Contributing

Read `AGENTS.md` and `memory/project-assessment.md`, preserve unrelated user
work, and use the smallest relevant project skill.

Keep reusable preprocessing in `preprocessing/` or `scripts/`, Swiss-case
configuration and analysis in its case directory, and HICAR source changes in
the HICAR repository. Generated model data stays outside Git.

Validate changes in proportion to their scientific risk:

- input changes: schema, units, level order, exact grid, and a short pilot;
- namelist/physics changes: a controlled representative A/B;
- runtime/restart changes: focused tests plus a segmented target-stack run;
- analysis changes: synthetic exact-match and failure fixtures.

Run `make check` and `make test`. Record only the source revision,
configuration difference, interval, and evidence needed to reproduce and
interpret a useful experiment.
