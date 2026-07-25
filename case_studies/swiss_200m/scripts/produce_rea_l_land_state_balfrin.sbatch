#!/bin/bash -l
# Retrieve and spatially regrid one REA-L land/snow state for HICAR initialization.
#SBATCH --job-name=rea-l-land-producer
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --time=00:30:00

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module load nco/5.0.1-gcc netcdf-c/4.8.1-gcc >/dev/null

repo_root=${REPO_ROOT:-$SCRATCH/icon_hicar}
date=${REA_L_LAND_DATE:-20200701}
time=${REA_L_LAND_TIME:-0000}
case "$date:$time" in
  *[!0-9:]*|"") echo "REA_L_LAND_DATE/TIME must be numeric" >&2; exit 2 ;;
esac
test "${#date}" -eq 8 && test "${#time}" -eq 4 || {
  echo "REA_L_LAND_DATE/TIME must be YYYYMMDD/HHMM" >&2
  exit 2
}

case_root=$repo_root/case_studies/swiss_200m
target_grid_file=$case_root/config/fieldextra_target_grid.txt
output_dir=${REA_L_LAND_OUTPUT_DIR:-$repo_root/streaming/rea_l_ch1/land_state}
output=$output_dir/rea_l_land_state_${date}_${time}.nc
manifest=$output_dir/rea_l_land_state_${date}_${time}.manifest.json
uenv_bin=${UENV_BIN:-/usr/bin/uenv}
fdb_image=${REA_FDB_IMAGE:-fdb/5.19:v2}
fieldextra=${FIELD_EXTRA_BIN:-/oprusers/osm/opr.inn/abs/fieldextra_gnu_opt_omp-16.0.0-gcc-12.3.0}
resources=${FIELD_EXTRA_RESOURCES:-/oprusers/osm/opr.inn/config/resources}
sample=${FIELD_EXTRA_SAMPLE:-/oprusers/osm/opr.inn/modules/eccodes_cosmo_resources/2.36.0.3/samples/COSMO_GRIB2_default.tmpl}
icon_grid=${ICON_GRID:-/oprusers/osm/opr.inn/data/grid_descriptions/icon_grid_0001_R19B08_mch.nc}

for path in "$target_grid_file" "$fieldextra" "$resources/dictionary_icon.txt" \
  "$sample" "$icon_grid"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done
mkdir -p "$output_dir" "$repo_root/tmp"
test ! -e "$output" && test ! -e "$output.ready" || {
  echo "refusing to overwrite published land state: $output" >&2
  exit 2
}
work=$(mktemp -d "$repo_root/tmp/rea_l_land_producer.XXXXXX")
trap 'rm -rf "$work"' EXIT
target_grid=$(tr -d '\n' < "$target_grid_file")

cat > "$work/surface.mars" <<EOF
retrieve,
  class=rd,
  stream=reanl,
  expver=r001,
  model=icon-rea-l-ch1,
  type=cf,
  date=$date,
  time=$time,
  levtype=sfc,
  param=500044/500147/502336,
  step=0
EOF
cat > "$work/soil_temperature.mars" <<EOF
retrieve,
  class=rd,
  stream=reanl,
  expver=r001,
  model=icon-rea-l-ch1,
  type=cf,
  date=$date,
  time=$time,
  levtype=dp,
  levelist=0/0.005/0.02/0.06/0.18/0.54/1.62/4.86,
  param=500166,
  step=0
EOF
cat > "$work/soil_water.mars" <<EOF
retrieve,
  class=rd,
  stream=reanl,
  expver=r001,
  model=icon-rea-l-ch1,
  type=cf,
  date=$date,
  time=$time,
  levtype=dp,
  levelist=0/0.01/0.03/0.09/0.27/0.81/2.43/7.29,
  param=500167,
  step=0
EOF
for name in surface soil_temperature soil_water; do
  "$uenv_bin" run --view=rea-l-ch1 "$fdb_image" -- \
    fdb-read --statistics "$work/$name.mars" "$work/$name.grib"
done
test "$("$uenv_bin" run --view=rea-l-ch1 "$fdb_image" -- \
  grib_count "$work/surface.grib" | tail -n 1)" = 3
test "$("$uenv_bin" run --view=rea-l-ch1 "$fdb_image" -- \
  grib_count "$work/soil_temperature.grib" | tail -n 1)" = 8
test "$("$uenv_bin" run --view=rea-l-ch1 "$fdb_image" -- \
  grib_count "$work/soil_water.grib" | tail -n 1)" = 8

cat > "$work/common.nl" <<EOF
&RunSpecification
 strict_nl_parsing = .true.
 verbosity = "moderate"
 additional_diagnostic = .true.
 diagnostic_length = 110
 soft_memory_limit = 20.0
 n_ompthread_total = 4
 n_ompthread_collect = 1
 n_ompthread_generate = 1
/
&GlobalResource
 dictionary = "$resources/dictionary_icon.txt"
 grib_definition_path = "$resources/eccodes_definitions_cosmo",
                        "$resources/eccodes_definitions_vendor"
 grib2_sample = "$sample"
 icon_grid_description = "$icon_grid"
