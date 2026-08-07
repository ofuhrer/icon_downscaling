#!/usr/bin/env bash
set -eo pipefail

# Smoke-test REA-L-CH1 FDB access on Balfrin.
# Run from a local machine with passwordless SSH to Balfrin:
#   scripts/balfrin_rea_l_ch1_fdb_smoke.sh
#   scripts/balfrin_rea_l_ch1_fdb_smoke.sh fdb/operator:v1

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
# shellcheck source=load_balfrin_site_config.sh
. "$repo_root/scripts/load_balfrin_site_config.sh"
image="${1:-$REA_FDB_IMAGE}"

ssh -o BatchMode=yes -o ConnectTimeout=10 balfrin 'bash -s' -- "$image" <<'REMOTE'
set -eo pipefail

[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"

workdir="$SCRATCH/icon_hicar/fdb_smoke"
mkdir -p "$workdir"
cd "$workdir"

echo "== environment =="
printf 'host=%s\n' "$(hostname)"
printf 'user=%s\n' "$(whoami)"
printf 'scratch=%s\n' "$SCRATCH"
printf 'workdir=%s\n' "$PWD"
printf 'USER_ENV_ROOT=%s\n' "$USER_ENV_ROOT"
id

image="$1"
echo
echo "== uenv image =="
uenv image pull "$image" || true
uenv image find "${image%:*}" | sed -n '1,80p'

echo
echo "== fdb-info rea-l-ch1 =="
uenv run --view=rea-l-ch1 "$image" -- fdb-info --all | sed -n '1,100p'

echo
echo "== shortname_to_paramid =="
uenv run --view=rea-l-ch1 "$image" -- python - <<'PY'
import uenv_param_map
names = ["T_2M", "U_10M", "V_10M", "TOT_PREC", "ASWDIR_S", "ASWDIFD_S", "U"]
print(dict(zip(names, uenv_param_map.shortname_to_paramid(names))))
PY

cat > t2m_step0.mars <<'EOF'
retrieve,
  class=rd,
  stream=reanl,
  expver=r001,
  model=icon-rea-l-ch1,
  type=cf,
  date=20100101,
  time=0000,
  levtype=sfc,
  param=500011,
  step=0
EOF

echo
echo "== request =="
cat t2m_step0.mars

echo
echo "== fdb-read T_2M step 0 =="
rm -f t2m_step0.grib
set +e
uenv run --view=rea-l-ch1 "$image" -- fdb-read --statistics t2m_step0.mars t2m_step0.grib
rc=$?
set -e
echo "fdb-read exit_code=$rc"

if [ "$rc" -eq 0 ] && [ -s t2m_step0.grib ]; then
  ls -lh t2m_step0.grib
  uenv run --view=rea-l-ch1 "$image" -- grib_ls \
    -p shortName,name,paramId,units,gridType,numberOfPoints,typeOfLevel,level,stepType,stepRange,startStep,endStep,dataDate,dataTime,validityDate,validityTime \
    t2m_step0.grib | sed -n '1,80p'
else
  if [ "$rc" -eq 0 ]; then
    echo "fdb-read returned success but produced an empty result" >&2
    rc=1
  fi
  echo
  echo "== ACL diagnostic =="
  getfacl -p /store_new/mch/msopr/rea-l-ch1/fdb/data 2>&1 | sed -n '1,120p'
  ls -ld /store_new/mch/msopr/rea-l-ch1/fdb/data 2>&1 || true
fi

cat > hhl_level1.mars <<'EOF'
retrieve,
  class=rd,
  stream=reanl,
  expver=r001,
  model=icon-rea-l-ch1,
  type=cf,
  date=20100101,
  time=0000,
  levtype=ml,
  levelist=1,
  param=500008,
  step=0
EOF

echo
echo "== fdb-read HHL level 1 =="
rm -f hhl_level1.grib
uenv run --view=rea-l-ch1 "$image" -- fdb-read --statistics hhl_level1.mars hhl_level1.grib
if [ ! -s hhl_level1.grib ]; then
  echo "HHL fdb-read returned an empty result" >&2
  exit 1
fi
uenv run --view=rea-l-ch1 "$image" -- grib_ls \
  -p shortName,name,paramId,units,gridType,numberOfPoints,typeOfLevel,level,stepType,stepRange,dataDate,dataTime \
  hhl_level1.grib | sed -n '1,80p'

exit "$rc"
REMOTE
