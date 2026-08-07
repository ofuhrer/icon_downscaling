#!/bin/bash -l
# Generate and validate alternative HICAR surface initial states for several REA-L dates.
#SBATCH --job-name=hicarprep-surface
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

target_static=${TARGET_STATIC:-$SCRATCH/icon_hicar/case_studies/alpine_bridge_200m/static/domain_static_alpine_bridge_200m.nc}
icon_extpar=${ICON_EXTPAR:-/oprusers/osm/opr.inn/data/ICON_INPUT/ICON-CH1-EPS/external_parameter_icon_grid_0001_R19B08_mch_tuned.nc}
noahmp_table=${NOAHMP_TABLE:-$SCRATCH/icon_hicar/qualification/fixed_parameters_soil_v1_b514/source/run/NoahmpTable.TBL}
fdb_image=${REA_FDB_IMAGE_OVERRIDE:-fdb/5.21:v1}
output_root=${OUTPUT_ROOT:-$SCRATCH/icon_hicar/qualification/hicarprep_surface_cases_v11}
packager=$repo_root/case_studies/swiss_200m/validation/package_rea_l_native_surface.py
hicarprep=$repo_root/scripts/hicarprep.py
case_list=${REA_L_SURFACE_CASES:-"20200115 20200210 20200702 20201115"}

for path in "$target_static" "$icon_extpar" "$noahmp_table" "$packager" "$hicarprep"; do
  test -s "$path" || { echo "missing or empty input: $path" >&2; exit 2; }
done
mkdir -p "$output_root"
work=$(mktemp -d "$SCRATCH/icon_hicar/tmp/hicarprep_surface_cases.XXXXXX")
trap 'rm -rf "$work"' EXIT

weights=$output_root/icon_rea_l_to_alpine_bridge_200m_rbf.nc
for date in $case_list; do
  case "$date" in
    *[!0-9]*|?????????*) echo "case date must be YYYYMMDD: $date" >&2; exit 2 ;;
  esac
  case_root=$output_root/$date
  mkdir -p "$case_root"
  source_nc=$case_root/icon_rea_l_native_surface_${date}_0000.nc
  test ! -e "$source_nc" || { echo "refusing to overwrite $source_nc" >&2; exit 2; }

  cat > "$work/surface_${date}.mars" <<EOF
retrieve,
  class=rd,
  stream=reanl,
  expver=r001,
  model=icon-rea-l-ch1,
  type=cf,
  date=$date,
  time=0000,
  levtype=sfc,
  param=500044/500147/502336,
  step=0
EOF
  cat > "$work/t_so_${date}.mars" <<EOF
retrieve,
  class=rd,
  stream=reanl,
  expver=r001,
  model=icon-rea-l-ch1,
  type=cf,
  date=$date,
  time=0000,
  levtype=dp,
  levelist=0/0.005/0.02/0.06/0.18/0.54/1.62/4.86,
  param=500166,
  step=0
EOF
  cat > "$work/w_so_${date}.mars" <<EOF
retrieve,
  class=rd,
  stream=reanl,
  expver=r001,
  model=icon-rea-l-ch1,
  type=cf,
  date=$date,
  time=0000,
  levtype=dp,
  levelist=0/0.01/0.03/0.09/0.27/0.81/2.43/7.29,
  param=500167,
  step=0
EOF
  for name in surface t_so w_so; do
    uenv run --view=rea-l-ch1 "$fdb_image" -- \
      fdb-read --statistics "$work/${name}_${date}.mars" "$work/${name}_${date}.grib"
  done

  uenv run --view=rea-l-ch1 "$fdb_image" -- python "$packager" \
    --icon-extpar "$icon_extpar" \
    --surface-grib "$work/surface_${date}.grib" \
    --soil-temperature-grib "$work/t_so_${date}.grib" \
    --soil-water-grib "$work/w_so_${date}.grib" \
    --valid-time "${date:0:4}-${date:4:2}-${date:6:2}T00:00:00Z" \
    --output "$source_nc"

  if [[ ! -s "$weights" ]]; then
    uenv run --view=rea-l-ch1 "$fdb_image" -- python "$hicarprep" build-weights \
      --icon-grid "$source_nc" --static "$target_static" --output "$weights"
  fi

  product_arguments=()
  for method in smi relative_saturation absolute_w_so; do
    product=$case_root/hicar_surface_${method}_${date}_0000.nc
    uenv run --view=rea-l-ch1 "$fdb_image" -- python "$hicarprep" prepare-surface \
      --icon-surface "$source_nc" --static "$target_static" --weights "$weights" \
      --noahmp-table "$noahmp_table" --soil-water-method "$method" \
      --allow-static-epoch-back-extrapolation --output "$product"
    product_arguments+=(--product "$method=$product")
    runtime_domain=$case_root/hicar_runtime_domain_${method}_${date}_0000.nc
    uenv run --view=rea-l-ch1 "$fdb_image" -- python "$hicarprep" assemble-runtime-domain \
      --static "$target_static" --surface "$product" --output "$runtime_domain"
  done
  uenv run --view=rea-l-ch1 "$fdb_image" -- python "$hicarprep" validate-surface-case \
    --icon-surface "$source_nc" --static "$target_static" --noahmp-table "$noahmp_table" \
    "${product_arguments[@]}" --report "$case_root/validation.json" \
    > "$case_root/validation.stdout.json"
  touch "$case_root/validation.json.ready"
done

echo "PASS: generated and validated cases under $output_root"
