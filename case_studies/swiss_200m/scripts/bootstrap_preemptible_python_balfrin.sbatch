#!/bin/bash -l
# Build and publish the Python environment used by the pre-emptible workflow.
#SBATCH --job-name=hicar-preempt-python
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=8G
#SBATCH --time=00:30:00
#SBATCH --no-requeue

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module purge || true
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module load python/3.11.7
export PYTHONDONTWRITEBYTECODE=1

release=${HICAR_RUNTIME_RELEASE:?Set HICAR_RUNTIME_RELEASE}
release=$(realpath "$release")
release_id=$(basename "$release")
target=${HICAR_PYTHON_ENV_ROOT:-$SCRATCH/icon_hicar/runtime/python/$release_id}
report=${HICAR_PYTHON_ENV_REPORT:-$SCRATCH/icon_hicar/runtime/python/$release_id.environment.json}
manifest="$release/runtime_release.json"
requirements="$release/requirements/balfrin-preemptible.txt"

for path in "$manifest" "$manifest.ready" "$requirements"; do
  test -e "$path" || { echo "missing Python environment input: $path" >&2; exit 2; }
done
requirements_sha=$(sha256sum "$requirements" | awk '{print $1}')
manifest_sha=$(sha256sum "$manifest" | awk '{print $1}')

validate_python() {
  local executable=$1
  "$executable" - <<'PY'
import netCDF4
import numpy
import pyproj
import scipy
import yaml
print(
    numpy.__version__,
    netCDF4.__version__,
    scipy.__version__,
    pyproj.__version__,
    yaml.__version__,
)
PY
  "$executable" -m pip check
}

publish_report() {
  local executable=$1
  local partial="$report.partial.${SLURM_JOB_ID:-$$}"
  mkdir -p "$(dirname "$report")"
  PYTHONDONTWRITEBYTECODE=1 "$executable" - "$partial" "$target" "$executable" \
    "$manifest" "$manifest_sha" "$requirements" "$requirements_sha" <<'PY'
import hashlib
import itertools
import json
import os
import pathlib
import platform
import subprocess
import sys
from datetime import datetime, timezone

(
    output,
    environment_root,
    executable,
    manifest,
    manifest_sha,
    requirements,
    requirements_sha,
) = sys.argv[1:]
root = pathlib.Path(environment_root).resolve()
python = pathlib.Path(executable).absolute()
pip_freeze = sorted(
    line.strip()
    for line in subprocess.check_output(
        [executable, "-m", "pip", "freeze"],
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    ).splitlines()
    if line.strip()
)
freeze_bytes = ("\n".join(pip_freeze) + "\n").encode()
python_hash = hashlib.sha256()
with python.open("rb") as stream:
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        python_hash.update(block)
mutable = []
for path in itertools.chain((root,), root.rglob("*")):
    if path.is_symlink() or not (path.is_file() or path.is_dir()):
        continue
    if path.stat().st_mode & 0o222:
        mutable.append(str(path))
        if len(mutable) == 5:
            break
if mutable:
    raise SystemExit("environment remains writable: " + ", ".join(mutable))
versions = {}
for name in ("numpy", "netCDF4", "scipy", "pyproj", "yaml"):
    module = __import__(name)
    versions["PyYAML" if name == "yaml" else name] = module.__version__
payload = {
    "schema_version": 2,
    "status": "PASS",
    "purpose": "preemptible-runtime",
    "published_at": datetime.now(timezone.utc).isoformat(),
    "environment_root": str(root),
    "immutable": True,
    "python": str(python),
    "python_sha256": python_hash.hexdigest(),
    "python_version": platform.python_version(),
    "runtime_release": str(pathlib.Path(manifest).resolve()),
    "runtime_release_sha256": manifest_sha,
    "requirements": str(pathlib.Path(requirements).resolve()),
    "requirements_sha256": requirements_sha,
    "versions": versions,
    "pip_freeze": pip_freeze,
    "pip_freeze_sha256": hashlib.sha256(freeze_bytes).hexdigest(),
}
pathlib.Path(output).write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n"
)
PY
  rm -f "$report.ready"
  mv "$partial" "$report"
  touch "$report.ready"
}

if test -e "$target"; then
  test -x "$target/bin/python" || {
    echo "existing Python environment is incomplete: $target" >&2
    exit 2
  }
  test -f "$report" && test -f "$report.ready" || {
    echo "refusing unreported existing Python environment: $target" >&2
    exit 2
  }
  validate_python "$target/bin/python"
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$release" python3 - \
    "$report" "$manifest" <<'PY'
import pathlib
import sys

from orchestration.runtime_contract import validate_python_environment

validate_python_environment(
    pathlib.Path(sys.argv[1]),
    pathlib.Path(sys.argv[2]),
    smoke=True,
)
PY
  echo "Python environment already published: $report"
  exit 0
fi

partial="$target.partial.${SLURM_JOB_ID:?}"
test ! -e "$partial" || {
  echo "Python environment staging path already exists: $partial" >&2
  exit 2
}
cleanup() {
  if test -d "$partial"; then
    rm -rf "$partial"
  fi
}
trap cleanup EXIT
mkdir -p "$(dirname "$target")"
python3 -m venv "$partial"
"$partial/bin/python" -m pip install --upgrade pip
"$partial/bin/python" -m pip install --requirement "$requirements"
validate_python "$partial/bin/python"
PYTHONDONTWRITEBYTECODE=1 "$partial/bin/python" -m compileall -q "$partial"
mv "$partial" "$target"
trap - EXIT
find "$target" \( -type f -o -type d \) -exec chmod a-w {} +
publish_report "$target/bin/python"
echo "PASS: Python environment published at $report"
