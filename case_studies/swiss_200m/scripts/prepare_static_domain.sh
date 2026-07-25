#!/usr/bin/env bash
set -euo pipefail

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
CASE="$ROOT/case_studies/swiss_200m"
STATIC_DIR="$CASE/static"
OUTPUT="$STATIC_DIR/domain_static_swiss_200m.nc"
TEMP_OUTPUT="$STATIC_DIR/.domain_static_swiss_200m.nc.partial.$$"
CACHE_DIR="${SCRATCH:-$ROOT/.cache}/icon_hicar/cache/hicar_static_public"
BOUNDARY_TOPO="${BOUNDARY_TOPO:-}"

[[ ${1:-} == "--execute" ]] || { echo "Usage: $0 --execute" >&2; exit 2; }
[[ -f "$BOUNDARY_TOPO" && -f "$BOUNDARY_TOPO.ready" ]] || { echo "Set published BOUNDARY_TOPO" >&2; exit 2; }
[[ ! -e "$OUTPUT" && ! -e "$OUTPUT.ready" ]] || { echo "Refusing overwrite: $OUTPUT" >&2; exit 2; }
mkdir -p "$STATIC_DIR"
python3 "$ROOT/scripts/prepare_static_inputs.py" \
  --output "$TEMP_OUTPUT" --center-lat 46.815 --center-lon 8.225 \
  --width-km 454 --height-km 330 --dx-m 200 \
  --public-sources --static-field-set land-surface --cache-dir "$CACHE_DIR" \
  --boundary-topo-source "$BOUNDARY_TOPO" --topo-blend-width-km 30 \
  --topo-blend-shape cosine --write-topo-blend-diagnostics --lu-categories USGS
python3 "$CASE/validation/validate_domain_plan.py" --static-file "$TEMP_OUTPUT"
mv "$TEMP_OUTPUT" "$OUTPUT"
python3 "$ROOT/scripts/hicar_domain_to_fieldextra_grid.py" --domain-file "$OUTPUT" \
  --border-km 10 --dlon-deg 0.01 --dlat-deg 0.01 > "$CASE/config/fieldextra_target_grid.txt"
sha256sum "$OUTPUT" > "$OUTPUT.sha256"
printf 'published\n' > "$OUTPUT.ready"
