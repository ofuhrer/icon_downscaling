#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: check_recovery_readiness.sh [--offline]

Audit whether coordinator source, external revisions, and the durable archive
contract are ready for deletion of local and Balfrin scratch copies.

The default verifies remote Git reachability. --offline skips network checks
but can never produce a fully ready verdict.
EOF
}

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
ONLINE=1
FAILURES=0
WARNINGS=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --offline) ONLINE=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "error: unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

pass() {
  printf 'PASS  %s\n' "$*"
}

warn() {
  printf 'WARN  %s\n' "$*"
  WARNINGS=$((WARNINGS + 1))
}

fail() {
  printf 'FAIL  %s\n' "$*"
  FAILURES=$((FAILURES + 1))
}

require_file() {
  if [ -f "$REPO_ROOT/$1" ]; then
    pass "tracked recovery input exists: $1"
  else
    fail "missing recovery input: $1"
  fi
}

git -C "$REPO_ROOT" rev-parse --show-toplevel >/dev/null 2>&1 || {
  echo "error: $REPO_ROOT is not a Git worktree" >&2
  exit 2
}

outer_changes=$(git -C "$REPO_ROOT" status --porcelain \
  --untracked-files=all --ignore-submodules=all)
if [ -z "$outer_changes" ]; then
  pass "coordinator worktree has no outer changes"
else
  fail "coordinator worktree has uncommitted or untracked outer changes"
fi

outer_unpushed=$(git -C "$REPO_ROOT" rev-list --count \
  --branches --not --remotes 2>/dev/null || printf 'unknown')
if [ "$outer_unpushed" = "0" ]; then
  pass "coordinator has no local-only branch commits"
else
  fail "coordinator local-only branch commits: $outer_unpushed"
fi

outer_branch=$(git -C "$REPO_ROOT" branch --show-current)
outer_remote=$(git -C "$REPO_ROOT" remote get-url origin 2>/dev/null || true)
if [ "$ONLINE" -eq 1 ] && [ -n "$outer_branch" ] && [ -n "$outer_remote" ]; then
  if outer_remote_head=$(git ls-remote "$outer_remote" \
       "refs/heads/$outer_branch" | awk 'NR == 1 {print $1}'); then
    outer_head=$(git -C "$REPO_ROOT" rev-parse HEAD)
    if [ "$outer_remote_head" = "$outer_head" ]; then
      pass "coordinator HEAD is the remote $outer_branch tip"
    else
      fail "coordinator HEAD is not the remote $outer_branch tip"
    fi
  else
    fail "coordinator remote is not reachable"
  fi
elif [ "$ONLINE" -eq 0 ]; then
  warn "remote coordinator reachability not checked offline"
else
  fail "coordinator origin or current branch is unavailable"
fi

hicar_path="$REPO_ROOT/HICAR"
hicar_pin=$(git -C "$REPO_ROOT" ls-tree HEAD HICAR | awk 'NR == 1 {print $3}')
if git -C "$hicar_path" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  hicar_head=$(git -C "$hicar_path" rev-parse HEAD)
  if [ "$hicar_head" = "$hicar_pin" ]; then
    pass "HICAR checkout matches coordinator pin $hicar_pin"
  else
    fail "HICAR checkout $hicar_head differs from coordinator pin $hicar_pin"
  fi

  if [ -z "$(git -C "$hicar_path" status --porcelain --untracked-files=all)" ]; then
    pass "HICAR worktree is clean"
  else
    fail "HICAR worktree has uncommitted or untracked changes"
  fi

  hicar_unpushed=$(git -C "$hicar_path" rev-list --count \
    --branches --not --remotes 2>/dev/null || printf 'unknown')
  if [ "$hicar_unpushed" = "0" ]; then
    pass "HICAR has no local-only branch commits"
  else
    fail "HICAR local-only branch commits: $hicar_unpushed"
  fi

  if [ "$ONLINE" -eq 1 ]; then
    if git -C "$hicar_path" fetch --quiet --no-tags origin "$hicar_pin" &&
       git -C "$hicar_path" cat-file -e "$hicar_pin^{commit}"; then
      pass "pinned HICAR commit is reachable from origin"
    else
      fail "pinned HICAR commit is not fetchable from origin"
    fi
  else
    warn "remote HICAR reachability not checked offline"
  fi
else
  fail "HICAR submodule is not initialized"
fi

require_file "externals/fieldextra.lock"
fieldextra_url=$(awk -F= '$1 == "fieldextra_url" {print $2}' \
  "$REPO_ROOT/externals/fieldextra.lock")
fieldextra_commit=$(awk -F= '$1 == "fieldextra_commit" {print $2}' \
  "$REPO_ROOT/externals/fieldextra.lock")
if [ "$ONLINE" -eq 1 ]; then
  fieldextra_probe=$(mktemp -d)
  git -C "$fieldextra_probe" init --quiet
  if git -C "$fieldextra_probe" fetch --quiet --no-tags --depth=1 \
       "$fieldextra_url" "$fieldextra_commit" 2>/dev/null &&
     git -C "$fieldextra_probe" cat-file -e \
       "$fieldextra_commit^{commit}" 2>/dev/null; then
    pass "locked fieldextra commit is accessible to this account"
  else
    fail "locked private fieldextra commit is not fetchable by this account"
  fi
  rm -rf "$fieldextra_probe"
else
  warn "private fieldextra access not checked offline"
fi

rebuild_inventory="recovery/rebuild_inventory.json"
require_file "$rebuild_inventory"

