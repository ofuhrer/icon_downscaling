#!/bin/bash -l
# Build, validate, and atomically publish one structured REA-L forcing record.
# Submit manually after the REA-L source bundle is published, e.g.:
# sbatch --export=ALL,REA_DATE=20100101,REA_TIME=0000,REA_STEP=0 scripts/regrid_rea_l_forcing.sbatch
#SBATCH --job-name=rea_l_hicar_forcing
#SBATCH --partition=pp-short
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --time=01:00:00
#SBATCH --output=%x_%j.out
#SBATCH --error=%x_%j.err

set -euo pipefail

: "${REA_DATE:?set REA_DATE as YYYYMMDD}"
: "${REA_TIME:?set REA_TIME as HHMM}"
REA_STEP="${REA_STEP:-${SLURM_ARRAY_TASK_ID:-0}}"
[[ "$REA_DATE" =~ ^[0-9]{8}$ ]] || { echo "REA_DATE must be YYYYMMDD" >&2; exit 2; }
[[ "$REA_TIME" =~ ^[0-9]{4}$ ]] || { echo "REA_TIME must be HHMM" >&2; exit 2; }
[[ "$REA_STEP" =~ ^[0-9]+$ ]] || { echo "REA_STEP must be a non-negative integer" >&2; exit 2; }
REA_STEP=$((10#$REA_STEP))

[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"

REPO_ROOT="${REPO_ROOT:-$SCRATCH/icon_hicar}"
CASE_ROOT="${CASE_ROOT:-$REPO_ROOT/case_studies/swiss_100m}"
if [[ "$REA_STEP" == 0 ]]; then
  TAG="${REA_DATE}_${REA_TIME}"
else
  TAG="${REA_DATE}_${REA_TIME}_s$(printf '%02d' "$REA_STEP")"
fi
SOURCE_DIR="$CASE_ROOT/source/rea_l_ch1/$TAG"
DYNAMIC="$SOURCE_DIR/rea_l_ch1_${TAG}_dynamic.grib"
STATIC="$SOURCE_DIR/rea_l_ch1_${TAG}_static.grib"
SOURCE_MANIFEST="$SOURCE_DIR/source_manifest.json"
GRID_FILE="$CASE_ROOT/config/fieldextra_target_grid.txt"
OUT_DIR="$CASE_ROOT/forcing/rea_l_ch1/$TAG"
FINAL="$OUT_DIR/rea_l_hicar_${TAG}.nc"
PARTIAL="$OUT_DIR/.rea_l_hicar_${TAG}.nc.partial.${SLURM_JOB_ID}"
REPORT="$OUT_DIR/rea_l_hicar_${TAG}.validation.json"
REPORT_PARTIAL="$OUT_DIR/.rea_l_hicar_${TAG}.validation.json.partial.${SLURM_JOB_ID}"
MANIFEST="$OUT_DIR/rea_l_hicar_${TAG}.manifest.json"
MANIFEST_PARTIAL="$OUT_DIR/.rea_l_hicar_${TAG}.manifest.json.partial.${SLURM_JOB_ID}"
VALIDATOR="$CASE_ROOT/validation/validate_forcing.py"
STATIC_DOMAIN="${HICAR_STATIC_DOMAIN:-$CASE_ROOT/static/domain_static_swiss_100m.nc}"
PYTHON="${HICAR_VALIDATION_PYTHON:-$SCRATCH/icon_hicar/venv_static/bin/python}"

for path in "$DYNAMIC" "$DYNAMIC.ready" "$STATIC" "$STATIC.ready" \
            "$SOURCE_MANIFEST" "$GRID_FILE" "$STATIC_DOMAIN" \
            "$STATIC_DOMAIN.ready" "$VALIDATOR" "$PYTHON" \
            "$REPO_ROOT/scripts/prepare_icon_inputs.sh"; do
  [[ -e "$path" ]] || { echo "missing required path: $path" >&2; exit 2; }
done
for path in "$FINAL" "$FINAL.ready" "$REPORT" "$MANIFEST"; do
  [[ ! -e "$path" ]] || {
    echo "refusing to overwrite forcing publication artifact: $path" >&2
    exit 2
  }
done
mkdir -p "$OUT_DIR"
cleanup() {
  rm -f "$PARTIAL" "$PARTIAL.ready" "$PARTIAL.sha256" \
        "$REPORT_PARTIAL" "$MANIFEST_PARTIAL"
}
trap cleanup EXIT

TARGET_GRID=$(<"$GRID_FILE")
"$REPO_ROOT/scripts/prepare_icon_inputs.sh" \
  --input "$DYNAMIC" --static "$STATIC" --output "$PARTIAL" \
  --target-grid "$TARGET_GRID" --model-name rea-l-ch1 --expver 639 \
  --load-balfrin-modules --compression 1 \
  --work-dir "$OUT_DIR/.work_${SLURM_JOB_ID}"

test -s "$PARTIAL"
test -f "$PARTIAL.ready"
rm "$PARTIAL.ready"
EXPECTED_VALID_TIME=$(
  "$PYTHON" -c \
    'from datetime import datetime,timedelta; import sys; print((datetime.strptime(sys.argv[1]+sys.argv[2], "%Y%m%d%H%M")+timedelta(hours=int(sys.argv[3]))).isoformat())' \
    "$REA_DATE" "$REA_TIME" "$REA_STEP"
)
"$PYTHON" "$VALIDATOR" \
  --forcing-file "$PARTIAL" \
  --published-forcing-file "$FINAL" \
  --static-file "$STATIC_DOMAIN" \
  --expected-valid-time "$EXPECTED_VALID_TIME" \
  --report "$REPORT_PARTIAL"
test -s "$REPORT_PARTIAL"
grep -q '"status": "PASS"' "$REPORT_PARTIAL"

FORCING_SHA=$(sha256sum "$PARTIAL" | awk '{print $1}')
SOURCE_MANIFEST_SHA=$(sha256sum "$SOURCE_MANIFEST" | awk '{print $1}')
DYNAMIC_SHA=$(sha256sum "$DYNAMIC" | awk '{print $1}')
STATIC_SHA=$(sha256sum "$STATIC" | awk '{print $1}')
"$PYTHON" - "$MANIFEST_PARTIAL" <<PY
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
record = {
    "status": "PASS",
    "source": "ICON REA-L-CH1 FDB",
    "cycle_date": "$REA_DATE",
    "cycle_time": "$REA_TIME",
    "step_hours": int("$REA_STEP"),
    "valid_time": "$EXPECTED_VALID_TIME",
    "target_grid": "$TARGET_GRID",
    "forcing_file": "$(basename "$FINAL")",
    "forcing_sha256": "$FORCING_SHA",
    "source_manifest": "$SOURCE_MANIFEST",
    "source_manifest_sha256": "$SOURCE_MANIFEST_SHA",
    "dynamic_grib_sha256": "$DYNAMIC_SHA",
    "static_grib_sha256": "$STATIC_SHA",
    "validation_report": "$(basename "$REPORT")",
}
path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\\n")
PY
test -s "$MANIFEST_PARTIAL"

mv "$PARTIAL" "$FINAL"
mv "$REPORT_PARTIAL" "$REPORT"
mv "$MANIFEST_PARTIAL" "$MANIFEST"
touch "$FINAL.ready"
trap - EXIT
printf 'forcing=%s\nready=%s\nsha256=%s\nreport=%s\nmanifest=%s\n' \
  "$FINAL" "$FINAL.ready" "$FORCING_SHA" "$REPORT" "$MANIFEST"
