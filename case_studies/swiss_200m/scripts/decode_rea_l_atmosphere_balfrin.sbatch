#!/bin/bash -l
# Retrieve one native ICON REA-L-CH1 atmospheric state and publish the strict
# hicarprep adapter product. Native GRIB is kept only in node-local scratch.
#
# Required submission variables:
#   REPO_ROOT                     immutable coordinator checkout
#   ICON_ATMOSPHERE_OUTPUT        new adapter NetCDF path
#   ICON_ATMOSPHERE_CYCLE_DATE    YYYYMMDD
#   ICON_ATMOSPHERE_CYCLE_TIME    HHMM
#   ICON_ATMOSPHERE_STEP_HOURS    non-negative integer
#   ICON_ATMOSPHERE_VALID_TIME    YYYY-MM-DDTHH:MM:SSZ
#
# Optional:
#   ICON_EXTPAR                   native-grid EXTPAR file
#   ICON_MISSING_QI_POLICY        error or source-absent-zero
#   REA_FDB_IMAGE_OVERRIDE        FDB uenv image
#SBATCH --job-name=rea-l-atmosphere
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=01:00:00

set -euo pipefail

repo_root=${REPO_ROOT:?Set REPO_ROOT to the immutable coordinator checkout}
. "$repo_root/scripts/load_balfrin_site_config.sh"
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

partition=${SLURM_JOB_PARTITION:?This decoder must run under Slurm}
case "$partition" in pp-short|pp-long) ;; *) echo "unsupported partition: $partition" >&2; exit 2 ;; esac
allow=$(scontrol show partition "$partition" -o | sed -n 's/.*AllowGroups=\([^ ]*\).*/\1/p')
case ",$allow," in *,ALL,*|*,s83,*) ;; *) echo "partition access rejected: $allow" >&2; exit 2 ;; esac

output=${ICON_ATMOSPHERE_OUTPUT:?Set ICON_ATMOSPHERE_OUTPUT}
cycle_date=${ICON_ATMOSPHERE_CYCLE_DATE:?Set ICON_ATMOSPHERE_CYCLE_DATE}
cycle_time=${ICON_ATMOSPHERE_CYCLE_TIME:?Set ICON_ATMOSPHERE_CYCLE_TIME}
step=${ICON_ATMOSPHERE_STEP_HOURS:?Set ICON_ATMOSPHERE_STEP_HOURS}
valid_time=${ICON_ATMOSPHERE_VALID_TIME:?Set ICON_ATMOSPHERE_VALID_TIME}
icon_extpar=${ICON_EXTPAR:-/oprusers/osm/opr.inn/data/ICON_INPUT/ICON-CH1-EPS/external_parameter_icon_grid_0001_R19B08_mch_tuned.nc}
missing_qi_policy=${ICON_MISSING_QI_POLICY:-source-absent-zero}
image=${REA_FDB_IMAGE_OVERRIDE:-${REA_FDB_IMAGE:?REA_FDB_IMAGE is required}}
uenv_bin=${UENV_BIN:-/usr/bin/uenv}
hicarprep=$repo_root/scripts/hicarprep.py

[[ "$cycle_date" =~ ^[0-9]{8}$ ]] || { echo "cycle date must be YYYYMMDD" >&2; exit 2; }
[[ "$cycle_time" =~ ^[0-9]{4}$ ]] || { echo "cycle time must be HHMM" >&2; exit 2; }
[[ "$step" =~ ^[0-9]+$ ]] || { echo "step must be a non-negative integer" >&2; exit 2; }
case "$missing_qi_policy" in error|source-absent-zero) ;; *) echo "unsupported QI policy" >&2; exit 2 ;; esac
for path in "$repo_root" "$icon_extpar" "$uenv_bin" "$hicarprep"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done
test ! -e "$output" || { echo "refusing to overwrite $output" >&2; exit 2; }
test ! -e "$output.ready" || { echo "refusing to overwrite $output.ready" >&2; exit 2; }
mkdir -p "$(dirname "$output")" "$SCRATCH/icon_hicar/tmp"
work=$(mktemp -d "$SCRATCH/icon_hicar/tmp/rea_l_atmosphere.XXXXXX")
trap 'rm -rf "$work"' EXIT

cat > "$work/full.mars" <<EOF
retrieve,
  class=rd,
  stream=reanl,
  expver=r001,
  model=icon-rea-l-ch1,
  type=cf,
  date=$cycle_date,
  time=$cycle_time,
  levtype=ml,
  levelist=1/to/80,
  param=500001/500014/500028/500030/500035/500100,
  step=$step
EOF
cat > "$work/w.mars" <<EOF
retrieve,
  class=rd,
  stream=reanl,
  expver=r001,
  model=icon-rea-l-ch1,
  type=cf,
  date=$cycle_date,
  time=$cycle_time,
  levtype=ml,
  levelist=1/to/81,
  param=500032,
  step=$step
EOF
cat > "$work/hhl.mars" <<EOF
retrieve,
  class=rd,
  stream=reanl,
  expver=r001,
  model=icon-rea-l-ch1,
  type=cf,
  date=$cycle_date,
  time=$cycle_time,
  levtype=ml,
  levelist=1/to/81,
  param=500008,
  step=0
EOF
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
  param=500007/500054,
  step=0
EOF

for part in full w hhl surface; do
  "$uenv_bin" run --view=rea-l-ch1 "$image" -- \
    fdb-read --statistics "$work/$part.mars" "$work/$part.grib"
done
cat "$work/full.grib" "$work/w.grib" > "$work/dynamic.grib"
cat "$work/hhl.grib" "$work/surface.grib" > "$work/geometry.grib"
dynamic_count=$("$uenv_bin" run --view=rea-l-ch1 "$image" -- grib_count "$work/dynamic.grib" | tail -n 1)
geometry_count=$("$uenv_bin" run --view=rea-l-ch1 "$image" -- grib_count "$work/geometry.grib" | tail -n 1)
test "$dynamic_count" = 561
test "$geometry_count" = 83

"$uenv_bin" run --view=rea-l-ch1 "$image" -- python "$hicarprep" \
  decode-icon-atmosphere \
  --dynamic-grib "$work/dynamic.grib" \
  --geometry-grib "$work/geometry.grib" \
  --icon-extpar "$icon_extpar" \
  --valid-time "$valid_time" \
  --missing-qi-policy "$missing_qi_policy" \
  --output "$output"
test -s "$output"
touch "$output.ready"

printf 'PASS output=%s valid_time=%s dynamic_messages=%s geometry_messages=%s qi_policy=%s\n' \
  "$output" "$valid_time" "$dynamic_count" "$geometry_count" "$missing_qi_policy"
