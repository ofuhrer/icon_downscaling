#!/bin/bash -l
# Identify REA-L soil and snow fields needed for independent production chains.
#SBATCH --job-name=rea-l-land-state
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=00:30:00

set -euo pipefail
repo_root=${REPO_ROOT:-$SCRATCH/icon_hicar}
# shellcheck source=scripts/load_balfrin_site_config.sh
. "$repo_root/scripts/load_balfrin_site_config.sh"
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"

date=${REA_L_AUDIT_DATE:-20200101}
report=${REA_L_LAND_REPORT:-$repo_root/case_studies/swiss_200m/validation/rea_l_land_state_inventory.json}
uenv_bin=${UENV_BIN:-/usr/bin/uenv}
image=${REA_FDB_IMAGE:?REA_FDB_IMAGE is required}
work=$(mktemp -d "$repo_root/tmp/rea_l_land_state.XXXXXX")
trap 'rm -rf "$work"' EXIT

cat > "$work/surface_state.mars" <<EOF
retrieve,
  class=rd,
  stream=reanl,
  expver=r001,
  model=icon-rea-l-ch1,
  type=cf,
  date=$date,
  time=0000,
  levtype=sfc,
  param=500038/500044/500053/500055/500078/500080/500086/500087/500088/500089/500134/500146/500147/500169/500170/500304/502336/502946/503135/503307/503422/503750,
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
  time=0000,
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
  time=0000,
  levtype=dp,
  levelist=0/0.01/0.03/0.09/0.27/0.81/2.43/7.29,
  param=500167,
  step=0
EOF
for name in surface_state soil_temperature soil_water; do
  "$uenv_bin" run --view=rea-l-ch1 "$image" -- \
    fdb-read --statistics "$work/$name.mars" "$work/$name.grib"
done
cat "$work/surface_state.grib" "$work/soil_temperature.grib" \
  "$work/soil_water.grib" > "$work/land.grib"
test "$("$uenv_bin" run --view=rea-l-ch1 "$image" -- grib_count "$work/land.grib" | tail -n 1)" = 38
"$uenv_bin" run --view=rea-l-ch1 "$image" -- \
  grib_ls -j -p paramId,shortName,name,units,typeOfLevel,level,step,typeOfFirstFixedSurface,scaleFactorOfFirstFixedSurface,scaledValueOfFirstFixedSurface,typeOfSecondFixedSurface,scaleFactorOfSecondFixedSurface,scaledValueOfSecondFixedSurface "$work/land.grib" \
  > "$work/messages.json"

python3 - "$work/messages.json" "$report" "$date" "$image" <<'PY'
import json
import os
import pathlib
import sys
import tempfile

source, report, date, image = sys.argv[1:]
messages = json.load(open(source))
inventory = messages.get("messages", [])
print(
    json.dumps(
        sorted(
            {
                (message["paramId"], message["shortName"], message["name"])
                for message in inventory
            }
        ),
        indent=2,
    )
)
found = [
    message
    for message in inventory
    if message["shortName"] in {"W_SNOW", "RHO_SNOW", "SKT", "T_SO", "W_SO"}
]
if len(found) != 19:
    raise SystemExit("expected 19 REA-L land-state messages")
names = {message["shortName"] for message in found}
if names != {"W_SNOW", "RHO_SNOW", "SKT", "T_SO", "W_SO"}:
    raise SystemExit(f"unexpected REA-L land-state fields: {sorted(names)}")
payload = {
    "schema_version": 2,
    "status": "PASS",
    "source": "ICON REA-L-CH1 FDB",
    "cycle_date": date,
    "cycle_time": "0000",
    "step": 0,
    "fdb_image": image,
    "land_state_messages": found,
    "soil_layer_descriptors": [
        message for message in found if message["shortName"] in {"T_SO", "W_SO"}
    ],
    "surface_inventory": inventory[:22],
    "required_transformations": {
        "surface_temperature": "regrid SKT [K]",
        "soil_temperature": "depth-interpolate T_SO [K] to the four HICAR/NoahMP soil layers",
        "soil_vwc": "convert layer-integrated W_SO [kg m-2] to volumetric water content and depth-remap",
        "snow_water_equivalent": "regrid W_SNOW [kg m-2 = mm water equivalent]",
        "snow_height": "derive W_SNOW / RHO_SNOW [m], with zero and missing-density handling",
    },
}
path = pathlib.Path(report)
path.parent.mkdir(parents=True, exist_ok=True)
with tempfile.NamedTemporaryFile(
    mode="w", dir=path.parent, prefix=f".{path.name}.", delete=False
) as stream:
    json.dump(payload, stream, indent=2, sort_keys=True)
    stream.write("\n")
    temporary = stream.name
os.replace(temporary, path)
pathlib.Path(f"{path}.ready").touch()
print(json.dumps(payload, indent=2, sort_keys=True))
PY
