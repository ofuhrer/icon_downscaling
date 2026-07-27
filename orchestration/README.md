# Pre-emptible Balfrin campaign controller

This directory contains the primary stateful controller for short,
restart-linked HICAR attempts on Balfrin. It does not alter HICAR checkpoint serialization or
claim that the current restart-physics candidate is scientifically qualified.
Qualification and production authorization remain independent gates.

## Recovery contract

`prepare_preemptible_campaign.py` splits each restart chain into immutable
segments of at most 24 simulation hours. A segment can be retried many times,
but every retry receives a new attempt ID and a new run/restart directory.
Only a `model_chunk_completion.json.ready` publication can advance the chain.
The successor stages the exact restart path and verifies its SHA-256 against
that predecessor publication before HICAR starts.

Heavy model submissions always override the legacy runner defaults with:

- `--partition=preemptible` and `--no-requeue`;
- a wall-time between 10 minutes and 6 hours;
- `--signal=B:USR1@300` for a controlled stop before wall-time; and
- the partition's cancellation-time `SIGTERM` path.

`preemption.py` records `attempt_interrupted.json`, forwards termination to the
whole `srun` process group, and exits 75 unless model completion was already
published. Balfrin's 60-second cancellation grace is not assumed sufficient
to write a roughly 42 GB restart. The reliable recovery path therefore
discards the interrupted immutable attempt and reruns that short segment from
the last already-published predecessor checkpoint. The signal is useful for
classification and clean process shutdown; it is not presented as a new
checkpoint facility.

The controller state is outside Slurm. Reconciliation uses an exclusive
filesystem lock, atomic state updates, stable job names, pre-submission
intent, and atomic submission receipts. A controller crash between `sbatch`
and receipt publication is reconciled by job name before the same intent may
be submitted again. Scheduler `COMPLETED` without a validated ready marker is
a failure, not success. `PREEMPTED`, `CANCELLED`, `NODE_FAIL`, `BOOT_FAIL`, or
exit 75 are retryable only within the configured attempt budget. A true hard
kill appears on Balfrin as `FAILED` with Slurm exit status `0:9`; `SIGKILL`
and externally delivered `SIGTERM` are also retryable without requiring a
signal-time report. `OUT_OF_MEMORY`, `TIMEOUT`, application exit failures,
scientific failures, and unexplained failures still block the campaign.

## Elastic capacity

One restart chain remains sequential. Independently authorized chains may run
concurrently. The planner derives the maximum model slots from a global
44-node budget:

```text
200 m: 4 nodes/attempt  -> at most 11 model attempts
100 m: 16 nodes/attempt -> at most 2 model attempts
```

Pending attempts count against the limit, so the controller does not build an
unbounded queue when Balfrin is full. When capacity becomes available Slurm
can start all queued low-priority model attempts up to that limit. Operators
can adjust the limit without changing the immutable campaign plan:

```bash
python orchestration/preemptible_campaign.py set-capacity \
  --campaign /absolute/path/campaign_plan.json --models 6 --cpus 2
```

Setting either value to zero pauses new submissions in that pool. Set both to
zero before intentionally cancelling active jobs.

Forcing, finalization, solver audits, and compression use one campaign-wide
`pp-short` array with at most two active tasks (`%2`). Heavy HICAR is the only
stage initially placed in `preemptible`; post-processing stays on the bounded
CPU partition.

## Per-campaign execution snapshot

The repository and its normal development environment remain mutable. At
launch, copy only the scripts and configuration used by that campaign into a
read-only execution directory with `prepare_runtime_release.py`. This small
snapshot exists so a retry six months later does not silently use newer code;
it is not a project release or a constraint on continued development.

The runtime Python environment is a separate publication. Submit
`bootstrap_preemptible_python_balfrin.sbatch` on `pp-short` against the
deployed release. It installs the exact direct versions in
`requirements/balfrin-preemptible.txt`, runs import and package checks, and
publishes a report binding the interpreter hash, exact resolved package
inventory, requirements, immutable environment tree, and runtime release.
Each campaign snapshot gets a separate environment by default. The planner
and controller reject an environment that has subsequently changed. Model and
CPU jobs receive that interpreter path explicitly rather than relying on
shell activation.

## Planning and launch

Definitions use absolute Balfrin paths:

```json
{
  "schema_version": 1,
  "purpose": "qualification",
  "campaign_id": "swiss-200m-example",
  "campaign_root": "/scratch/USER/icon_hicar/campaigns/example",
  "runtime_release": "/scratch/USER/icon_hicar/runtime/releases/release-id/runtime_release.json",
  "python_environment": "/scratch/USER/icon_hicar/runtime/python/release-id.environment.json",
  "model": {
    "expected_hicar_commit": "0000000000000000000000000000000000000000",
    "case_root": "/scratch/USER/icon_hicar/case_studies/swiss_200m",
    "hicar_root": "/scratch/USER/icon_hicar/HICAR",
    "static_file": "/scratch/USER/icon_hicar/case_studies/swiss_200m/static/domain.nc",
    "nodes": 4,
    "time_limit": "06:00:00",
    "output_profile": "routine",
    "output_interval_seconds": 3600
  },
  "policy": {
    "segment_hours": 24,
    "model_node_budget": 44,
    "model_slots": 11,
    "cpu_slots": 2,
    "prefetch_segments_per_chain": 1,
    "max_model_attempts": 5,
    "max_cpu_attempts": 3,
    "rolling_retirement": true,
    "preserve_restart_every_segments": 30,
    "max_unretired_segments_per_chain": 2
  },
  "chains": [
    {
      "chain_id": "year-2000",
      "start": "2000-01-01T00:00:00",
      "end": "2001-01-01T00:00:00"
    }
  ]
}
```

