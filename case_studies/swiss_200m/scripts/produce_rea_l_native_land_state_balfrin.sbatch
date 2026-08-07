#!/bin/bash -l
# Retrieve one REA-L land state and remap native ICON cells directly to HICAR.
#SBATCH --job-name=rea-l-native-land
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=48G
#SBATCH --time=01:00:00

set -euo pipefail
repo_root=${REPO_ROOT:-$SCRATCH/icon_hicar}
. "$repo_root/scripts/load_balfrin_site_config.sh"
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"

date=${REA_L_LAND_DATE:-20200702}
time=${REA_L_LAND_TIME:-0000}
cycle_date=${REA_L_LAND_CYCLE_DATE:-$date}
cycle_time=${REA_L_LAND_CYCLE_TIME:-$time}
step_hours=${REA_L_LAND_STEP_HOURS:-0}
target_static=${TARGET_STATIC:?TARGET_STATIC is required}
icon_extpar=${ICON_EXTPAR:-/oprusers/osm/opr.inn/data/ICON_INPUT/ICON-CH1-EPS/external_parameter_icon_grid_0001_R19B08_mch_tuned.nc}
fdb_image=${REA_FDB_IMAGE_OVERRIDE:-fdb/5.21:v1}
row_chunk=${EARTHKIT_ROW_CHUNK:-32}
output_dir=${REA_L_NATIVE_OUTPUT_DIR:-$SCRATCH/icon_hicar/qualification/native_land_init_v1}
work_root=${REA_L_LAND_WORK_ROOT:-$SCRATCH/icon_hicar/tmp}
remapper=${REMAPPER:-$repo_root/case_studies/swiss_200m/validation/remap_rea_l_native_land_state.py}

for path in "$target_static" "$icon_extpar" "$remapper"; do
  test -s "$path" || { echo "missing or empty input: $path" >&2; exit 2; }
done
case "$date:$time:$cycle_date:$cycle_time:$step_hours:$row_chunk" in
  *[!0-9:]*|"") echo "date/time/step/chunk values must be numeric" >&2; exit 2 ;;
esac
(( step_hours >= 0 && step_hours <= 24 && row_chunk > 0 )) || exit 2

mkdir -p "$output_dir" "$work_root"
output=$output_dir/rea_l_native_land_state_${date}_${time}.nc
manifest=$output_dir/rea_l_native_land_state_${date}_${time}.manifest.json
test ! -e "$output" && test ! -e "$output.ready" || {
  echo "refusing to overwrite published output: $output" >&2
  exit 2
}
target_sha=$(sha256sum "$target_static" | awk '{print $1}')
mir_cache=$SCRATCH/icon_hicar/cache/mir/native_land/${target_sha}/rows_${row_chunk}
mkdir -p "$mir_cache"

work=$(mktemp -d "$work_root/rea_l_native_land.XXXXXX")
trap 'rm -rf "$work"' EXIT
cat > "$work/surface.mars" <<EOF
retrieve,
  class=rd,
  stream=reanl,
  expver=r001,
  model=icon-rea-l-ch1,
  type=cf,
  date=$cycle_date,
  time=$cycle_time,
  levtype=sfc,
  param=500044/500147/502336,
  step=$step_hours
EOF
cat > "$work/soil_temperature.mars" <<EOF
retrieve,
  class=rd,
  stream=reanl,
  expver=r001,
  model=icon-rea-l-ch1,
  type=cf,
  date=$cycle_date,
  time=$cycle_time,
  levtype=dp,
  levelist=0/0.005/0.02/0.06/0.18/0.54/1.62/4.86,
  param=500166,
  step=$step_hours
EOF
cat > "$work/soil_water.mars" <<EOF
retrieve,
  class=rd,
  stream=reanl,
  expver=r001,
  model=icon-rea-l-ch1,
  type=cf,
  date=$cycle_date,
  time=$cycle_time,
  levtype=dp,
  levelist=0/0.01/0.03/0.09/0.27/0.81/2.43/7.29,
  param=500167,
  step=$step_hours
EOF
for name in surface soil_temperature soil_water; do
  uenv run --view=rea-l-ch1 "$fdb_image" -- \
    fdb-read --statistics "$work/$name.mars" "$work/$name.grib"
done

export OMP_NUM_THREADS=${SLURM_CPUS_PER_TASK:-8}
uenv run --view=rea-l-ch1 "$fdb_image" -- python "$remapper" \
  --target-static "$target_static" \
  --icon-extpar "$icon_extpar" \
  --surface-grib "$work/surface.grib" \
  --soil-temperature-grib "$work/soil_temperature.grib" \
  --soil-water-grib "$work/soil_water.grib" \
  --output "$output" \
  --manifest "$manifest" \
  --mir-cache "$mir_cache" \
  --row-chunk "$row_chunk"
