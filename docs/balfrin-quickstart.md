# Balfrin quickstart

This is the supported path from a clean checkout to a planned, bounded
ICON REA-L-CH1 to HICAR qualification attempt. It is intended for an
authorized MeteoSwiss Balfrin user with access to the REA-L-CH1 FDB,
the shared operational fieldextra installation, and the project directory
below `/store_new/mch/msopr/olifu/icon_downscaling`.

The primary execution path is the stateful pre-emptible controller. Legacy
long Slurm chains remain useful as qualification evidence, but they are not
the recommended campaign interface.

> [!IMPORTANT]
> The repository currently carries a scientific hold after the V29 summer
> temperature screen. The commands below may be used through campaign
> planning and dry reconciliation. Do not submit the model watcher until
> `memory/project-state.md` explicitly authorizes the bounded run. A
> qualification definition never authorizes a month, annual, 20-year, or
> 100 m campaign.

## 1. Clone and check access

Work on a supported Balfrin login node, not `balfrin-ln001`:

```bash
cd "$SCRATCH"
git clone --recurse-submodules \
  https://github.com/ofuhrer/icon_downscaling.git
cd icon_downscaling
git submodule update --init --recursive
make balfrin-preflight CHECK_FDB=1

. ./scripts/load_balfrin_site_config.sh
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"
module load python/3.11.7
```

The preflight checks the pinned production HICAR commit, required Slurm
commands, the `preemptible` partition, the module tree, FDB metadata,
fieldextra resources, the ICON grid, writable scratch, and writable durable
storage. It publishes
`$SCRATCH/icon_hicar/onboarding/balfrin_preflight.json.ready` only on PASS.
The Make target loads the supported Python module itself; keep Python 3.11
loaded for the remaining commands.

Shared non-secret defaults are in `config/balfrin.env`. Every primary build,
forcing, model, CPU-worker, and watcher wrapper loads it through
`scripts/load_balfrin_site_config.sh` before initializing modules. A temporary
site replacement can be selected with `HICAR_SITE_CONFIG`; explicit
environment variables take precedence. Select an absolute, stable
`HICAR_SITE_CONFIG` path before preflight so the interactive shell and Slurm
jobs read the same record. Durable project storage must remain below
`/store_new`.

For a real two-field FDB read, run this from a workstation checkout:

```bash
./scripts/balfrin_rea_l_ch1_fdb_smoke.sh fdb/5.19:v2
```

## 2. Restore the bounded static input

The summer REA-L-initialized Swiss domain is checksum-published in the
durable recovery foundation. Restore it to scratch without copying archive
metadata by hand:

```bash
REPO=$(pwd -P)
WORK="$SCRATCH/icon_hicar/onboarding"
STATIC="$WORK/static/domain_static_swiss_200m_rea_l_20200701_0000.nc"

python3 scripts/restore_recovery_artifact.py \
  --plan recovery/archive_plan_foundation_v1.json \
  --item-id static:summer-initialization \
  --output "$STATIC"
```

The restore is idempotent, verifies the archive report and payload SHA-256,
copies atomically, and publishes both `$STATIC.ready` and a restore report.
Routine forcing and output caches are regenerated; they are not recovery
artifacts.

## 3. Build the production HICAR pin

The coordinator pins one engineering production line:
`feature/icon_downscaling` at
`7700c97a0248abcc1db055ef04c22e1ff9ec6d22`. This tip contains the qualified
V26 restart state and the selectively validated SCHNAPS fixes. V29 remains a
separate failed scientific evidence branch.

Build the exact pinned commit with the canonical GPU/NCCL builder:

```bash
HICAR_COMMIT=$(git -C HICAR rev-parse HEAD)
BUILD="$WORK/build/hicar-${HICAR_COMMIT:0:12}-gpu-nccl"

sbatch --wait --no-requeue \
  --export=ALL,HICAR_COORDINATOR_ROOT="$REPO",HICAR_SOURCE_ROOT="$REPO/HICAR",HICAR_BUILD_ROOT="$BUILD",HICAR_EXPECTED_COMMIT="$HICAR_COMMIT",HICAR_BUILD_VARIANT=gpu-nccl,HICAR_BUILD_MODE=release \
  case_studies/swiss_200m/scripts/build_hicar_balfrin.sbatch

test -f "$BUILD/hicar_build_provenance.txt.ready"
```

The build fails on a dirty or wrong source checkout and publishes provenance
only after the executable, tester, linkage, and NCCL checks pass.

## 4. Snapshot the campaign runtime

Keep developing the repository normally. Immediately before launching a
campaign, copy its small set of executable scripts and configuration into a
read-only campaign runtime:

```bash
COORDINATOR_COMMIT=$(git rev-parse HEAD)
RELEASE="$WORK/runtime/coordinator-${COORDINATOR_COMMIT:0:12}"

python3 orchestration/prepare_runtime_release.py \
  --source-root "$REPO" \
  --output-root "$RELEASE" \
  --purpose production
```

