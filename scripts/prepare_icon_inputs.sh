#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  prepare_icon_inputs.sh --input ICON_GRIB --output OUT.nc [options]

Create one HICAR forcing NetCDF file from one ICON dynamic output file. If the
static geometry is stored in a separate ICON file, provide it with --static.
The script runs locally on the machine where fieldextra and NCO are available;
it does not SSH or submit jobs.

Required:
  -i, --input PATH             ICON dynamic GRIB input, e.g. lfff00010000
  -o, --output PATH            HICAR forcing NetCDF output

Fieldextra/resource options:
  -s, --static PATH            Static ICON GRIB input [input]
      --fieldextra-bin PATH    fieldextra executable
      --resources-dir PATH     fieldextra resource directory
      --sample PATH            GRIB2 sample template
      --icon-grid PATH         ICON grid description file
      --expver VALUE           localNumberOfExperiment metadata [639]
      --target-grid VALUE      fieldextra out_regrid_target string
      --target-method VALUE    fieldextra out_regrid_method [default]
      --model-name VALUE       fieldextra model name [icon-ch1-eps]

Domain/subsetting options:
      --domain-file PATH       HICAR static domain NetCDF; derive a geolatlon
                               fieldextra target grid covering it plus border
      --domain-lat-var NAME    Latitude variable in domain file [lat]
      --domain-lon-var NAME    Longitude variable in domain file [lon]
      --domain-border-km KM    Extra forcing border around HICAR domain [10]
      --domain-dlon-deg DEG    Forcing longitude spacing for derived grid [0.01]
      --domain-dlat-deg DEG    Forcing latitude spacing for derived grid [0.01]
      --target-grid-out PATH   Write derived target-grid string to PATH
      --skip-icon-coverage-check
                               Do not check derived grid against --icon-grid

Level/options:
      --level-min N            First full model level [1]
      --level-max N            Last full model level [80]
      --half-level-min N       First half model level [level-min]
      --half-level-max N       Last half model level [level-max + 1]
      --no-w                   Do not extract/preserve W
      --compression N          NetCDF4 deflate level for final file [0]
      --work-dir DIR           Work directory [temporary directory]
      --keep-work              Do not remove temporary work directory
      --skip-syntaxcheck       Do not run fieldextra --syntaxcheck
      --load-balfrin-modules   Initialize Balfrin modules and load NCO after fieldextra
  -h, --help                   Show this help

Typical Balfrin use:
  scripts/prepare_icon_inputs.sh \
    --input "$SCRATCH/icon_hicar/data/icon_ch1_eps_20260710T18_ctrl_oper_icon_000/lfff00010000" \
    --output "$SCRATCH/icon_hicar/forcing/hicar_forcing_f001.nc" \
    --static "$SCRATCH/icon_hicar/data/icon_ch1_eps_20260710T18_ctrl_oper_icon_000/lfff00000000c" \
    --load-balfrin-modules
EOF
}

die() {
  echo "error: $*" >&2
  exit 2
}

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$SCRIPT_DIR/load_balfrin_site_config.sh"

abs_path() {
  local path=$1
  local dir
  local base
  dir=$(dirname "$path")
  base=$(basename "$path")
  printf '%s/%s\n' "$(cd "$dir" && pwd)" "$base"
}

