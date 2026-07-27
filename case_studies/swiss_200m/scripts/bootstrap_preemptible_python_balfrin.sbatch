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

release=${HICAR_RUNTIME_RELEASE:?Set HICAR_RUNTIME_RELEASE}
target=${HICAR_PYTHON_ENV_ROOT:-$SCRATCH/icon_hicar/venv_static}
report=${HICAR_PYTHON_ENV_REPORT:-$SCRATCH/icon_hicar/runtime/python_environment.json}
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
  "$executable" - "$partial" "$executable" "$manifest" "$manifest_sha" \
    "$requirements" "$requirements_sha" <<'PY'
import json
import pathlib
import platform
import subprocess
import sys
from datetime import datetime, timezone

(
    output,
    executable,
    manifest,
    manifest_sha,
    requirements,
    requirements_sha,
) = sys.argv[1:]
versions = {}
for name in ("numpy", "netCDF4", "scipy", "pyproj", "yaml"):
    module = __import__(name)
    versions["PyYAML" if name == "yaml" else name] = module.__version__
payload = {
    "schema_version": 1,
    "status": "PASS",
    "purpose": "preemptible-runtime",
    "published_at": datetime.now(timezone.utc).isoformat(),
    "python": str(pathlib.Path(executable).absolute()),
    "python_version": platform.python_version(),
    "runtime_release": str(pathlib.Path(manifest).resolve()),
    "runtime_release_sha256": manifest_sha,
    "requirements": str(pathlib.Path(requirements).resolve()),
    "requirements_sha256": requirements_sha,
    "versions": versions,
    "pip_freeze": subprocess.check_output(
        [executable, "-m", "pip", "freeze"], text=True
    ).splitlines(),
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
  validate_python "$target/bin/python"
  if test -f "$report.ready"; then
    python3 - "$report" "$target/bin/python" "$manifest_sha" "$requirements_sha" <<'PY'
import json
import pathlib
import sys
report, executable, manifest_sha, requirements_sha = sys.argv[1:]
payload = json.load(open(report))
if payload.get("status") != "PASS":
    raise SystemExit("Python environment report is not PASS")
if pathlib.Path(payload["python"]).absolute() != pathlib.Path(executable).absolute():
    raise SystemExit("Python environment report identifies another executable")
if payload.get("runtime_release_sha256") != manifest_sha:
    raise SystemExit("Python environment report identifies another runtime")
if payload.get("requirements_sha256") != requirements_sha:
    raise SystemExit("Python environment report identifies other requirements")
PY
  else
    publish_report "$target/bin/python"
  fi
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
python3 -m venv "$partial"
"$partial/bin/python" -m pip install --upgrade pip
"$partial/bin/python" -m pip install --requirement "$requirements"
validate_python "$partial/bin/python"
mv "$partial" "$target"
trap - EXIT
publish_report "$target/bin/python"
echo "PASS: Python environment published at $report"
