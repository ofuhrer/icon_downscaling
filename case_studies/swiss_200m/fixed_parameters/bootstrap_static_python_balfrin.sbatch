#!/usr/bin/env bash
#SBATCH --job-name=static-abc-python
#SBATCH --partition=pp-short
#SBATCH --cpus-per-task=4
#SBATCH --time=00:30:00
#SBATCH --mem=7G
#SBATCH --output=/scratch/mch/olifu/icon_hicar/qualification/static_abc_v1/logs/%x_%j.out
#SBATCH --error=/scratch/mch/olifu/icon_hicar/qualification/static_abc_v1/logs/%x_%j.err

set -euo pipefail
export PYTHONUNBUFFERED=1

qual_root=${STATIC_QUAL_ROOT:-/scratch/mch/olifu/icon_hicar/qualification/static_abc_v1}
runtime_root=${STATIC_RUNTIME_ROOT:?set STATIC_RUNTIME_ROOT to the immutable static runtime}
runtime_id=$(basename "$runtime_root")
environment=${STATIC_PYTHON_ROOT:-$qual_root/python-${runtime_id}}
manifest=${environment}.environment.json
ready=${manifest}.ready

test "${SLURM_JOB_PARTITION:-}" = pp-short || {
  echo "static Python bootstrap must run in pp-short" >&2
  exit 2
}
test -f "$runtime_root/config/balfrin.env"
. "$runtime_root/scripts/load_balfrin_site_config.sh"
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"
module load python/3.11.7 >/dev/null
mkdir -p "$qual_root/logs" /scratch/mch/olifu/icon_hicar/cache/pip

if test -f "$ready"; then
  "$environment/bin/python" -c 'import netCDF4,numpy,scipy,pyproj,rasterio'
  test ! -w "$environment/pyvenv.cfg" || {
    echo "published static Python environment is writable" >&2
    exit 2
  }
  echo "$environment"
  exit 0
fi

test -f "$environment/pyvenv.cfg" || python3 -m venv "$environment"
"$environment/bin/python" -m pip install --upgrade pip >/dev/null
"$environment/bin/python" -m pip install \
  --cache-dir /scratch/mch/olifu/icon_hicar/cache/pip --only-binary=:all: \
  netCDF4==1.7.4 numpy==2.3.4 scipy==1.17.1 pyproj==3.7.2 rasterio==1.4.3

tmp=${manifest}.tmp.${SLURM_JOB_ID}
runtime_manifest=${runtime_root}/runtime_manifest.json
runtime_manifest_sha=$(sha256sum "$runtime_manifest" | awk '{print $1}')
"$environment/bin/python" - "$tmp" "$runtime_id" "$runtime_manifest_sha" <<'PY'
import hashlib, importlib, json, os, platform, subprocess, sys
from pathlib import Path
output = Path(sys.argv[1])
packages = {}
for name in ("netCDF4", "numpy", "scipy", "pyproj", "rasterio"):
    module = importlib.import_module(name)
    packages[name] = getattr(module, "__version__", "unknown")
payload = {
    "schema": "hicar-static-python-environment/v1",
    "python": str(Path(sys.executable).resolve()),
    "python_version": platform.python_version(),
    "runtime_id": sys.argv[2],
    "runtime_manifest_sha256": sys.argv[3],
    "packages": packages,
    "pip_freeze": subprocess.run(
        [sys.executable, "-m", "pip", "freeze"], check=True, capture_output=True, text=True
    ).stdout.splitlines(),
}
with output.open("w", encoding="utf-8") as stream:
    json.dump(payload, stream, indent=2, sort_keys=True)
    stream.write("\n")
PY
mv "$tmp" "$manifest"
digest=$(sha256sum "$manifest" | awk '{print $1}')
printf 'sha256 %s  %s\n' "$digest" "$(basename "$manifest")" > "${ready}.tmp.${SLURM_JOB_ID}"
mv "${ready}.tmp.${SLURM_JOB_ID}" "$ready"
chmod -R a-w "$environment"
chmod a-w "$manifest" "$ready"
echo "$environment"