FIELD_EXTRA_BIN=${FIELD_EXTRA_BIN:-/oprusers/osm/opr.inn/abs/fieldextra_gnu_opt_omp-16.0.0-gcc-12.3.0}
RESOURCES_DIR=${FIELD_EXTRA_RESOURCES:-/oprusers/osm/opr.inn/config/resources}
SAMPLE=${FIELD_EXTRA_SAMPLE:-/oprusers/osm/opr.inn/modules/eccodes_cosmo_resources/2.36.0.3/samples/COSMO_GRIB2_default.tmpl}
ICON_GRID=${ICON_GRID:-/oprusers/osm/opr.inn/data/grid_descriptions/icon_grid_0001_R19B08_mch.nc}
EXPVER="639"
TARGET_GRID="rotlatlon,356860000,-1420000,540000,1060000,20000,20000,190000000,43000000"
TARGET_METHOD="default"
MODEL_NAME="icon-ch1-eps"
DOMAIN_FILE=""
DOMAIN_LAT_VAR="lat"
DOMAIN_LON_VAR="lon"
DOMAIN_BORDER_KM="10"
DOMAIN_DLON_DEG="0.01"
DOMAIN_DLAT_DEG="0.01"
TARGET_GRID_OUT=""
SKIP_ICON_COVERAGE_CHECK=0
LEVEL_MIN=1
LEVEL_MAX=80
HALF_LEVEL_MIN=""
HALF_LEVEL_MAX=""
COMPRESSION=0
WORK_DIR=""
KEEP_WORK=0
SKIP_SYNTAXCHECK=0
INCLUDE_W=1
LOAD_BALFRIN_MODULES=0
DYNAMIC_GRIB=""
STATIC_GRIB=""
OUT_NC=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    -i|--input) DYNAMIC_GRIB=${2:?}; shift 2 ;;
    -s|--static) STATIC_GRIB=${2:?}; shift 2 ;;
    -o|--output) OUT_NC=${2:?}; shift 2 ;;
    --fieldextra-bin) FIELD_EXTRA_BIN=${2:?}; shift 2 ;;
    --resources-dir) RESOURCES_DIR=${2:?}; shift 2 ;;
    --sample) SAMPLE=${2:?}; shift 2 ;;
    --icon-grid) ICON_GRID=${2:?}; shift 2 ;;
    --expver) EXPVER=${2:?}; shift 2 ;;
    --target-grid) TARGET_GRID=${2:?}; shift 2 ;;
    --target-method) TARGET_METHOD=${2:?}; shift 2 ;;
    --model-name) MODEL_NAME=${2:?}; shift 2 ;;
    --domain-file) DOMAIN_FILE=${2:?}; shift 2 ;;
    --domain-lat-var) DOMAIN_LAT_VAR=${2:?}; shift 2 ;;
    --domain-lon-var) DOMAIN_LON_VAR=${2:?}; shift 2 ;;
    --domain-border-km) DOMAIN_BORDER_KM=${2:?}; shift 2 ;;
    --domain-dlon-deg) DOMAIN_DLON_DEG=${2:?}; shift 2 ;;
    --domain-dlat-deg) DOMAIN_DLAT_DEG=${2:?}; shift 2 ;;
    --target-grid-out) TARGET_GRID_OUT=${2:?}; shift 2 ;;
    --skip-icon-coverage-check) SKIP_ICON_COVERAGE_CHECK=1; shift ;;
    --level-min) LEVEL_MIN=${2:?}; shift 2 ;;
    --level-max) LEVEL_MAX=${2:?}; shift 2 ;;
    --half-level-min) HALF_LEVEL_MIN=${2:?}; shift 2 ;;
    --half-level-max) HALF_LEVEL_MAX=${2:?}; shift 2 ;;
    --compression) COMPRESSION=${2:?}; shift 2 ;;
    --work-dir) WORK_DIR=${2:?}; shift 2 ;;
    --keep-work) KEEP_WORK=1; shift ;;
    --skip-syntaxcheck) SKIP_SYNTAXCHECK=1; shift ;;
    --no-w) INCLUDE_W=0; shift ;;
    --load-balfrin-modules) LOAD_BALFRIN_MODULES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[ -n "$DYNAMIC_GRIB" ] || die "--input is required"
[ -n "$OUT_NC" ] || die "--output is required"
rm -f "${OUT_NC}.ready"
if [ -z "$STATIC_GRIB" ]; then
  STATIC_GRIB=$DYNAMIC_GRIB
fi

case "$LEVEL_MIN:$LEVEL_MAX:$COMPRESSION" in
  *[!0-9:]*|"") die "level and compression options must be integers" ;;
esac

[ "$LEVEL_MIN" -le "$LEVEL_MAX" ] || die "--level-min must be <= --level-max"
[ "$COMPRESSION" -ge 0 ] && [ "$COMPRESSION" -le 9 ] || die "--compression must be 0..9"

if [ -z "$HALF_LEVEL_MIN" ]; then
  HALF_LEVEL_MIN=$LEVEL_MIN
