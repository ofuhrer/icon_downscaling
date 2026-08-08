#!/usr/bin/env bash
set -euo pipefail

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
CASE="$ROOT/case_studies/swiss_200m"
STATIC_DIR="$CASE/static"
OUTPUT="$STATIC_DIR/domain_static_swiss_200m.nc"
EXTERNAL="$STATIC_DIR/domain_external_swiss_200m.nc"
INITIAL="$STATIC_DIR/domain_initial_placeholder_swiss_200m.nc"
RAW="$STATIC_DIR/.domain_source_swiss_200m.nc.partial.$$"
TEMP_OUTPUT="$STATIC_DIR/.domain_static_swiss_200m.nc.partial.$$"
TEMP_EXTERNAL="$STATIC_DIR/.domain_external_swiss_200m.nc.partial.$$"
TEMP_INITIAL="$STATIC_DIR/.domain_initial_placeholder_swiss_200m.nc.partial.$$"
CACHE_DIR="${SCRATCH:-$ROOT/.cache}/icon_hicar/cache/hicar_static_public"
BOUNDARY_TOPO="${BOUNDARY_TOPO:-}"

[[ ${1:-} == "--execute" ]] || { echo "Usage: $0 --execute" >&2; exit 2; }
[[ -f "$BOUNDARY_TOPO" && -f "$BOUNDARY_TOPO.ready" ]] || { echo "Set published BOUNDARY_TOPO" >&2; exit 2; }
for path in "$OUTPUT" "$EXTERNAL" "$INITIAL"; do
  [[ ! -e "$path" && ! -e "$path.ready" ]] || { echo "Refusing overwrite: $path" >&2; exit 2; }
done
mkdir -p "$STATIC_DIR"
trap 'rm -f "$RAW" "$TEMP_OUTPUT" "$TEMP_EXTERNAL" "$TEMP_INITIAL"' EXIT
python3 "$ROOT/scripts/prepare_static_inputs.py" \
  --output "$RAW" --center-lat 46.815 --center-lon 8.225 \
  --width-km 454 --height-km 330 --dx-m 200 \
  --public-sources --static-field-set land-surface --cache-dir "$CACHE_DIR" \
  --boundary-topo-source "$BOUNDARY_TOPO" --topo-blend-width-km 30 \
  --topo-blend-shape cosine --write-topo-blend-diagnostics --lu-categories USGS
python3 "$ROOT/scripts/hicarprep.py" build-domain \
  --source "$RAW" --static "$TEMP_OUTPUT" --external "$TEMP_EXTERNAL" \
  --initial-surface "$TEMP_INITIAL" --epoch-valid-from 2021-01-01T00:00:00Z \
  --initial-valid-time 2021-01-01T00:00:00Z \
  --nz 80 --model-top-m 12000 --lowest-layer-m 26 --stretch-factor 0.65 \
  --decay-rate-large 2 --decay-rate-small 6 --smooth-window-radius 5 --smooth-cycles 10 \
  --minimum-layer-thickness-m 20
mv "$TEMP_OUTPUT" "$OUTPUT"
mv "$TEMP_EXTERNAL" "$EXTERNAL"
mv "$TEMP_INITIAL" "$INITIAL"
touch "$OUTPUT.ready" "$EXTERNAL.ready" "$INITIAL.ready"
