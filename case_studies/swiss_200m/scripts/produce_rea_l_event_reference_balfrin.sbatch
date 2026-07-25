#!/bin/bash -l
# Produce one three-hourly, source-grid REA-L surface reference record.
# Submit as an array over 0..((record_count-1)/3).
#SBATCH --job-name=rea-l-event-reference
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=12G
#SBATCH --time=00:30:00

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module load nco/5.0.1-gcc netcdf-c/4.8.1-gcc >/dev/null

plan=${STREAM_PLAN:?Set STREAM_PLAN to a published chunk_plan.json}
reference_index=${REFERENCE_INDEX:-${SLURM_ARRAY_TASK_ID:-}}
[[ "$reference_index" =~ ^[0-9]+$ ]] || {
  echo "reference array index is required" >&2
  exit 2
}
repo_root=${REPO_ROOT:-$SCRATCH/icon_hicar}
python=${HICAR_VALIDATION_PYTHON:-$repo_root/venv_static/bin/python}
case_root=$repo_root/case_studies/swiss_200m
packager=$case_root/validation/package_rea_l_surface_reference.py
target_grid_file=$case_root/config/fieldextra_target_grid.txt
uenv_bin=${UENV_BIN:-/usr/bin/uenv}
image=${REA_FDB_IMAGE:-fdb/5.19:v2}
fieldextra=${FIELD_EXTRA_BIN:-/oprusers/osm/opr.inn/abs/fieldextra_gnu_opt_omp-16.0.0-gcc-12.3.0}
resources=${FIELD_EXTRA_RESOURCES:-/oprusers/osm/opr.inn/config/resources}
sample=${FIELD_EXTRA_SAMPLE:-/oprusers/osm/opr.inn/modules/eccodes_cosmo_resources/2.36.0.3/samples/COSMO_GRIB2_default.tmpl}
icon_grid=${ICON_GRID:-/oprusers/osm/opr.inn/data/grid_descriptions/icon_grid_0001_R19B08_mch.nc}

for path in "$plan" "$plan.ready" "$python" "$packager" "$target_grid_file" \
  "$uenv_bin" "$fieldextra" "$resources/dictionary_icon.txt" "$sample" "$icon_grid"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done

IFS=$'\t' read -r chunk_root record_index cycle_date cycle_time step valid_time \
  interval_start initial final manifest precip_start_date precip_start_step \
  precip_end_date precip_end_step < <(
  "$python" - "$plan" "$reference_index" <<'PY'
import json
import pathlib
import sys
from datetime import datetime, timedelta

plan = json.load(open(sys.argv[1]))
reference_index = int(sys.argv[2])
record_index = reference_index * 3
records = plan["records"]
if record_index >= len(records):
    raise SystemExit(
        f"reference index {reference_index} maps outside 0..{len(records)-1}"
    )
record = records[record_index]
valid = datetime.fromisoformat(record["valid_time"])
if valid.minute or valid.second or valid.hour % 3:
    raise SystemExit(f"reference time is not on a three-hour boundary: {valid}")
interval_start = valid if record_index == 0 else valid - timedelta(hours=3)
reference_dir = pathlib.Path(plan["chunk_root"]) / "reference"
stamp = valid.strftime("%Y%m%d_%H%M")
final = reference_dir / f"rea_l_surface_reference_{stamp}.nc"
manifest = reference_dir / f"rea_l_surface_reference_{stamp}.manifest.json"
if record_index == 0:
    initial = "1"
    precip_start_date = precip_end_date = "-"
    precip_start_step = precip_end_step = "-"
elif int(record["step_hours"]) >= 3:
    initial = "0"
    precip_start_date = precip_end_date = record["cycle_date"]
    precip_start_step = str(int(record["step_hours"]) - 3)
    precip_end_step = str(record["step_hours"])
else:
    initial = "0"
    previous_cycle = valid - timedelta(days=1)
    precip_start_date = precip_end_date = previous_cycle.strftime("%Y%m%d")
    precip_start_step = "21"
    precip_end_step = "24"
print(
    plan["chunk_root"],
    record_index,
    record["cycle_date"],
    record["cycle_time"],
    record["step_hours"],
    record["valid_time"],
    interval_start.isoformat(),
    initial,
    final.resolve(),
    manifest.resolve(),
    precip_start_date,
    precip_start_step,
    precip_end_date,
    precip_end_step,
    sep="\t",
)
PY
)

mkdir -p "$(dirname "$final")" "$chunk_root/reference/cache" "$chunk_root/work"
if test -s "$final" && test -f "$final.ready" && test -s "$manifest" \
  && test -f "$manifest.ready"; then
  "$python" - "$final" "$manifest" "$valid_time" <<'PY'
import hashlib
import json
import sys

data, manifest_path, valid_time = sys.argv[1:]
manifest = json.load(open(manifest_path))
digest = hashlib.sha256(open(data, "rb").read()).hexdigest()
if manifest.get("status") != "PASS":
    raise SystemExit("existing reference manifest is not PASS")
if manifest.get("valid_time", "").replace("+00:00", "") != valid_time:
    raise SystemExit("existing reference has the wrong valid time")
if manifest.get("output_sha256") != digest:
    raise SystemExit("existing reference hash mismatch")
PY
  echo "reference already published and verified: $final"
  exit 0
