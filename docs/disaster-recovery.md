# Disaster recovery and clean-room rebuild

This document defines the evidence required before deleting both a development
workspace and the Balfrin scratch tree. GitHub preserves coordinator source,
but source alone does not preserve restricted data access, active external
changes, large scientific inputs, or qualification artifacts.

## Deletion gate

Do not delete the last local or Balfrin copy until all of these conditions
hold:

1. The coordinating repository has no local-only commits or modifications,
   and its selected release commit is reachable from GitHub.
2. Every intended HICAR change is committed and pushed. The coordinator pins
   the qualified, reachable HICAR commit, and the HICAR worktree has no
   uncommitted or local-only branch history. Qualification and failed-candidate
   commits that must remain auditable may instead be protected by a verified
   Git bundle in durable storage. Record every such commit and its protection
   in `recovery/rebuild_inventory.json`.
3. The locked fieldextra source commit is reachable by an authorized account,
   and every operational fieldextra executable used by a retained case has a
   version and checksum in that case's provenance.
4. `case_studies/swiss_200m/config/production_archive_contract.json` names an
   approved durable destination, owner, quota, retention policy, measured
   transfer rate, and successful restore/readback drill.
5. Durable inventory manifests record storage locations, sizes, and SHA-256
   hashes for every artifact that cannot be regenerated reliably:

   - qualified static domains and their public-data source identities;
   - selected forcing or complete source requests when the source archive is
     guaranteed to remain available;
   - minimal golden inputs and outputs for a physical regression;
   - validator-published restart checkpoints needed for continuity;
   - canonical report and ready-marker hashes, `model_runs` manifests,
     `source_commit.txt`, executable SHA-256, and module/build-script identity;
   - compressed scientific outputs and their validation reports;
   - exact fieldextra source/executable/configuration identity;
   - observation/reference products whose upstream retrieval may change.

6. Tagged coordinator and HICAR releases identify the recoverable milestone.
   A second durable Git mirror or bundle protects against account or hosting
   loss.
7. A clean-room drill has rebuilt and validated the project from an empty
   checkout and empty scratch directory.

`make recovery-audit` checks the source and archive-contract portions of this
gate. It is intentionally conservative and does not prove that an external
archive payload is complete.

Routine forcing caches, raw history that has an accepted compressed
publication, and ordinary build directories are regenerable scratch classes.
They need not be archived merely to make a multi-terabyte scratch tree
persistent. Their source requests, exact transformation identities, selected
scientific products, and qualification evidence do need durable protection.

## Recovery foundation archive

The current compact recovery foundation is rooted at:

```text
/store_new/mch/msopr/olifu/icon_downscaling/recovery/v1
```

It is deliberately separate from `$SCRATCH`.
`/store_new/mch/msopr/olifu` is the permanent, authoritative project-owned
online namespace. Durable locators must not be rewritten to another storage
prefix. The machine-readable authority is `recovery/storage_policy.json`.

The immutable v1 archive plan contains an earlier provenance sentence about a
possible namespace change. Its bytes cannot be edited without invalidating the
published plan hash; the storage policy supersedes that sentence only. All
payload, manifest, and checksum identities remain unchanged.

The foundation consists of:

- `source/source-protection-manifest-v1.json`, checksum, and ready marker for
  the verified HICAR bundles and exported working-tree patch;
- `manifests/archive-foundation-v1.json` and ready marker for the selected
  static domains, land initialization, conversion/reference identities, and
  exact v1/v2 failed restart-comparison artifacts;
- `manifests/archive-qualification-reports-v1.json` and ready marker for the
  canonical v1/v2/v3 failed restart reports;
- checksum-bound `*.readback.json` reports proving an independent full
  post-publication read of both archives;
- immutable per-file archive reports and ready markers below `artifacts/`.

Small canonical qualification reports also remain in the GitHub coordinator
history. The durable copies retain the deliberate scientific fact that these
failed reports did not publish qualification ready markers; the archive's own
ready markers assert byte-complete retention, not scientific qualification.

The HICAR bundles are audit records, not qualification claims. In particular,
the v2 candidate is a failed national comparison and
`518cc10bac88e1eae9acec6329f866516c7f4dd0` is classified
`UNQUALIFIED_BRIDGE_FAIL`.

The current clean handoff adds two directly reachable HICAR refs:

- `codex/restart-noahmp-state-v26` at `246c8992...` is the qualified bounded
  restart baseline;
- `codex/v29-summer-warm-bias` at `5da4b198...` is explicitly unqualified
  after the frozen summer temperature gate failed.