fi
if [ -z "$HALF_LEVEL_MAX" ]; then
  HALF_LEVEL_MAX=$((LEVEL_MAX + 1))
fi
case "$HALF_LEVEL_MIN:$HALF_LEVEL_MAX" in
  *[!0-9:]*|"") die "half-level options must be integers" ;;
esac

FULL_COUNT=$((LEVEL_MAX - LEVEL_MIN + 1))
HALF_COUNT=$((HALF_LEVEL_MAX - HALF_LEVEL_MIN + 1))
[ "$HALF_COUNT" -eq $((FULL_COUNT + 1)) ] || \
  die "half-level range must contain exactly one more level than full-level range"

if [ "$LOAD_BALFRIN_MODULES" -eq 1 ]; then
  [ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
  module use "$USER_ENV_ROOT/modules"
fi

for path in "$FIELD_EXTRA_BIN" "$RESOURCES_DIR/dictionary_icon.txt" \
            "$RESOURCES_DIR/eccodes_definitions_cosmo" \
            "$RESOURCES_DIR/eccodes_definitions_vendor" \
            "$SAMPLE" "$ICON_GRID" "$DYNAMIC_GRIB" "$STATIC_GRIB"; do
  [ -e "$path" ] || die "missing required path: $path"
done

DYNAMIC_GRIB=$(abs_path "$DYNAMIC_GRIB")
STATIC_GRIB=$(abs_path "$STATIC_GRIB")
if [ -n "$DOMAIN_FILE" ]; then
  command -v python3 >/dev/null 2>&1 || die "required command not found: python3"
  [ -e "$DOMAIN_FILE" ] || die "missing domain file: $DOMAIN_FILE"
  DOMAIN_FILE=$(abs_path "$DOMAIN_FILE")
  helper_args=(
    --domain-file "$DOMAIN_FILE"
    --lat-var "$DOMAIN_LAT_VAR"
    --lon-var "$DOMAIN_LON_VAR"
    --border-km "$DOMAIN_BORDER_KM"
    --dlon-deg "$DOMAIN_DLON_DEG"
    --dlat-deg "$DOMAIN_DLAT_DEG"
    --icon-grid "$ICON_GRID"
  )
  if [ "$SKIP_ICON_COVERAGE_CHECK" -eq 1 ]; then
    helper_args+=(--skip-icon-coverage-check)
  fi
  TARGET_GRID=$(python3 "$SCRIPT_DIR/hicar_domain_to_fieldextra_grid.py" "${helper_args[@]}")
  if [ -n "$TARGET_GRID_OUT" ]; then
    mkdir -p "$(dirname "$TARGET_GRID_OUT")"
    printf '%s\n' "$TARGET_GRID" > "$TARGET_GRID_OUT"
  fi
fi
OUT_DIR=$(dirname "$OUT_NC")
mkdir -p "$OUT_DIR"
OUT_NC=$(abs_path "$OUT_NC")

if [ -z "$WORK_DIR" ]; then
  WORK_DIR=$(mktemp -d "${TMPDIR:-/tmp}/prepare_icon_inputs.XXXXXX")
else
  mkdir -p "$WORK_DIR"
  WORK_DIR=$(abs_path "$WORK_DIR")
fi

cleanup() {
  if [ "$KEEP_WORK" -eq 0 ] && [ -n "${WORK_DIR:-}" ] && [ -d "$WORK_DIR" ]; then
    rm -rf "$WORK_DIR"
  fi
}
trap cleanup EXIT

has_var() {
  ncks -m -v "$1" "$2" >/dev/null 2>&1
}

has_dim() {
  ncks -m "$2" 2>/dev/null | awk -v dim="$1" '$1 == dim && $2 == "=" { found=1 } END { exit !found }'
}

join_by_comma() {
  local IFS=,
  echo "$*"
}

copy_or_drop_epsd() {
  local in_file=$1
  local out_file=$2
  if has_dim epsd_1 "$in_file"; then
    ncwa -O -a epsd_1 "$in_file" "$out_file"
  else
    cp "$in_file" "$out_file"
  fi
}

load_nco_modules_if_needed() {
  if [ "$LOAD_BALFRIN_MODULES" -eq 1 ]; then
    module load nco/5.0.1-gcc netcdf-c/4.8.1-gcc >/dev/null
  fi
}

check_nco_tools() {
  local cmd
  for cmd in ncwa ncks ncrename ncap2 ncpdq ncatted; do
    command -v "$cmd" >/dev/null 2>&1 || die "required command not found: $cmd"
  done
}

cd "$WORK_DIR"

echo "== ICON to HICAR forcing preprocessing =="
printf 'work=%s\n' "$WORK_DIR"
printf 'dynamic_input=%s\n' "$DYNAMIC_GRIB"
printf 'static_input=%s\n' "$STATIC_GRIB"
printf 'output=%s\n' "$OUT_NC"
if [ -n "$DOMAIN_FILE" ]; then
  printf 'domain_file=%s\n' "$DOMAIN_FILE"
  printf 'domain_border_km=%s domain_dlon_deg=%s domain_dlat_deg=%s\n' \
    "$DOMAIN_BORDER_KM" "$DOMAIN_DLON_DEG" "$DOMAIN_DLAT_DEG"
fi
printf 'levels=%s..%s half_levels=%s..%s\n' \
  "$LEVEL_MIN" "$LEVEL_MAX" "$HALF_LEVEL_MIN" "$HALF_LEVEL_MAX"
printf 'target_grid=%s\n' "$TARGET_GRID"

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
 dictionary = "$RESOURCES_DIR/dictionary_icon.txt"
 grib_definition_path = "$RESOURCES_DIR/eccodes_definitions_cosmo",
                        "$RESOURCES_DIR/eccodes_definitions_vendor"
 grib2_sample = "$SAMPLE"
 icon_grid_description = "$ICON_GRID"
/

&GlobalSettings
 default_model_name = "$MODEL_NAME"
 default_out_type_stdlongitude = .true.
 default_out_type_justontime = "no"
 auxiliary_metainfo = "localNumberOfExperiment=$EXPVER"
/

&ModelSpecification
 model_name = "$MODEL_NAME"
 regrid_method = "__ALL__:icontools,nnb_strict"
 earth_axis_large = 6371229.
 earth_axis_small = 6371229.
/
EOF

cat common_header.nl > dynamic.nl
cat >> dynamic.nl <<EOF

&Process
 in_file = "$DYNAMIC_GRIB"
 out_regrid_target = "$TARGET_GRID"
 out_regrid_method = "$TARGET_METHOD"
 out_file = "dynamic.nc"
 out_type = "NETCDF"
/
&Process in_field = "U",  levmin = $LEVEL_MIN, levmax = $LEVEL_MAX /
&Process in_field = "V",  levmin = $LEVEL_MIN, levmax = $LEVEL_MAX /
&Process in_field = "T",  levmin = $LEVEL_MIN, levmax = $LEVEL_MAX /
&Process in_field = "P",  levmin = $LEVEL_MIN, levmax = $LEVEL_MAX /
&Process in_field = "QV", levmin = $LEVEL_MIN, levmax = $LEVEL_MAX /
EOF

if [ "$INCLUDE_W" -eq 1 ]; then
  cat >> dynamic.nl <<EOF
&Process in_field = "W",  levmin = $HALF_LEVEL_MIN, levmax = $HALF_LEVEL_MAX /
EOF
fi

cat common_header.nl > static.nl
cat >> static.nl <<EOF

&Process
 in_file = "$STATIC_GRIB"
 out_regrid_target = "$TARGET_GRID"
 out_regrid_method = "$TARGET_METHOD"
 out_file = "static.nc"
 out_type = "NETCDF"
/
&Process in_field = "HEIGHT", levmin = $HALF_LEVEL_MIN, levmax = $HALF_LEVEL_MAX, level_class = "k_half" /
&Process in_field = "HSURF" /
&Process in_field = "FR_LAND" /
EOF

run_fieldextra() {
  local nl=$1
  local stem=${nl%.nl}
  ulimit -s unlimited 2>/dev/null || true
  export OMP_STACKSIZE=${OMP_STACKSIZE:-500M}

  if [ "$SKIP_SYNTAXCHECK" -eq 0 ]; then
    echo
    echo "== syntaxcheck: $nl =="
    "$FIELD_EXTRA_BIN" --syntaxcheck "$nl" > "${stem}.syntax.log" 2>&1
  fi

  echo
  echo "== fieldextra: $nl =="
  rm -f fieldextra.diagnostic
  "$FIELD_EXTRA_BIN" "$nl" > "${stem}.run.log" 2>&1
  [ -f fieldextra.diagnostic ] && mv fieldextra.diagnostic "${stem}.diagnostic"
}

run_fieldextra dynamic.nl
run_fieldextra static.nl

load_nco_modules_if_needed
check_nco_tools

copy_or_drop_epsd dynamic.nc hicar_work.nc
copy_or_drop_epsd static.nc static_noepsd.nc

append_vars=()
for var in HSURF FR_LAND; do
  if has_var "$var" static_noepsd.nc; then
    append_vars+=("$var")
  else
    echo "warning: static variable not found and will not be appended: $var" >&2
  fi
done
if [ "${#append_vars[@]}" -gt 0 ]; then
  var_csv=$(join_by_comma "${append_vars[@]}")
  ncks -A -v "$var_csv" static_noepsd.nc hicar_work.nc
fi

has_var HEIGHT static_noepsd.nc || die "static fieldextra output does not contain HEIGHT"
ncks -O -v HEIGHT static_noepsd.nc hhl_work.nc
ncrename -O -d z_1,z_2 -v z_1,z_2 -v HEIGHT,HHL hhl_work.nc
ncks -A -v HHL hhl_work.nc hicar_work.nc

full_last=$((FULL_COUNT - 1))
half_last=$FULL_COUNT
ncks -O -v HEIGHT static_noepsd.nc hfl_work.nc
ncap2 -O -s "defdim(\"z_hicar\",$FULL_COUNT); HFL[\$z_hicar,\$y_1,\$x_1]=0.5f*(HEIGHT(0:$full_last,:,:)+HEIGHT(1:$half_last,:,:));" \
  hfl_work.nc hfl_work.nc
ncks -O -v HFL hfl_work.nc hfl_only.nc
ncrename -O -d z_hicar,z_1 hfl_only.nc
ncks -A hfl_only.nc hicar_work.nc

ncpdq -O -a time,-z_1,-z_2,y_1,x_1 hicar_work.nc hicar_zrev.nc
ncrename -O -d z_1,z -v z_1,z -d z_2,z_hl -v z_2,z_hl hicar_zrev.nc

ncatted_args=(
  -a positive,z,d,,
  -a positive,z_hl,d,,
  -a valid_max,z,d,,
  -a valid_max,z_hl,d,,
  -a uid,z,d,,
  -a uid,z_hl,d,,
  -a uuid,z,d,,
  -a uuid,z_hl,d,,
  -a bounds,z,d,,
)
for var in P QV T U V W HFL HHL HSURF FR_LAND; do
  if has_var "$var" hicar_zrev.nc; then
    ncatted_args+=(-a "cell_methods,$var,d,,")
  fi
done
ncatted_args+=(
  -a long_name,HFL,o,c,"geometric height on full levels"
  -a units,HFL,o,c,"m"
  -a long_name,HHL,o,c,"geometric height on half levels"
  -a units,HHL,o,c,"m"
)
ncatted -O "${ncatted_args[@]}" hicar_zrev.nc

exclude_vars=()
for var in epsd_1 z_bnds_1; do
  if has_var "$var" hicar_zrev.nc; then
    exclude_vars+=("$var")
  fi
done

if [ "${#exclude_vars[@]}" -gt 0 ]; then
  exclude_csv=$(join_by_comma "${exclude_vars[@]}")
  ncks -4 -L "$COMPRESSION" -O -x -v "$exclude_csv" hicar_zrev.nc "$OUT_NC"
else
  ncks -4 -L "$COMPRESSION" -O hicar_zrev.nc "$OUT_NC"
fi

echo
echo "== complete =="
touch "${OUT_NC}.ready"
printf 'output=%s\n' "$OUT_NC"
printf 'ready=%s\n' "${OUT_NC}.ready"
if [ "$KEEP_WORK" -eq 1 ]; then
  printf 'work=%s\n' "$WORK_DIR"
fi
