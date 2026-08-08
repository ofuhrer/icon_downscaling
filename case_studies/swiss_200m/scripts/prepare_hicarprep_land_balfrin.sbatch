#!/bin/bash -l
# Build the valid-time HICAR runtime domain from native REA-L land fields.
#SBATCH --job-name=hicarprep-land
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=48G
#SBATCH --time=01:00:00

set -euo pipefail

repo_root=${REPO_ROOT:?}
. "$repo_root/scripts/load_balfrin_site_config.sh"
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

valid=${VALID_TIME:?Use YYYY-MM-DDT00:00:00}
static=${HICAR_STATIC_TEMPLATE:?}
output=${HICAR_RUNTIME_DOMAIN_OUTPUT:?}
weights=${HICARPREP_RBF_WEIGHTS:?}
noahmp=${NOAHMP_TABLE:?}
external=${HICAR_EXTERNAL_PARAMETERS:-}
python=${HICAR_PYTHON:-python3}
icon_extpar=${ICON_EXTPAR:-/oprusers/osm/opr.inn/data/ICON_INPUT/ICON-CH1-EPS/external_parameter_icon_grid_0001_R19B08_mch_tuned.nc}
image=${REA_FDB_IMAGE_OVERRIDE:-${REA_FDB_IMAGE:?}}
date=${valid:0:10}; date=${date//-/}

if test -f "$output.ready"; then exit 0; fi
test ! -e "$output" || { echo "incomplete runtime domain exists: $output" >&2; exit 2; }
mkdir -p "$(dirname "$output")" "$SCRATCH/icon_hicar/tmp"
work=$(mktemp -d "$SCRATCH/icon_hicar/tmp/hicarprep_land.XXXXXX")
trap 'rm -rf "$work"' EXIT

cat > "$work/surface.mars" <<EOF
retrieve,class=rd,stream=reanl,expver=r001,model=icon-rea-l-ch1,type=cf,
date=$date,time=0000,levtype=sfc,param=500044/500147/502336,step=0
EOF
cat > "$work/t_so.mars" <<EOF
retrieve,class=rd,stream=reanl,expver=r001,model=icon-rea-l-ch1,type=cf,
date=$date,time=0000,levtype=dp,levelist=0/0.005/0.02/0.06/0.18/0.54/1.62/4.86,param=500166,step=0
EOF
cat > "$work/w_so.mars" <<EOF
retrieve,class=rd,stream=reanl,expver=r001,model=icon-rea-l-ch1,type=cf,
date=$date,time=0000,levtype=dp,levelist=0/0.01/0.03/0.09/0.27/0.81/2.43/7.29,param=500167,step=0
EOF
for name in surface t_so w_so; do
  uenv run --view=rea-l-ch1 "$image" -- fdb-read --statistics "$work/$name.mars" "$work/$name.grib"
done

native="$work/native_surface.nc"
surface="$work/hicar_surface.nc"
# MIR is provided by the REA-L uenv Python, not by the project NetCDF venv.
uenv run --view=rea-l-ch1 "$image" -- python \
  "$repo_root/case_studies/swiss_200m/validation/package_rea_l_native_surface.py" \
  --icon-extpar "$icon_extpar" --surface-grib "$work/surface.grib" \
  --soil-temperature-grib "$work/t_so.grib" --soil-water-grib "$work/w_so.grib" \
  --valid-time "${valid}Z" --output "$native"
if test ! -s "$weights"; then
  mkdir -p "$(dirname "$weights")"
  uenv run --view=rea-l-ch1 "$image" -- "$python" "$repo_root/scripts/hicarprep.py" \
    build-weights --icon-grid "$native" --static "$static" --output "$weights"
fi
external_args=()
epoch_args=(--allow-static-epoch-back-extrapolation)
if test -n "$external"; then
  test -s "$external" || { echo "missing external-parameter product: $external" >&2; exit 2; }
  external_args=(--external "$external")
  epoch_args=(--allow-external-epoch-back-extrapolation)
fi
uenv run --view=rea-l-ch1 "$image" -- "$python" "$repo_root/scripts/hicarprep.py" \
  prepare-surface --icon-surface "$native" --static "$static" --weights "$weights" \
  --noahmp-table "$noahmp" --soil-water-method smi "${external_args[@]}" \
  "${epoch_args[@]}" --output "$surface"
uenv run --view=rea-l-ch1 "$image" -- "$python" "$repo_root/scripts/hicarprep.py" \
  assemble-runtime-domain --static "$static" --surface "$surface" \
  "${external_args[@]}" --valid-time "${valid}Z" --output "$output"
touch "$output.ready"
