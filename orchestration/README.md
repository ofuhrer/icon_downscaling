# Campaign orchestration boundary

This directory reserves the architectural boundary for a future stateful
retry/lease controller. It contains no active campaign controller today.

The current seven-day and other long Slurm chains are **not pre-emption-safe**:
they do not install scheduler signal/requeue handling, and fixed run
directories intentionally reject partial products. Slurm requeue is not
equivalent to model resume, and operators must not replay a pre-empted segment
in place.

Balfrin's `preemptible` partition currently uses `PreemptMode=CANCEL` with
`SIGTERM` and 60 seconds of grace; `lowprio` has priority zero with pre-emption
off. A global `JobRequeue=1` setting does not make either path restart-safe.
Any eventual signal handler must exit non-zero unless a validated
segment-completion marker already exists, otherwise `afterok` may release an
incomplete successor.

Safe-stop on `SIGTERM` is best effort, not a guarantee. A restart is roughly
42 GB at 200 m and about four times larger at 100 m, so the grace period cannot
be assumed sufficient to publish a new checkpoint. Hard-kill recovery must
fall back to the last already-closed, validator-published checkpoint.

A future controller belongs here only if it:

- uses durable segment and immutable attempt identities;
- stores retry state outside a job or submitted DAG;
- acquires an exclusive, expiring lease before submission;
- keeps one chain sequential while scheduling independent chains against
  available capacity;
- reconciles Slurm state with checksum-bound publication state;
- retries only classified infrastructure interruptions within a bounded
  policy;
- selects only validator-published, provenance-bound restart checkpoints;
- makes publication and retirement idempotent across termination at every
  boundary;
- preserves scientific and model failures as terminal evidence.

Case-specific Slurm jobs stay beside their case. Scientific validators stay in
the case validation directory. HICAR runtime logic stays in HICAR. The
controller coordinates those interfaces; it does not replace them or weaken
their gates.
