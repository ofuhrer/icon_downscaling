#!/usr/bin/env bash
# Prepare only real ICON-covered benchmark domains, then mark them preflight-ready.
set -euo pipefail
 [ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
 module use "$USER_ENV_ROOT/modules"
 module load python/3.11.7
ROOT=${SCALING_ROOT:?set SCALING_ROOT to the Balfrin hicar_scaling directory}
SRC=${HICAR_WORKSPACE:?set HICAR_WORKSPACE to the synced coordinating workspace}
PYTHON=${STATIC_PYTHON:-$SCRATCH/icon_hicar/venv_static/bin/python}
test -x "$PYTHON"
# prepare_icon_inputs.sh invokes python3 internally for the fieldextra grid
# helper.  Ensure that resolves to the same NetCDF-capable environment.
export PATH="$(dirname "$PYTHON"):$PATH"
ICON_ROOT=${ICON_ROOT:-$SCRATCH/icon_hicar/data/icon_ch1_eps_20260710T18_ctrl_oper_icon_000}
CENTER_LAT=${CENTER_LAT:-46.75}; CENTER_LON=${CENTER_LON:-8.15}
"$PYTHON" - "$ROOT/manifest.json" <<'PY' | while IFS=$'\t' read -r width height; do
import json,sys
m=json.load(open(sys.argv[1]))
for width,height in sorted({(s['width_km'],s['height_km']) for s in m['scenarios']}):
    print(f"{width}\t{height}")
PY
  domain="$ROOT/domains/${width}x${height}km"; mkdir -p "$domain/static" "$domain/forcing"
  test -f "$domain/PREFLIGHT_OK" && continue
  "$PYTHON" "$SRC/scripts/prepare_static_inputs.py" --output "$domain/static/domain_static_unrelaxed.nc" --center-lat "$CENTER_LAT" --center-lon "$CENTER_LON" --width-km "$width" --height-km "$height" --dx-m 250 --public-sources --static-field-set land-surface
  for hour in $(seq 0 8); do
    native=$(printf 'lfff%04d0000' "$hour")
    forcing=$(printf 'hicar_forcing_f%03d.nc' "$hour")
    "$SRC/scripts/prepare_icon_inputs.sh" --input "$ICON_ROOT/$native" --static "$ICON_ROOT/lfff00000000c" --output "$domain/forcing/$forcing" --domain-file "$domain/static/domain_static_unrelaxed.nc" --load-balfrin-modules
  done
  "$PYTHON" "$SRC/scripts/prepare_static_inputs.py" --output "$domain/static/domain_static_relaxed.nc" --center-lat "$CENTER_LAT" --center-lon "$CENTER_LON" --width-km "$width" --height-km "$height" --dx-m 250 --public-sources --static-field-set land-surface --boundary-topo-source "$domain/forcing/hicar_forcing_f000.nc" --write-topo-blend-diagnostics
  for hour in $(seq 0 8); do
    printf '"%s/forcing/hicar_forcing_f%03d.nc"\n' "$domain" "$hour"
  done > "$domain/forcing_file_list.txt"
  test -f "$domain/static/domain_static_relaxed.nc.ready"; test -f "$domain/forcing/hicar_forcing_f008.nc.ready"
done
