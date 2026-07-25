#!/usr/bin/env bash
# Build the Switzerland-wide 100 m static domain only after a REA-L HSURF file
# has been prepared.  Default mode prints the command; pass --execute to run it.
set -euo pipefail

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
CASE="$ROOT/case_studies/swiss_100m"
STATIC_DIR="$CASE/static"
OUTPUT="$STATIC_DIR/domain_static_swiss_100m.nc"
TEMP_OUTPUT="$STATIC_DIR/.domain_static_swiss_100m.nc.partial.$$"
MANIFEST="$STATIC_DIR/domain_static_swiss_100m.manifest.json"
TEMP_MANIFEST="$STATIC_DIR/.domain_static_swiss_100m.manifest.json.partial.$$"
CACHE_DIR="${SCRATCH:-$ROOT/.cache}/icon_hicar/cache/hicar_static_public"
BOUNDARY_TOPO="${BOUNDARY_TOPO:-}"
EXECUTE=0

if [[ ${1:-} == "--execute" ]]; then
  EXECUTE=1
elif [[ $# -ne 0 ]]; then
  echo "Usage: $0 [--execute]" >&2
  exit 2
fi

[[ -n "$BOUNDARY_TOPO" ]] || {
  echo "Set BOUNDARY_TOPO to a prepared REA-L forcing NetCDF containing HSURF." >&2
  exit 2
}
[[ -f "$BOUNDARY_TOPO" && -f "$BOUNDARY_TOPO.ready" ]] || {
  echo "BOUNDARY_TOPO and its publication marker must both exist: $BOUNDARY_TOPO(.ready)" >&2
  exit 2
}

cmd=(
  python3 "$ROOT/scripts/prepare_static_inputs.py"
  --output "$TEMP_OUTPUT"
  --center-lat 46.815 --center-lon 8.225
  --width-km 454 --height-km 330 --dx-m 100
  --public-sources --static-field-set land-surface --cache-dir "$CACHE_DIR"
  --boundary-topo-source "$BOUNDARY_TOPO"
  --topo-blend-width-km 30 --topo-blend-shape cosine
  --write-topo-blend-diagnostics --lu-categories USGS
)

printf 'Static-domain command:\n'
printf '  %q' "${cmd[@]}"
printf '\n'

if [[ $EXECUTE -eq 0 ]]; then
  echo "Dry run only. Re-run with --execute after checking REA-L HSURF coverage and scratch capacity."
  exit 0
fi

mkdir -p "$STATIC_DIR"
if [[ -e "$OUTPUT" || -e "$OUTPUT.ready" || -e "$MANIFEST" ]]; then
  echo "Refusing to overwrite an existing published or candidate static domain: $OUTPUT" >&2
  exit 2
fi
rm -f "$TEMP_OUTPUT" "$TEMP_OUTPUT.ready"
"${cmd[@]}"
python3 "$CASE/validation/validate_domain_plan.py" --static-file "$TEMP_OUTPUT"
mv "$TEMP_OUTPUT" "$OUTPUT"
rm -f "$TEMP_OUTPUT.ready"
python3 "$ROOT/scripts/hicar_domain_to_fieldextra_grid.py" \
  --domain-file "$OUTPUT" --border-km 10 --dlon-deg 0.01 --dlat-deg 0.01 \
  > "$CASE/config/fieldextra_target_grid.txt"
if command -v sha256sum >/dev/null 2>&1; then
  static_sha=$(sha256sum "$OUTPUT" | awk '{print $1}')
  boundary_sha=$(sha256sum "$BOUNDARY_TOPO" | awk '{print $1}')
else
  static_sha=$(shasum -a 256 "$OUTPUT" | awk '{print $1}')
  boundary_sha=$(shasum -a 256 "$BOUNDARY_TOPO" | awk '{print $1}')
fi
cat > "$TEMP_MANIFEST" <<EOF
{
  "case_id": "swiss_100m_v1",
  "static_file": "$(basename "$OUTPUT")",
  "static_sha256": "$static_sha",
  "boundary_topography_file": "$BOUNDARY_TOPO",
  "boundary_topography_sha256": "$boundary_sha",
  "topography_blend": {"width_km": 30, "shape": "cosine"},
  "forcing_grid": "$(cat "$CASE/config/fieldextra_target_grid.txt")",
  "public_sources": ["Copernicus DEM GLO-30", "ESA WorldCover 2021 v200", "SoilGrids 250 m 0-5 cm mean texture"]
}
EOF
mv "$TEMP_MANIFEST" "$MANIFEST"
touch "$OUTPUT.ready"
