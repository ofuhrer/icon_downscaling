#!/usr/bin/env bash
set -euo pipefail

[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"
module load gcc/12.3.0

: "${HICAR_COORDINATOR_ROOT:?set coordinator checkout containing the environment file}"

TOOL_ROOT="$SCRATCH/icon_hicar/tools/micromamba-2.3.2"
MAMBA="$TOOL_ROOT/bin/micromamba"
MAMBA_ARCHIVE="$TOOL_ROOT/micromamba-2.3.2-linux-64.tar.bz2"
MAMBA_SHA256="5512233cdd8564a671626081026dc861537a963baa06706baab08fac6f3bb9d2"
MAMBA_URL="https://micro.mamba.pm/api/micromamba/linux-64/2.3.2"
ENV_PREFIX="$SCRATCH/icon_hicar/terrain-radiation-env-v1"
ENV_FILE="$HICAR_COORDINATOR_ROOT/case_studies/swiss_200m/config/terrain_radiation_environment.yml"
PROJ_CACHE="$SCRATCH/icon_hicar/cache/proj"
EGM_GRID="$PROJ_CACHE/us_nga_egm08_25.tif"
EGM_SHA256="4191d471eefebf24091b56dbc604353cb3b8cf8cc70e448bb9ae56a272bef17a"

mkdir -p "$TOOL_ROOT" "$PROJ_CACHE"
if [[ ! -x "$MAMBA" ]]; then
  curl -LfsS "$MAMBA_URL" -o "$MAMBA_ARCHIVE.partial"
  echo "$MAMBA_SHA256  $MAMBA_ARCHIVE.partial" | sha256sum --check
  mv "$MAMBA_ARCHIVE.partial" "$MAMBA_ARCHIVE"
  tar -xjf "$MAMBA_ARCHIVE" -C "$TOOL_ROOT" bin/micromamba
fi
echo "$MAMBA_SHA256  $MAMBA_ARCHIVE" | sha256sum --check

export MAMBA_ROOT_PREFIX="$SCRATCH/icon_hicar/micromamba-root-2.3.2"
if [[ ! -x "$ENV_PREFIX/bin/python" ]]; then
  "$MAMBA" create --yes --prefix "$ENV_PREFIX" --file "$ENV_FILE"
fi
if ! "$MAMBA" run --prefix "$ENV_PREFIX" python -c 'import horayzon' >/dev/null 2>&1; then
  "$MAMBA" run --prefix "$ENV_PREFIX" python -m pip install \
    'git+https://github.com/ChristianSteger/HORAYZON.git@23212a7da3236d5af11a7e1f7687e6dbff0de741'
fi
"$MAMBA" run --prefix "$ENV_PREFIX" python - <<'PY'
import horayzon, netCDF4, numpy, pyproj, scipy
print("horayzon", getattr(horayzon, "__version__", "1.2.1 source tag"))
print("netCDF4", netCDF4.__version__)
print("numpy", numpy.__version__)
print("pyproj", pyproj.__version__)
print("scipy", scipy.__version__)
PY
"$MAMBA" list --prefix "$ENV_PREFIX" --explicit > "$ENV_PREFIX/environment.explicit.txt"

if [[ ! -s "$EGM_GRID" ]]; then
  curl -LfsS https://cdn.proj.org/us_nga_egm08_25.tif -o "$EGM_GRID.partial"
  echo "$EGM_SHA256  $EGM_GRID.partial" | sha256sum --check
  mv "$EGM_GRID.partial" "$EGM_GRID"
fi
echo "$EGM_SHA256  $EGM_GRID" | sha256sum --check

printf 'HICAR_TERRAIN_PYTHON=%s\nEGM2008_GRID=%s\n' "$ENV_PREFIX/bin/python" "$EGM_GRID"
