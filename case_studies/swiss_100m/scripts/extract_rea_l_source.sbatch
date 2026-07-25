#!/bin/bash -l
# Extract one REA-L timestamp into HICAR forcing-converter source bundles.
# Submit manually, e.g.:
# sbatch --export=ALL,REA_DATE=20100101,REA_TIME=0000,REA_STEP=0 scripts/extract_rea_l_source.sbatch
#SBATCH --job-name=rea_l_ch1_source
#SBATCH --partition=pp-short
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --time=00:30:00
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

CASE_ROOT="${CASE_ROOT:-$SCRATCH/icon_hicar/case_studies/swiss_100m}"
if [[ "$REA_STEP" == 0 ]]; then
  TAG="${REA_DATE}_${REA_TIME}"
else
  TAG="${REA_DATE}_${REA_TIME}_s$(printf '%02d' "$REA_STEP")"
fi
OUT_DIR="$CASE_ROOT/source/rea_l_ch1/$TAG"
# Invoke the site client directly.  A user-shell `uenv` function can point to
# an older client than the current FDB image metadata supports.
UENV_BIN="${UENV_BIN:-/usr/bin/uenv}"
[[ -x "$UENV_BIN" ]] || { echo "missing uenv client: $UENV_BIN" >&2; exit 2; }
IMAGE="${REA_FDB_IMAGE:-fdb/5.19:v2}"
mkdir -p "$OUT_DIR"

DYNAMIC="$OUT_DIR/rea_l_ch1_${TAG}_dynamic.grib"
STATIC="$OUT_DIR/rea_l_ch1_${TAG}_static.grib"
MANIFEST="$OUT_DIR/source_manifest.json"
BASE_TAG="${REA_DATE}_${REA_TIME}"
BASE_DIR="$CASE_ROOT/source/rea_l_ch1/$BASE_TAG"
BASE_STATIC="$BASE_DIR/rea_l_ch1_${BASE_TAG}_static.grib"
for output in "$DYNAMIC" "$STATIC" "$MANIFEST"; do
  [[ ! -e "$output" && ! -e "$output.ready" ]] || {
    echo "refusing to overwrite published or candidate output: $output" >&2
    exit 2
  }
done

WORK="$OUT_DIR/.extract_${SLURM_JOB_ID}.partial"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK"
cd "$WORK"

cat > full_levels.mars <<EOF
retrieve,
  class=rd,
  stream=reanl,
  expver=r001,
  model=icon-rea-l-ch1,
  type=cf,
  date=$REA_DATE,
  time=$REA_TIME,
  levtype=ml,
  levelist=1/to/80,
  param=500001/500014/500028/500030/500035,
  step=$REA_STEP
EOF
cat > w_levels.mars <<EOF
retrieve,
  class=rd,
  stream=reanl,
  expver=r001,
  model=icon-rea-l-ch1,
  type=cf,
  date=$REA_DATE,
  time=$REA_TIME,
  levtype=ml,
  levelist=1/to/81,
  param=500032,
  step=$REA_STEP
EOF
if [[ "$REA_STEP" == 0 ]]; then
  cat > hhl_levels.mars <<EOF
retrieve,
  class=rd,
  stream=reanl,
  expver=r001,
  model=icon-rea-l-ch1,
  type=cf,
  date=$REA_DATE,
  time=$REA_TIME,
  levtype=ml,
  levelist=1/to/81,
  param=500008,
  step=0
EOF
  cat > surface.mars <<EOF
retrieve,
  class=rd,
  stream=reanl,
  expver=r001,
  model=icon-rea-l-ch1,
  type=cf,
  date=$REA_DATE,
  time=$REA_TIME,
  levtype=sfc,
  param=500007/500054,
  step=0
EOF
else
  for path in "$BASE_STATIC" "$BASE_STATIC.ready"; do
    [[ -e "$path" ]] || {
      echo "step $REA_STEP requires the published cycle-static bundle: $path" >&2
      exit 2
    }
  done
fi

