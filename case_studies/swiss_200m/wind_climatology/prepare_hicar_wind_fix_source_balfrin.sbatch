#!/bin/bash -l
#SBATCH --job-name=hicar-wind-fix-source
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=8G
#SBATCH --time=01:00:00

set -euo pipefail

: "${HICAR_BASE_SOURCE:?set HICAR_BASE_SOURCE}"
: "${HICAR_EXPECTED_BASE_COMMIT:?set HICAR_EXPECTED_BASE_COMMIT}"
: "${HICAR_FIX_PATCH:?set HICAR_FIX_PATCH}"
: "${HICAR_TARGET_SOURCE:?set HICAR_TARGET_SOURCE}"
: "${HICAR_SOURCE_REPORT:?set HICAR_SOURCE_REPORT}"

test ! -e "${HICAR_TARGET_SOURCE}"
test ! -e "${HICAR_SOURCE_REPORT}"
test ! -e "${HICAR_SOURCE_REPORT}.ready"
test "$(git -C "${HICAR_BASE_SOURCE}" rev-parse HEAD)" = \
  "${HICAR_EXPECTED_BASE_COMMIT}"
test -z "$(git -C "${HICAR_BASE_SOURCE}" status --porcelain)"

mkdir -p "$(dirname "${HICAR_TARGET_SOURCE}")"
cp -a --reflink=auto "${HICAR_BASE_SOURCE}" "${HICAR_TARGET_SOURCE}"
git -C "${HICAR_TARGET_SOURCE}" apply --check "${HICAR_FIX_PATCH}"
git -C "${HICAR_TARGET_SOURCE}" apply "${HICAR_FIX_PATCH}"
git -C "${HICAR_TARGET_SOURCE}" diff --check
git -C "${HICAR_TARGET_SOURCE}" add src/objects/domain_obj.F90
git -C "${HICAR_TARGET_SOURCE}" \
  -c user.name='ICON-HICAR qualification' \
  -c user.email='noreply@localhost' \
  commit -m 'Restore adjusted horizontal wind tendency application'
test -z "$(git -C "${HICAR_TARGET_SOURCE}" status --porcelain)"

python3 - \
  "${HICAR_BASE_SOURCE}" \
  "${HICAR_EXPECTED_BASE_COMMIT}" \
  "${HICAR_FIX_PATCH}" \
  "${HICAR_TARGET_SOURCE}" \
  "${HICAR_SOURCE_REPORT}" <<'PY'
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

base, base_commit, patch, target, output = map(Path, sys.argv[1:])


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git(*args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(target), *args], universal_newlines=True
    ).strip()


payload = {
    "schema_version": 1,
    "status": "PASS",
    "purpose": "isolated-horizontal-wind-tendency-fix-source",
    "base_source": str(base.resolve()),
    "base_commit": str(base_commit),
    "patch": str(patch.resolve()),
    "patch_sha256": sha256(patch),
    "source": str(target.resolve()),
    "source_commit": git("rev-parse", "HEAD"),
    "source_tree_status": git("status", "--porcelain"),
    "changed_files": git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").splitlines(),
}
output.parent.mkdir(parents=True, exist_ok=True)
with tempfile.NamedTemporaryFile(
    mode="w", encoding="utf-8", dir=output.parent, delete=False
) as stream:
    json.dump(payload, stream, indent=2, sort_keys=True)
    stream.write("\n")
    temporary = Path(stream.name)
os.replace(temporary, output)
Path(f"{output}.ready").touch()
print(payload["source_commit"])
PY