For the supported single-chain two-hour definition, use
`scripts/create_balfrin_smoke_campaign.py` as shown in
[`docs/balfrin-quickstart.md`](../docs/balfrin-quickstart.md). The hand-written
example above documents the complete schema; it is not the recommended first
entry point.

Multiple chains additionally require a published
`independent_chain_authorization`. Its schema-v1 `scope` must exactly bind the
campaign ID, HICAR commit, static initialization path, chain count, chain IDs,
start/end timestamps, and per-chain land-initialization choice; its
`scope_sha256` is the SHA-256 of the sorted, indented JSON scope plus a final
newline. Both planning and every reconciliation reject an authorization for
another campaign. A definition with `"purpose": "production"` also requires a
checksum-frozen annual assessment whose
decision is `GO_20_YEAR_200M_PRODUCTION`. The planner and controller recheck
those publications and hashes. This prevents the orchestration mechanism from
bypassing the current scientific hold.

Prepare, inspect without submission, then launch the lightweight watcher:

```bash
RELEASE=/absolute/path/to/immutable/runtime-release
PYTHON=/absolute/path/to/published/venv/bin/python

"$PYTHON" "$RELEASE/orchestration/prepare_preemptible_campaign.py" \
  --definition /absolute/path/campaign_definition.json \
  --output /absolute/path/campaign_plan.json \
  --repo-root "$RELEASE"

"$PYTHON" "$RELEASE/orchestration/preemptible_campaign.py" reconcile \
  --campaign /absolute/path/campaign_plan.json \
  --repo-root "$RELEASE"

sbatch --no-requeue \
  --export=ALL,REPO_ROOT="$RELEASE",HICAR_VALIDATION_PYTHON="$PYTHON",HICAR_CAMPAIGN_PLAN=/absolute/path/campaign_plan.json \
  "$RELEASE/case_studies/swiss_200m/scripts/watch_preemptible_campaign_balfrin.sbatch"
```

The watcher runs on `pp-long` and submits one `afterany` successor before it
starts reconciling. A successor exits without chaining again when the
campaign is complete or blocked, so multi-day campaigns retain a lightweight
external reconciler without relying on Slurm requeue.

## Completion and storage

Every model output file listed by every successful attempt is compressed and
validated; the earlier month workflow's first-file-only compression shortcut
is not used. A journal is written before deletion begins, so a killed
retirement task can resume when some targets are already absent. After
compression and solver validation the lifecycle worker:

- removes verified raw history and forcing payloads;
- removes unpublished failed-attempt directories;
- withdraws forcing ready markers before deleting forcing;
- retires a restart only after the adjacent successor is published and
  solver-valid; and
- preserves the final restart plus the configured periodic checkpoints.

The controller gives compression and retirement priority over additional
forcing production and stops a chain from advancing when its configured
unretired-segment backlog is full. Campaign completion is published only
after:

- every model publication is PASS;
- every segment's solver audit is PASS;
- every listed compressed file is published PASS; and
- every segment and restart retirement publication is PASS; and
- each chain's concatenated output times are exact, unique, ordered, and
  gap-free from the declared start through end.

The resulting `campaign_completion.json.ready` binds the segment completion,
solver, compression, segment-retirement, and restart-retirement reports by
SHA-256. Durable transfer remains governed by the separately approved archive
contract; rolling scratch retirement is not durable archiving.

## Engineering cancellation qualification

`qualify_preemptible_recovery.py` runs a one-node sleep probe, never HICAR. It
verifies the real Balfrin paths for a graceful `SIGTERM`, a true `SIGKILL`
without signal-time cleanup, and creation of a third immutable retry. It
pauses campaign capacity and cancels the final probe job before publishing
`preemption_recovery_engineering.json.ready`.

This report is always `ENGINEERING_ONLY`, `promotion_eligible=false`, and
`scientific_authorization=false`. It qualifies scheduler/controller recovery;
it cannot qualify HICAR restart physics or authorize a scientific campaign.

## Real HICAR recovery qualification

The scheduler-only sleep probe is necessary but not sufficient. Before the
first campaign, prepare a fresh two-hour qualification definition with
`--segment-hours 1`, turn it into a campaign plan, and submit:

```bash
sbatch --no-requeue \
  --export=ALL,REPO_ROOT="$RELEASE",HICAR_VALIDATION_PYTHON="$PYTHON",HICAR_CAMPAIGN_PLAN="$PLAN",HICAR_RECOVERY_QUALIFICATION_REPORT="$CAMPAIGN/hicar_preemptible_recovery.json" \
  "$RELEASE/case_studies/swiss_200m/scripts/qualify_hicar_preemptible_recovery_balfrin.sbatch"
```

The first one-hour HICAR segment must complete and publish its restart. The
qualifier sends SIGTERM to the first continuation attempt and SIGKILL to the
second only after each real `srun` has produced model output. A third immutable
attempt must complete. Its model completion must bind the exact predecessor
restart path, restart SHA-256, predecessor publication path, and publication
SHA-256 before the engineering report can pass.

This bounded report is also `ENGINEERING_ONLY`; it uses the `routine` output
profile to qualify the real HICAR/srun/restart mechanism without conflating
that with scientific diagnostic-output qualification. It does not supersede
restart-equivalence, seasonal, annual, or production authorization.