"$UENV_BIN" run --view=rea-l-ch1 "$IMAGE" -- fdb-read --statistics full_levels.mars full_levels.grib
"$UENV_BIN" run --view=rea-l-ch1 "$IMAGE" -- fdb-read --statistics w_levels.mars w_levels.grib
sources=(full_levels.grib w_levels.grib)
if [[ "$REA_STEP" == 0 ]]; then
  "$UENV_BIN" run --view=rea-l-ch1 "$IMAGE" -- fdb-read --statistics hhl_levels.mars hhl_levels.grib
  "$UENV_BIN" run --view=rea-l-ch1 "$IMAGE" -- fdb-read --statistics surface.mars surface.grib
  sources+=(hhl_levels.grib surface.grib)
fi
for source in "${sources[@]}"; do
  test -s "$source" || { echo "empty FDB result: $source" >&2; exit 1; }
done

full_count=$("$UENV_BIN" run --view=rea-l-ch1 "$IMAGE" -- grib_count full_levels.grib | tail -n 1)
w_count=$("$UENV_BIN" run --view=rea-l-ch1 "$IMAGE" -- grib_count w_levels.grib | tail -n 1)
[[ "$full_count" == 400 && "$w_count" == 81 ]] || {
  echo "unexpected dynamic message counts: full=$full_count w=$w_count" >&2
  exit 1
}

cat full_levels.grib w_levels.grib > dynamic.grib
dynamic_count=$("$UENV_BIN" run --view=rea-l-ch1 "$IMAGE" -- grib_count dynamic.grib | tail -n 1)
[[ "$dynamic_count" == 481 ]] || {
  echo "unexpected dynamic bundle message count: dynamic=$dynamic_count" >&2
  exit 1
}
if [[ "$REA_STEP" == 0 ]]; then
  hhl_count=$("$UENV_BIN" run --view=rea-l-ch1 "$IMAGE" -- grib_count hhl_levels.grib | tail -n 1)
  surface_count=$("$UENV_BIN" run --view=rea-l-ch1 "$IMAGE" -- grib_count surface.grib | tail -n 1)
  [[ "$hhl_count" == 81 && "$surface_count" == 2 ]] || {
    echo "unexpected static message counts: hhl=$hhl_count surface=$surface_count" >&2
    exit 1
  }
  cat hhl_levels.grib surface.grib > static.grib
  static_count=$("$UENV_BIN" run --view=rea-l-ch1 "$IMAGE" -- grib_count static.grib | tail -n 1)
  [[ "$static_count" == 83 ]] || {
    echo "unexpected static bundle message count: static=$static_count" >&2
    exit 1
  }
fi

mv dynamic.grib "$DYNAMIC"
if [[ "$REA_STEP" == 0 ]]; then
  mv static.grib "$STATIC"
  static_reused=false
  static_source="$(basename "$STATIC")"
else
  cp --reflink=auto --preserve=mode,timestamps "$BASE_STATIC" "$STATIC"
  static_reused=true
  static_source="$BASE_STATIC"
fi
dynamic_sha=$(sha256sum "$DYNAMIC" | awk '{print $1}')
static_sha=$(sha256sum "$STATIC" | awk '{print $1}')
cat > "$WORK/source_manifest.json" <<EOF
{
  "source": "ICON REA-L-CH1 FDB",
  "date": "$REA_DATE",
  "time": "$REA_TIME",
  "step_hours": $REA_STEP,
  "fdb_image": "$IMAGE",
  "native_grid_points": 1147980,
  "dynamic": {"path": "$(basename "$DYNAMIC")", "messages": 481, "sha256": "$dynamic_sha"},
  "static": {"path": "$(basename "$STATIC")", "messages": 83, "sha256": "$static_sha", "reused": $static_reused, "source": "$static_source"}
}
EOF
mv "$WORK/source_manifest.json" "$MANIFEST"
touch "$DYNAMIC.ready" "$STATIC.ready"
echo "published dynamic=$DYNAMIC static=$STATIC manifest=$MANIFEST"