The latter also remains protected by the verified
`hicar-water-budget-v29-5da4b198.bundle`. Its selected 25-record summer
history, exact configuration/log, and independent scientific reports are
published in
`manifests/archive-v29-summer-handoff-v1.json` (SHA-256
`e32284b286ef7d2f482b57412ec912ad4f5764a5bc4ff5680a4bdc9336c9aedc`).
Independent readback job `4950163` verified all 13 files and
16,349,047,095 bytes. The three large restart payloads are intentionally not
part of that handoff because the failed summer gate authorizes no
continuation.

Cleanup also found an unclassified dirty patch in the legacy Balfrin root
HICAR checkout. It is preserved, rather than silently discarded, as
`source/hicar-balfrin-root-dirty-after-86c9a87e.patch` with SHA-256
`dbec40233ff87cdae0b02c55d63c61019fcc84982344aa7d0cdbb36aba1a5a8e`.
Manifest `archive-legacy-balfrin-root-patch-v1.json` (SHA-256
`d1c6c308e62163c03d5e7022ee0eb34c94c456a777ffe2f009921ddf72380b40`)
and independent readback job `4950168` pass. This is legacy non-promoting
evidence; do not treat it as a candidate branch.

The source-controlled selections are
`recovery/archive_plan_foundation_v1.json` and
`recovery/archive_plan_qualification_reports_v1.json`. Publish them only
through `scripts/archive_recovery_plan_balfrin.sbatch`; the publisher validates
source identity, copies through a partial name, hashes the copy again, and
creates ready markers last. On Balfrin, independently verify every published
byte with:

```bash
module use /mch-environment/v8/modules
module load python/3.11.7
make recovery-archive-verify
```

The completed selective scratch retirement is recorded in
`recovery/cleanup_handoff_v29.json`. It is not a full-deletion authorization:
it proves the V26/V29 source protection, V29 selected-history readback,
retired branch set, exact cleanup jobs, and post-cleanup retained classes.
The retained Swiss forcing/static/source inputs remain in scratch because
they are expensive and still useful. Run `make recovery-audit` on Balfrin,
where `/store_new` is mounted, before ever deleting those last copies; the
unresolved annual archive contract must continue to make that audit fail
closed.

Routine forcing and model-output caches remain intentionally excluded. The
authoritative REA-L fields remain in institutional FDB; the archive instead
preserves the exact fieldextra executable/configuration identities, ICON grid,
static and land-initialization payloads, public OGD reference inputs, and
minimal failed-qualification trajectories needed to reproduce the current
scientific conclusions.

## Access that cannot be stored in Git

Record the responsible owner and renewal procedure, but never credentials, for:

- GitHub access to the HICAR fork and private COSMO-ORG fieldextra source;
- the signed key and account required for `ssh balfrin`;
- MeteoSwiss FDB/ICON/REA-L data authorization;
- observation and public-data services used by validation;
- the approved durable archive namespace.

A source request is reproducible only while the source service retains the
requested records and the account remains authorized. Archive irreplaceable
inputs even when their extraction scripts are versioned.

## Clean-room rebuild

From a new workstation:

```bash
git clone --recurse-submodules \
  https://github.com/ofuhrer/icon_downscaling.git
cd icon_downscaling
./scripts/bootstrap_externals.sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements/dev.txt
make check
make test
```

An authorized developer may add the locked fieldextra reference:

```bash
./scripts/bootstrap_externals.sh --with-fieldextra
```

On Balfrin:

1. Verify the current module tree and cluster policy before relying on the
   versions recorded in `.agents/skills/`.
2. Clone the same coordinator release into `$SCRATCH/icon_hicar` and
   initialize the pinned HICAR submodule.
3. Restore archived artifacts into a new campaign root and verify every
   checksum before creating or accepting ready markers.
4. Build HICAR with the recorded CPU and GPU module stacks.
5. Run the four-rank halo test, a five-minute smoke, and a one-hour physical
   regression before restoring a national or long-duration campaign.
6. Re-run the case-specific geometry, forcing, restart, numerical, physical,
   and observational gates. Historical scheduler success is not a substitute
   for current validation.

The module and launcher procedures are in
`.agents/skills/balfrin-user-environment/` and
`.agents/skills/hicar-balfrin-runtime/`. Case manifests remain the authority
for scientific inputs and acceptance thresholds.

## Restore drill acceptance

A restore drill passes only when:

- all Git revisions and executable hashes match the selected release;
- archived payload hashes match their inventory;
- every candidate HICAR commit is contained by a configured remote ref or a
  verified, checksum-bound Git bundle;
- no ready marker is accepted without its payload and validation report;
- the portable coordinator suite passes;
- the Balfrin build and representative physical run pass;
- the restored case can publish a new, independently validated artifact.

Run this drill after material dependency or storage changes and before relying
on scratch retirement for a long campaign.
