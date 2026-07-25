#!/usr/bin/env bash
set -eo pipefail

# Copy and inspect a small ICON-CH1-EPS CTRL archive sample on Balfrin.
# Usage:
#   scripts/balfrin_prepare_icon_ch1_eps_sample.sh

ssh -o BatchMode=yes -o ConnectTimeout=10 balfrin 'bash -s' <<'REMOTE'
set -eo pipefail

run=/store_new/mch/msopr/osm/ICON-CH1-EPS/FCST26/26071018_639
src="$run/grib"
surf_dst="$SCRATCH/icon_hicar/data/icon_ch1_eps_20260710T18_ctrl_i1eff"
ml_dst="$SCRATCH/icon_hicar/data/icon_ch1_eps_20260710T18_ctrl_i1effk"

[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"
set +u
. ~osm/.opr_setup_dir
set -u
module use "$OPR_SETUP_DIR/modules"
module load eccodes/2.36.4-gcc
module load modulefiles/eccodes_cosmo_resources/2.36.0.3
export GRIB_DEFINITION_PATH="$ECCODES_DEFINITION_PATH"

echo "== archive source =="
ls -ld "$run" "$src"

copy_family() {
  local pattern=$1
  local dst=$2
  mkdir -p "$dst"
  find "$src" -maxdepth 1 -type f -name "$pattern" -printf '%f\n' | sort > "$dst/files.expected"
  rsync -a --files-from="$dst/files.expected" "$src/" "$dst/"
  find "$dst" -maxdepth 1 -type f -name "$pattern" -printf '%f %s\n' | sort > "$dst/files.copied.sizes"
  local count bytes
  count=$(find "$dst" -maxdepth 1 -type f -name "$pattern" | wc -l)
  bytes=$(find "$dst" -maxdepth 1 -type f -name "$pattern" -printf '%s\n' | awk '{s+=$1} END {print s+0}')
  printf '%s count=%s bytes=%s GiB=%.3f\n' "$dst" "$count" "$bytes" "$(awk -v b="$bytes" 'BEGIN {print b/1024/1024/1024}')"
}

echo
echo "== copy =="
copy_family 'i1eff????????_000' "$surf_dst"
copy_family 'i1eff????????_000k' "$ml_dst"

inspect_family() {
  local pattern=$1
  local dst=$2
  local bad=0
  : > "$dst/shortnames.all"
  for f in "$dst"/$pattern; do
    base=$(basename "$f")
    grib_ls -p shortName,paramId,typeOfLevel,level,stepType,stepRange,startStep,endStep "$f" > "$dst/$base.grib_ls.txt"
    unknown=$(awk 'NR>1 && $1 ~ /unknown|UNKNOWN/ {c++} END {print c+0}' "$dst/$base.grib_ls.txt")
    awk 'NR>1 && $1 !~ /^[0-9]+$/ && $1 != "of" && $1 != "shortName" {print $1}' "$dst/$base.grib_ls.txt" >> "$dst/shortnames.all"
    printf '%s messages=%s unknown_shortName=%s\n' "$base" "$(grib_count "$f")" "$unknown"
    if [ "$unknown" -ne 0 ]; then bad=1; fi
  done | tee "$dst/decode.summary"
  sort -u "$dst/shortnames.all" > "$dst/shortnames.unique"
  return "$bad"
}

echo
echo "== inspect surface/single-level files =="
inspect_family 'i1eff????????_000' "$surf_dst"

echo
echo "== inspect model-level files =="
inspect_family 'i1eff????????_000k' "$ml_dst"

echo
echo "== key surface fields in +1h file =="
grib_ls -p shortName,paramId,name,units,typeOfLevel,level,stepType,stepRange,startStep,endStep,dataDate,dataTime,validityDate,validityTime \
  "$surf_dst/i1eff00010000_000" \
  | grep -E 'T_2M|U_10M|V_10M|TOT_PREC|ASWDIR_S|ASWDIFD_S|CLCT|PMSL|PS' \
  | sed -n '1,120p'

echo
echo "== destinations =="
printf '%s\n%s\n' "$surf_dst" "$ml_dst"
REMOTE
