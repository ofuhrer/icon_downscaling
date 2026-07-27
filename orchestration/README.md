# Pre-emptible Balfrin campaign controller

This directory contains the stateful controller for short, restart-linked
HICAR attempts on Balfrin. It does not alter HICAR checkpoint serialization or
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
exit 75 are retryable only within the configured attempt budget; scientific
or unexplained model failures block the campaign.

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

## Planning and launch

Definitions use absolute Balfrin paths:

```json
{
  "schema_version": 1,
  "purpose": "qualification",
  "campaign_id": "swiss-200m-example",
  "campaign_root": "/scratch/USER/icon_hicar/campaigns/example",
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
    "max_cpu_attempts": 3
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

Multiple chains additionally require a published
`independent_chain_authorization`. A definition with `"purpose":
"production"` also requires a checksum-frozen annual assessment whose
decision is `GO_20_YEAR_200M_PRODUCTION`. The planner and controller recheck
those publications and hashes. This prevents the orchestration mechanism from
bypassing the current scientific hold.

Prepare, inspect without submission, then launch the lightweight watcher:

```bash
python orchestration/prepare_preemptible_campaign.py \
  --definition /absolute/path/campaign_definition.json \
  --output /absolute/path/campaign_plan.json \
  --repo-root /absolute/path/icon_hicar

python orchestration/preemptible_campaign.py reconcile \
  --campaign /absolute/path/campaign_plan.json \
  --repo-root /absolute/path/icon_hicar

sbatch --no-requeue \
  --export=ALL,REPO_ROOT=/absolute/path/icon_hicar,HICAR_CAMPAIGN_PLAN=/absolute/path/campaign_plan.json \
  case_studies/swiss_200m/scripts/watch_preemptible_campaign_balfrin.sbatch
```

The watcher runs on `pp-long` and submits one `afterany` successor before it
starts reconciling. A successor exits without chaining again when the
campaign is complete or blocked, so multi-day campaigns retain a lightweight
external reconciler without relying on Slurm requeue.

## Completion and storage

Every model output file listed by every successful attempt is compressed and
validated; the earlier month workflow's first-file-only compression shortcut
is not used. Campaign completion is published only after:

- every forcing and model publication is PASS;
- every segment's solver audit is PASS;
- every listed compressed file is published PASS; and
- each chain's concatenated output times are exact, unique, ordered, and
  gap-free from the declared start through end.

The resulting `campaign_completion.json.ready` binds the segment completion,
solver, and compression reports by SHA-256. Source output, forcing, and
restart retirement are intentionally not automatic in this first
pre-emptible controller. They remain governed by the existing hash-checked
retirement tools and the approved archive contract; capacity planning must
allow for the rolling workspace until that destructive lifecycle is integrated
and interruption-tested.