while IFS='|' read -r candidate kind locator protected_ref bundle_sha; do
  case "$kind" in
    remote_ref)
      if [ "$ONLINE" -eq 0 ]; then
        warn "candidate $candidate remote containment not checked offline"
        continue
      fi
      candidate_probe=$(mktemp -d)
      git -C "$candidate_probe" init --quiet
      if git -C "$candidate_probe" fetch --quiet --no-tags \
           "$locator" "$protected_ref" 2>/dev/null &&
         git -C "$candidate_probe" cat-file -e \
           "$candidate^{commit}" 2>/dev/null; then
        pass "HICAR candidate $candidate is contained by $protected_ref"
      else
        fail "HICAR candidate $candidate is not contained by $protected_ref"
      fi
      rm -rf "$candidate_probe"
      ;;
    git_bundle)
      case "$locator" in
        /*) bundle_path="$locator" ;;
        *) bundle_path="$REPO_ROOT/$locator" ;;
      esac
      if [ ! -f "$bundle_path" ]; then
        fail "HICAR candidate $candidate bundle is unavailable: $locator"
        continue
      fi
      observed_bundle_sha=$(python3 - "$bundle_path" <<'PY'
import hashlib
import sys

digest = hashlib.sha256()
with open(sys.argv[1], "rb") as handle:
    for block in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(block)
print(digest.hexdigest())
PY
)
      if [ -z "$bundle_sha" ] || [ "$observed_bundle_sha" != "$bundle_sha" ]; then
        fail "HICAR candidate $candidate bundle checksum does not match"
        continue
      fi
      candidate_probe=$(mktemp -d)
      git -C "$candidate_probe" init --quiet
      if git -C "$candidate_probe" fetch --quiet --no-tags \
           "$bundle_path" "$protected_ref" 2>/dev/null &&
         git -C "$candidate_probe" cat-file -e \
           "$candidate^{commit}" 2>/dev/null; then
        pass "HICAR candidate $candidate is contained by verified bundle ref"
      else
        fail "HICAR candidate $candidate is absent from verified bundle ref"
      fi
      rm -rf "$candidate_probe"
      ;;
    *)
      fail "HICAR candidate $candidate has no remote-ref or bundle protection"
      ;;
  esac
done < <(python3 - "$REPO_ROOT/$rebuild_inventory" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    inventory = json.load(handle)

for candidate in inventory.get("hicar_source_candidates", []):
    protection = candidate.get("protection", {})
    values = (
        candidate.get("commit", ""),
        protection.get("kind", ""),
        protection.get("locator") or "",
        protection.get("ref") or "",
        protection.get("sha256") or "",
    )
    print("|".join(values))
PY
)

inventory_result=$(python3 - "$REPO_ROOT/$rebuild_inventory" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    inventory = json.load(handle)

unprotected = []
for candidate in inventory.get("hicar_source_candidates", []):
    protection = candidate.get("protection", {})
    if protection.get("kind") not in {"remote_ref", "git_bundle"}:
        unprotected.append(candidate.get("commit", "UNKNOWN"))
    elif not protection.get("locator"):
        unprotected.append(candidate.get("commit", "UNKNOWN"))

unarchived = []
for artifact in inventory.get("rebuild_critical_artifact_classes", []):
    if not artifact.get("archive_manifest") or not artifact.get("restore_verification"):
        unarchived.append(artifact.get("id", "UNKNOWN"))

status = inventory.get("status", "MISSING")
if status != "READY" or unprotected or unarchived:
    print(
        "NOT_READY "
        f"status={status} "
        f"unprotected={','.join(unprotected) or 'none'} "
        f"unarchived={','.join(unarchived) or 'none'}"
    )
else:
    print("READY")
PY
)
case "$inventory_result" in
  READY) pass "rebuild-critical source and artifact inventory is complete" ;;
  *) fail "rebuild inventory: $inventory_result" ;;
esac

archive_contract="case_studies/swiss_200m/config/production_archive_contract.json"
require_file "$archive_contract"
archive_result=$(python3 - "$REPO_ROOT/$archive_contract" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    contract = json.load(handle)

approval = contract.get("approval", {})
required = (
    "destination",
    "owner",
    "quota_bytes",
    "measured_transfer_bytes_per_second",
    "restore_drill_report",
    "approved_by",
)
missing = [name for name in required if approval.get(name) in (None, "")]
status = contract.get("status", "MISSING")
if status == "UNRESOLVED" or missing:
    print(f"NOT_READY status={status} missing={','.join(missing) or 'none'}")
else:
    print(f"READY status={status}")
PY
)
case "$archive_result" in
  READY*) pass "durable archive contract is approved and complete" ;;
  *) fail "durable archive contract: $archive_result" ;;
esac

for skill in \
  balfrin-user-environment \
  icon-balfrin-grib \
  icon-hicar-forcing \
  icon-hicar-domain \
  hicar-alpine-configuration \
  hicar-balfrin-runtime
do
  require_file ".agents/skills/$skill/SKILL.md"
done

printf '\nSummary: %d failure(s), %d warning(s)\n' "$FAILURES" "$WARNINGS"
if [ "$FAILURES" -ne 0 ] || [ "$WARNINGS" -ne 0 ]; then
  printf '%s\n' "NOT READY FOR DELETION"
  exit 1
fi

printf '%s\n' "SOURCE AND CONTRACT CHECKS READY"
printf '%s\n' \
  "Complete the external artifact inventory and clean-room restore drill before deletion."