This is a per-campaign execution snapshot, not a project release. It contains
the controller, launch scripts, validators, namelist template, target grid,
and site defaults needed to make retries reproducible while development
continues on `main`.

Build a release-specific, read-only Python environment:

```bash
sbatch --wait --no-requeue \
  --export=ALL,HICAR_RUNTIME_RELEASE="$RELEASE" \
  "$RELEASE/case_studies/swiss_200m/scripts/bootstrap_preemptible_python_balfrin.sbatch"

RELEASE_ID=$(basename "$RELEASE")
PYTHON_REPORT="$SCRATCH/icon_hicar/runtime/python/${RELEASE_ID}.environment.json"
PYTHON=$(
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["python"])' \
    "$PYTHON_REPORT"
)
```

The controller rechecks the interpreter hash, read-only environment tree,
exact `pip freeze` inventory, requirements, and runtime binding on every
reconciliation.

## 5. Create and inspect one two-hour campaign

The helper below refuses an unready static file, a dirty/wrong HICAR checkout,
an unqualified build publication, a dirty runtime release, or a changed
Python environment:

```bash
CAMPAIGN="$WORK/campaigns/swiss-200m-smoke"
DEFINITION="$CAMPAIGN/definition.json"
PLAN="$CAMPAIGN/campaign_plan.json"

python3 scripts/create_balfrin_smoke_campaign.py \
  --campaign-root "$CAMPAIGN" \
  --runtime-manifest "$RELEASE/runtime_release.json" \
  --python-report "$PYTHON_REPORT" \
  --hicar-root "$REPO/HICAR" \
  --build-root "$BUILD" \
  --static-file "$STATIC" \
  --start 2020-07-01T00:00:00 \
  --hours 2 \
  --segment-hours 1 \
  --output-profile routine \
  --output "$DEFINITION"

"$PYTHON" "$RELEASE/orchestration/prepare_preemptible_campaign.py" \
  --definition "$DEFINITION" \
  --output "$PLAN" \
  --repo-root "$RELEASE"

"$PYTHON" "$RELEASE/orchestration/preemptible_campaign.py" reconcile \
  --campaign "$PLAN" \
  --repo-root "$RELEASE"
```

The `routine` profile deliberately keeps this engineering recovery drill
independent of scientific diagnostic-output qualification. The final command
is a dry reconciliation: it creates pristine controller
state and prints intended actions without submitting jobs. The generated
definition is bounded to one four-node chain, two one-hour segments, one model
slot, one CPU slot, and six attempts. The extra attempt budget accommodates
genuine scheduler pre-emption in addition to the drill's two controlled
cancellations. It uses `preemptible` for HICAR and `pp-short` for bounded
forcing/post-processing.

Submit the engineering-only target-stack recovery drill against a fresh plan:

```bash
REPORT="$CAMPAIGN/hicar_preemptible_recovery.json"
sbatch --no-requeue \
  --export=ALL,REPO_ROOT="$RELEASE",HICAR_VALIDATION_PYTHON="$PYTHON",HICAR_CAMPAIGN_PLAN="$PLAN",HICAR_RECOVERY_QUALIFICATION_REPORT="$REPORT" \
  "$RELEASE/case_studies/swiss_200m/scripts/qualify_hicar_preemptible_recovery_balfrin.sbatch"
```

It completes the first segment, sends SIGTERM and then SIGKILL to two real
HICAR continuation attempts after `srun` starts producing output, and requires
a third immutable attempt to complete from the exact predecessor restart.
This qualifies recovery engineering only.

## 6. Launch only when the scientific state authorizes it

After reviewing the plan and current project state, the actual submission
command is:

```bash
sbatch --no-requeue \
  --export=ALL,REPO_ROOT="$RELEASE",HICAR_VALIDATION_PYTHON="$PYTHON",HICAR_CAMPAIGN_PLAN="$PLAN" \
  "$RELEASE/case_studies/swiss_200m/scripts/watch_preemptible_campaign_balfrin.sbatch"
```

The watcher keeps retry state outside Slurm, creates immutable attempt
directories, and advances only from validator-published completion markers.
Slurm requeue is never treated as resume. A hard kill discards the interrupted
attempt and retries from the last already-published checkpoint.

Inspect without changing capacity:

```bash
"$PYTHON" "$RELEASE/orchestration/preemptible_campaign.py" status \
  --campaign "$PLAN"
```

Pause all new submissions before an intentional cancellation:

```bash
"$PYTHON" "$RELEASE/orchestration/preemptible_campaign.py" set-capacity \
  --campaign "$PLAN" --models 0 --cpus 0
```

See `orchestration/README.md` for recovery semantics, lifecycle retirement,
multi-chain authorization, and production gates.
