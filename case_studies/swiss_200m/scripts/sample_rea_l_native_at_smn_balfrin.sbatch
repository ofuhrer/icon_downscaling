#!/bin/bash -l
# Sample one 24-hour REA-L window directly on its native grid at SMN sites.
#SBATCH --job-name=rea-l-native-smn
#SBATCH --partition=pp-long
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=24G
#SBATCH --time=00:30:00

set -euo pipefail

repo_root=${REPO_ROOT:?}
. "$repo_root/scripts/load_balfrin_site_config.sh"
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

start=${START_TIME:?Use YYYY-MM-DDT00:00:00}
end=${END_TIME:?Use the following YYYY-MM-DDT00:00:00}
observations=${OBSERVATION_INPUT:?}
output=${REA_L_STATION_OUTPUT:?}
icon_extpar=${ICON_EXTPAR:-/oprusers/osm/opr.inn/data/ICON_INPUT/ICON-CH1-EPS/external_parameter_icon_grid_0001_R19B08_mch_tuned.nc}
image=${REA_FDB_IMAGE_OVERRIDE:-${REA_FDB_IMAGE:?}}
sampler=$repo_root/case_studies/swiss_200m/validation/sample_rea_l_native_at_smn.py
python=${HICAR_PYTHON:-python3}

test ! -e "$output" || exit 0
test -s "$observations"
test -s "$icon_extpar"
mkdir -p "$(dirname "$output")" "$SCRATCH/icon_hicar/tmp"
work=$(mktemp -d "$SCRATCH/icon_hicar/tmp/rea_l_native_smn.XXXXXX")
trap 'rm -rf "$work"' EXIT

IFS=$'\t' read -r start_date end_date duration_hours < <(
  "$python" - "$start" "$end" <<'PY'
from datetime import datetime
import sys
start, end = (datetime.fromisoformat(value) for value in sys.argv[1:])
print(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), int((end-start).total_seconds()/3600), sep="\t")
PY
)
test "$duration_hours" = 24
test "${start:11}" = 00:00:00
test "${end:11}" = 00:00:00

cat >"$work/current_start.mars" <<EOF
retrieve,class=rd,stream=reanl,expver=r001,model=icon-rea-l-ch1,type=cf,
date=$start_date,time=0000,levtype=sfc,
param=500000/500011/500017/500027/500029/500044/500045,step=0/to/23/by/1
EOF
cat >"$work/current_end.mars" <<EOF
retrieve,class=rd,stream=reanl,expver=r001,model=icon-rea-l-ch1,type=cf,
date=$end_date,time=0000,levtype=sfc,
param=500000/500011/500017/500027/500029/500044/500045,step=0
EOF
cat >"$work/precipitation.mars" <<EOF
retrieve,class=rd,stream=reanl,expver=r001,model=icon-rea-l-ch1,type=cf,
date=$start_date,time=0000,levtype=sfc,param=500041,step=0/to/24/by/1
EOF
for name in current_start current_end precipitation; do
  uenv run --view=rea-l-ch1 "$image" -- \
    fdb-read --statistics "$work/$name.mars" "$work/$name.grib"
done

uenv run --view=rea-l-ch1 "$image" -- python "$sampler" \
  --current-grib "$work/current_start.grib" \
  --current-grib "$work/current_end.grib" \
  --precipitation-grib "$work/precipitation.grib" \
  --icon-extpar "$icon_extpar" --observations "$observations" \
  --start "$start" --end "$end" --output "$output"
