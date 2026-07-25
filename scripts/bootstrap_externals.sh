#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: bootstrap_externals.sh [--with-fieldextra]

Initialize public Git submodules at their pinned revisions. With
--with-fieldextra, also materialize the private commit-locked fieldextra source
reference for developers who have access to COSMO-ORG/fieldextra.
EOF
}

die() {
  echo "error: $*" >&2
  exit 2
}

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
WITH_FIELDEXTRA=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --with-fieldextra) WITH_FIELDEXTRA=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

git -C "$REPO_ROOT" rev-parse --show-toplevel >/dev/null 2>&1 ||
  die "$REPO_ROOT is not a Git worktree"

hicar_path="$REPO_ROOT/HICAR"
if git -C "$hicar_path" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  if [ -n "$(git -C "$hicar_path" status --porcelain)" ]; then
    die "HICAR has local changes; refusing to move its pinned revision"
  fi
fi

git -C "$REPO_ROOT" submodule sync --recursive
git -C "$REPO_ROOT" submodule update --init --recursive

hicar_commit=$(git -C "$hicar_path" rev-parse HEAD)
printf '%-12s %s\n' "HICAR" "$hicar_commit"

if [ "$WITH_FIELDEXTRA" -eq 0 ]; then
  exit 0
fi

lock_file="$REPO_ROOT/externals/fieldextra.lock"
[ -f "$lock_file" ] || die "missing fieldextra lock: $lock_file"
# shellcheck disable=SC1090
. "$lock_file"
: "${fieldextra_url:?missing fieldextra_url in lock}"
: "${fieldextra_commit:?missing fieldextra_commit in lock}"

fieldextra_path="$REPO_ROOT/fieldextra"
if git -C "$fieldextra_path" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  if [ -n "$(git -C "$fieldextra_path" status --porcelain)" ]; then
    die "fieldextra has local changes; refusing to move its locked revision"
  fi
else
  [ ! -e "$fieldextra_path" ] ||
    die "$fieldextra_path exists but is not a Git worktree"
  git clone --no-checkout "$fieldextra_url" "$fieldextra_path"
fi

git -C "$fieldextra_path" fetch origin "$fieldextra_commit"
git -C "$fieldextra_path" checkout --detach "$fieldextra_commit"
printf '%-12s %s\n' "fieldextra" "$(git -C "$fieldextra_path" rev-parse HEAD)"