/
&GlobalSettings
 default_model_name = "rea-l-ch1"
 default_out_type_stdlongitude = .true.
 default_out_type_justontime = "no"
 auxiliary_metainfo = "localNumberOfExperiment=639"
/
&ModelSpecification
 model_name = "rea-l-ch1"
 regrid_method = "__ALL__:icontools,nnb_strict"
 earth_axis_large = 6371229.
 earth_axis_small = 6371229.
/
EOF

run_fieldextra() {
  local name=$1
  local grib=$2
  shift 2
  cp "$work/common.nl" "$work/$name.nl"
  cat >> "$work/$name.nl" <<EOF
&Process
 in_file = "$grib"
 out_regrid_target = "$target_grid"
 out_regrid_method = "default"
 out_file = "$name.raw.nc"
 out_type = "NETCDF"
/
EOF
  local field
  for field in "$@"; do
    printf '&Process in_field = "%s" /\n' "$field" >> "$work/$name.nl"
  done
  (
    cd "$work"
    ulimit -s unlimited 2>/dev/null || true
    export OMP_STACKSIZE=${OMP_STACKSIZE:-500M}
    "$fieldextra" --syntaxcheck "$name.nl" > "$name.syntax.log" 2>&1
    "$fieldextra" "$name.nl" > "$name.run.log" 2>&1
    test -s "$name.raw.nc" || {
      echo "fieldextra did not create $name.raw.nc" >&2
      ls -la
      cat "$name.run.log"
      test ! -f fieldextra.diagnostic || cat fieldextra.diagnostic
      exit 1
    }
  )
}

run_fieldextra surface "$work/surface.grib" SKT W_SNOW RHO_SNOW
run_fieldextra soil_temperature "$work/soil_temperature.grib" T_SO
run_fieldextra soil_water "$work/soil_water.grib" W_SO

drop_singleton_dim() {
  local file=$1
  local dim=$2
  if ncks -m "$file" | awk -v dim="$dim" '$1 == dim && $2 == "=" { found=1 } END { exit !found }'; then
    ncwa -O -a "$dim" "$file" "$file"
  fi
}
for file in "$work/surface.raw.nc" "$work/soil_temperature.raw.nc" \
  "$work/soil_water.raw.nc"; do
  drop_singleton_dim "$file" time
  drop_singleton_dim "$file" epsd_1
done

ncrename -O -d z_1,t_so_depth -v z_1,t_so_depth \
  "$work/soil_temperature.raw.nc"
ncatted -O -a bounds,t_so_depth,d,, "$work/soil_temperature.raw.nc"
ncrename -O -d z_1,w_so_layer -v z_1,w_so_layer \
  "$work/soil_water.raw.nc"
ncatted -O -a bounds,w_so_layer,d,, "$work/soil_water.raw.nc"
ncatted -O -a cell_methods,W_SO,o,c,"w_so_layer: interval" \
  "$work/soil_water.raw.nc"

cp "$work/surface.raw.nc" "$work/packaged.nc"
ncks -A -v T_SO,t_so_depth \
  "$work/soil_temperature.raw.nc" "$work/packaged.nc"
ncks -A -v W_SO,w_so_layer \
  "$work/soil_water.raw.nc" "$work/packaged.nc"
for variable in SKT W_SNOW RHO_SNOW T_SO W_SO lat_1 lon_1; do
  ncks -m -v "$variable" "$work/packaged.nc" >/dev/null
done

partial="$output.partial.$SLURM_JOB_ID"
ncks -4 -L 1 -O "$work/packaged.nc" "$partial"
mv "$partial" "$output"
sha256sum "$output" > "$output.sha256"

python3 - "$manifest" "$output" "$date" "$time" "$target_grid" "$fdb_image" <<'PY'
import hashlib
import json
import os
import pathlib
import sys
import tempfile

manifest, output, date, time, target_grid, image = sys.argv[1:]
output_path = pathlib.Path(output)
digest = hashlib.sha256(output_path.read_bytes()).hexdigest()
payload = {
    "schema_version": 1,
    "status": "PASS",
    "source": "ICON REA-L-CH1 FDB",
    "request": {
        "class": "rd",
        "stream": "reanl",
        "expver": "r001",
        "model": "icon-rea-l-ch1",
        "type": "cf",
        "date": date,
        "time": time,
        "step": 0,
        "fields": ["SKT", "T_SO", "W_SO", "W_SNOW", "RHO_SNOW"],
    },
    "fdb_image": image,
    "fieldextra_target_grid": target_grid,
    "output": str(output_path.resolve()),
    "output_size_bytes": output_path.stat().st_size,
    "output_sha256": digest,
}
path = pathlib.Path(manifest)
with tempfile.NamedTemporaryFile(
    mode="w", dir=path.parent, prefix=f".{path.name}.", delete=False
) as stream:
    json.dump(payload, stream, indent=2, sort_keys=True)
    stream.write("\n")
    temporary = stream.name
os.replace(temporary, path)
PY
touch "$manifest.ready"
touch "$output.ready"
echo "PASS: published $output"
