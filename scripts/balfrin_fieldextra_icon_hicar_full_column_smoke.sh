#!/usr/bin/env bash
set -eo pipefail

# Smoke-test full-column ICON -> structured-grid NetCDF output for HICAR forcing.
#
# This script runs on Balfrin through passwordless SSH and uses the operational
# fieldextra executable. It does not compile fieldextra.
#
# Defaults use the copied ICON-CH1-EPS CTRL forecast initialized 2026-07-10
# 18 UTC, using the hourly lfff full-column product.

remote_env=()
for name in FX_BIN FX_RES FX_SAMPLE ICON_GRID ICON_GRIB_DIR LEAD EXPVER DYN_FILE CONST_FILE \
            TARGET_GRID TARGET_METHOD LEVEL_MIN LEVEL_MAX RUN_ID WORK_PARENT OMP_STACKSIZE \
            OMP_PLACES OMP_PROC_BIND; do
  if [ "${!name+x}" ]; then
    remote_env+=("$name=${!name}")
  fi
done

ssh -o BatchMode=yes -o ConnectTimeout=10 balfrin env "${remote_env[@]}" 'bash -s' <<'REMOTE'
set -eo pipefail

[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"

FX_BIN="${FX_BIN:-/oprusers/osm/opr.inn/abs/fieldextra_gnu_opt_omp-16.0.0-gcc-12.3.0}"
FX_RES="${FX_RES:-/oprusers/osm/opr.inn/config/resources}"
FX_SAMPLE="${FX_SAMPLE:-/oprusers/osm/opr.inn/modules/eccodes_cosmo_resources/2.36.0.3/samples/COSMO_GRIB2_default.tmpl}"
ICON_GRID="${ICON_GRID:-/oprusers/osm/opr.inn/data/grid_descriptions/icon_grid_0001_R19B08_mch.nc}"

ICON_GRIB_DIR="${ICON_GRIB_DIR:-$SCRATCH/icon_hicar/data/icon_ch1_eps_20260710T18_ctrl_oper_icon_000}"
LEAD="${LEAD:-00010000}"
EXPVER="${EXPVER:-639}"

DYN_FILE="${DYN_FILE:-$ICON_GRIB_DIR/lfff${LEAD}}"
CONST_FILE="${CONST_FILE:-$ICON_GRIB_DIR/lfff00000000c}"
TARGET_GRID="${TARGET_GRID:-rotlatlon,356860000,-1420000,540000,1060000,20000,20000,190000000,43000000}"
TARGET_METHOD="${TARGET_METHOD:-default}"
LEVEL_MIN="${LEVEL_MIN:-1}"
LEVEL_MAX="${LEVEL_MAX:-80}"

RUN_ID="${RUN_ID:-$(date +%Y%m%dT%H%M%S)}"
WORK_PARENT="${WORK_PARENT:-$SCRATCH/icon_hicar/fieldextra_full_column_smoke}"
WORK="$WORK_PARENT/$RUN_ID"
mkdir -p "$WORK"
cd "$WORK"

echo "== fieldextra full-column smoke environment =="
printf 'host=%s\n' "$(hostname)"
printf 'user=%s\n' "$(whoami)"
printf 'work=%s\n' "$PWD"
printf 'fieldextra=%s\n' "$FX_BIN"
printf 'dyn_file=%s\n' "$DYN_FILE"
printf 'const_file=%s\n' "$CONST_FILE"
printf 'target_grid=%s\n' "$TARGET_GRID"
printf 'levels=%s..%s\n' "$LEVEL_MIN" "$LEVEL_MAX"

for path in "$FX_BIN" "$FX_RES/dictionary_icon.txt" "$FX_RES/eccodes_definitions_cosmo" \
            "$FX_RES/eccodes_definitions_vendor" "$FX_SAMPLE" "$ICON_GRID" \
            "$DYN_FILE" "$CONST_FILE"; do
  if [ ! -e "$path" ]; then
    echo "missing required path: $path" >&2
    exit 2
  fi
done

ulimit -s unlimited
export OMP_STACKSIZE="${OMP_STACKSIZE:-500M}"
export OMP_PLACES="${OMP_PLACES:-sockets}"
export OMP_PROC_BIND="${OMP_PROC_BIND:-spread,close,close}"

cat > common_header.nl <<EOF
&RunSpecification
 strict_nl_parsing = .true.
 verbosity = "moderate"
 additional_diagnostic = .true.
 diagnostic_length = 110
 soft_memory_limit = 40.0
 n_ompthread_total = 4
 n_ompthread_collect = 1
 n_ompthread_generate = 1
/

&GlobalResource
 dictionary = "$FX_RES/dictionary_icon.txt"
 grib_definition_path = "$FX_RES/eccodes_definitions_cosmo",
                        "$FX_RES/eccodes_definitions_vendor"
 grib2_sample = "$FX_SAMPLE"
 icon_grid_description = "$ICON_GRID"
/

&GlobalSettings
 default_model_name = "icon-ch1-eps"
 default_out_type_stdlongitude = .true.
 default_out_type_justontime = "no"
 auxiliary_metainfo = "localNumberOfExperiment=$EXPVER"
/

&ModelSpecification
 model_name = "icon-ch1-eps"
 regrid_method = "__ALL__:icontools,nnb_strict"
 earth_axis_large = 6371229.
 earth_axis_small = 6371229.
/
EOF

cat common_header.nl > full_column_dynamic.nl
cat >> full_column_dynamic.nl <<EOF

&Process
 in_file = "$DYN_FILE"
 out_regrid_target = "$TARGET_GRID"
 out_regrid_method = "$TARGET_METHOD"
 out_file = "icon_full_column_dynamic.nc"
 out_type = "NETCDF"
/
&Process in_field = "U",  levmin = $LEVEL_MIN, levmax = $LEVEL_MAX /
&Process in_field = "V",  levmin = $LEVEL_MIN, levmax = $LEVEL_MAX /
&Process in_field = "T",  levmin = $LEVEL_MIN, levmax = $LEVEL_MAX /
&Process in_field = "P",  levmin = $LEVEL_MIN, levmax = $LEVEL_MAX /
&Process in_field = "QV", levmin = $LEVEL_MIN, levmax = $LEVEL_MAX /
&Process in_field = "W",  levmin = 1, levmax = 81 /
EOF

cat common_header.nl > full_column_static.nl
cat >> full_column_static.nl <<EOF

&Process
 in_file = "$CONST_FILE"
 out_regrid_target = "$TARGET_GRID"
 out_regrid_method = "$TARGET_METHOD"
 out_file = "icon_static_geometry.nc"
 out_type = "NETCDF"
/
&Process in_field = "HEIGHT", levmin = 1, levmax = 81, level_class = "k_half" /
&Process in_field = "HSURF" /
&Process in_field = "FR_LAND" /
EOF

run_fx() {
  local nl=$1
  local stem=${nl%.nl}
  echo
  echo "== syntaxcheck: $nl =="
  "$FX_BIN" --syntaxcheck "$nl" > "${stem}.syntax.log" 2>&1
  echo "syntaxcheck ok"

  echo
  echo "== run: $nl =="
  rm -f fieldextra.diagnostic
  "$FX_BIN" "$nl" > "${stem}.run.log" 2>&1
  [ -f fieldextra.diagnostic ] && mv fieldextra.diagnostic "${stem}.diagnostic"
  echo "run ok"
}

run_fx full_column_dynamic.nl
run_fx full_column_static.nl

module load netcdf-c/4.8.1-gcc >/dev/null

echo
echo "== outputs =="
ls -lh *.nc *.log *.diagnostic

echo
echo "== icon_full_column_dynamic.nc =="
ncdump -h icon_full_column_dynamic.nc | sed -n '1,180p'

echo
echo "== icon_static_geometry.nc =="
ncdump -h icon_static_geometry.nc | sed -n '1,180p'

ln -sfn "$WORK" "$WORK_PARENT/latest"

echo
echo "== complete =="
printf 'work=%s\nlatest=%s\n' "$WORK" "$WORK_PARENT/latest"
REMOTE
