#!/bin/bash -l
# Translate a direct native-grid REA-L land state into HICAR cold-start fields.
#SBATCH --job-name=rea-l-native-init
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=24G
#SBATCH --time=00:30:00

set -euo pipefail
repo_root=${REPO_ROOT:-$SCRATCH/icon_hicar}
. "$repo_root/scripts/load_balfrin_site_config.sh"
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"

valid_time=${VALID_TIME:?VALID_TIME is required, for example 2020-07-02T00:00:00}
static_template=${TARGET_STATIC:?TARGET_STATIC is required}
land_state=${LAND_STATE:?LAND_STATE is required}
soil_water_mapping=${SOIL_WATER_MAPPING:-absolute}
noahmp_table=${NOAHMP_TABLE:-}
output_dir=${OUTPUT_DIR:-$SCRATCH/icon_hicar/qualification/native_land_init_v1/initialized}
initializer=${INITIALIZER:-$repo_root/case_studies/swiss_200m/validation/build_rea_l_land_initialization.py}
fdb_image=${REA_FDB_IMAGE_OVERRIDE:-fdb/5.21:v1}
lapse_rate=${SURFACE_TEMPERATURE_LAPSE_RATE_K_M:-}

case "$soil_water_mapping" in
  absolute|smi) ;;
  *) echo "SOIL_WATER_MAPPING must be absolute or smi" >&2; exit 2 ;;
esac

for path in "$static_template" "$land_state" "$initializer"; do
  test -s "$path" || { echo "missing or empty input: $path" >&2; exit 2; }
done
if [[ "$soil_water_mapping" == smi ]]; then
  test -s "$noahmp_table" || {
    echo "NOAHMP_TABLE is required for SMI mapping" >&2
    exit 2
  }
fi
test -e "$land_state.ready" || {
  echo "direct native land state is not published: $land_state.ready" >&2
  exit 2
}

stamp=${valid_time//[-:T]/}
static_name=${static_template##*/}
static_stem=${static_name%.nc}
output=$output_dir/${static_stem}_rea_l_native_${stamp}.nc
manifest=$output_dir/${static_stem}_rea_l_native_${stamp}.manifest.json
test ! -e "$output" && test ! -e "$output.ready" || {
  echo "refusing to overwrite published output: $output" >&2
  exit 2
}
mkdir -p "$output_dir"

arguments=(
  --static-template "$static_template"
  --land-state "$land_state"
  --output "$output"
  --manifest "$manifest"
  --valid-time "$valid_time"
  --source-lat-var lat
  --source-lon-var lon
  --soil-water-mapping "$soil_water_mapping"
  --source-topography-var HSURF
)
if [[ "$soil_water_mapping" == smi ]]; then
  arguments+=(--source-smi-var SMI --noahmp-table "$noahmp_table")
else
  arguments+=(--soil-water-var W_SO)
fi
if [[ -n "$lapse_rate" ]]; then
  arguments+=(--surface-temperature-lapse-rate-k-m "$lapse_rate")
fi

uenv run --view=rea-l-ch1 "$fdb_image" -- python "$initializer" "${arguments[@]}"