fi
for path in "$final" "$final.ready" "$manifest" "$manifest.ready"; do
  test ! -e "$path" || {
    echo "incomplete reference publication requires operator review: $path" >&2
    exit 2
  }
done

work=$(mktemp -d "$chunk_root/work/reference_${record_index}.XXXXXX")
trap 'rm -rf "$work"' EXIT
target_grid=$(tr -d '\n' < "$target_grid_file")

cat > "$work/common.nl" <<EOF
&RunSpecification
 strict_nl_parsing = .true.
 verbosity = "moderate"
 additional_diagnostic = .true.
 diagnostic_length = 110
 soft_memory_limit = 10.0
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
      cat "$name.run.log"
      test ! -f fieldextra.diagnostic || cat fieldextra.diagnostic
      exit 1
    }
  )
}

drop_singleton_dimensions() {
  local file=$1
  local dim
  for dim in time epsd_1; do
    if ncks -m "$file" | awk -v dim="$dim" \
      '$1 == dim && $2 == "=" { found=1 } END { exit !found }'; then
      ncwa -O -a "$dim" "$file" "$file"
    fi
  done
}

cat > "$work/current.mars" <<EOF
retrieve,
  class=rd,
  stream=reanl,
  expver=r001,
  model=icon-rea-l-ch1,
  type=cf,
  date=$cycle_date,
  time=$cycle_time,
  levtype=sfc,
  param=500000/500011/500017/500027/500029/500044/500045,
  step=$step
EOF
"$uenv_bin" run --view=rea-l-ch1 "$image" -- \
  fdb-read --statistics "$work/current.mars" "$work/current.grib"
test "$("$uenv_bin" run --view=rea-l-ch1 "$image" -- \
  grib_count "$work/current.grib" | tail -n 1)" = 7
run_fieldextra current "$work/current.grib" \
  PS T_2M TD_2M U_10M V_10M H_SNOW W_SNOW
drop_singleton_dimensions "$work/current.raw.nc"

geometry="$chunk_root/reference/cache/geometry.nc"
geometry_ready="$geometry.ready"
geometry_lock="$chunk_root/reference/cache/.geometry.lock"
produce_geometry() {
  cat > "$work/geometry.mars" <<EOF
retrieve,
  class=rd,
  stream=reanl,
  expver=r001,
  model=icon-rea-l-ch1,
  type=cf,
  date=$cycle_date,
  time=$cycle_time,
  levtype=sfc,
  param=500007,
  step=0
EOF
  "$uenv_bin" run --view=rea-l-ch1 "$image" -- \
    fdb-read --statistics "$work/geometry.mars" "$work/geometry.grib"
  run_fieldextra geometry "$work/geometry.grib" HSURF
  drop_singleton_dimensions "$work/geometry.raw.nc"
  mv "$work/geometry.raw.nc" "$geometry.partial.${SLURM_JOB_ID}"
  mv "$geometry.partial.${SLURM_JOB_ID}" "$geometry"
  touch "$geometry_ready"
}
if ! test -s "$geometry" || ! test -f "$geometry_ready"; then
  if mkdir "$geometry_lock" 2>/dev/null; then
    trap 'rmdir "$geometry_lock" 2>/dev/null || true; rm -rf "$work"' EXIT
    produce_geometry
    rmdir "$geometry_lock"
    trap 'rm -rf "$work"' EXIT
  else
    for _ in $(seq 1 300); do
      test -s "$geometry" && test -f "$geometry_ready" && break
      sleep 2
    done
  fi
fi
test -s "$geometry" && test -f "$geometry_ready" || {
  echo "geometry cache publication timed out" >&2
  exit 1
}

packager_args=(
  --current "$work/current.raw.nc"
  --geometry "$geometry"
  --valid-time "$valid_time"
  --interval-start "$interval_start"
  --output "$final"
  --manifest "$manifest"
  --source-cycle "${cycle_date}T${cycle_time}"
  --source-step "$step"
)
if test "$initial" = 1; then
  packager_args+=(--initial-record)
else
  for endpoint in start end; do
    if test "$endpoint" = start; then
      endpoint_date=$precip_start_date
      endpoint_step=$precip_start_step
    else
      endpoint_date=$precip_end_date
      endpoint_step=$precip_end_step
    fi
    cat > "$work/precip_${endpoint}.mars" <<EOF
retrieve,
  class=rd,
  stream=reanl,
  expver=r001,
  model=icon-rea-l-ch1,
  type=cf,
  date=$endpoint_date,
  time=0000,
  levtype=sfc,
  param=500041,
  step=$endpoint_step
EOF
    "$uenv_bin" run --view=rea-l-ch1 "$image" -- \
      fdb-read --statistics "$work/precip_${endpoint}.mars" \
      "$work/precip_${endpoint}.grib"
    run_fieldextra "precip_${endpoint}" "$work/precip_${endpoint}.grib" TOT_PREC
    drop_singleton_dimensions "$work/precip_${endpoint}.raw.nc"
  done
  packager_args+=(
    --precip-start "$work/precip_start.raw.nc"
    --precip-end "$work/precip_end.raw.nc"
  )
fi
"$python" "$packager" "${packager_args[@]}"
echo "PASS: published event reference record $record_index at $valid_time"
