#!/bin/bash -l
# Audit the exact REA-L surface-reference fields and accumulation semantics.
#SBATCH --job-name=rea-l-surface-audit
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=00:15:00

set -euo pipefail
repo_root=${REPO_ROOT:-$SCRATCH/icon_hicar}
# shellcheck source=scripts/load_balfrin_site_config.sh
. "$repo_root/scripts/load_balfrin_site_config.sh"
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"

uenv_bin=${UENV_BIN:-/usr/bin/uenv}
image=${REA_FDB_IMAGE:?REA_FDB_IMAGE is required}
output=${REA_L_SURFACE_AUDIT_REPORT:-$repo_root/case_studies/swiss_200m/validation/rea_l_surface_reference_inventory.txt}
mkdir -p "$(dirname "$output")" "$repo_root/tmp"
work=$(mktemp -d "$repo_root/tmp/rea_l_surface_audit.XXXXXX")
trap 'rm -rf "$work"' EXIT

cat > "$work/current.mars" <<'EOF'
retrieve,
  class=rd,
  stream=reanl,
  expver=r001,
  model=icon-rea-l-ch1,
  type=cf,
  date=20200701,
  time=0000,
  levtype=sfc,
  param=500000/500011/500017/500027/500029/500041/500045/500147,
  step=3
EOF
cat > "$work/precip_start.mars" <<'EOF'
retrieve,
  class=rd,
  stream=reanl,
  expver=r001,
  model=icon-rea-l-ch1,
  type=cf,
  date=20200701,
  time=0000,
  levtype=sfc,
  param=500041,
  step=0
EOF

"$uenv_bin" run --view=rea-l-ch1 "$image" -- \
  fdb-read --statistics "$work/current.mars" "$work/current.grib"
"$uenv_bin" run --view=rea-l-ch1 "$image" -- \
  fdb-read --statistics "$work/precip_start.mars" "$work/precip_start.grib"

partial="$output.partial.${SLURM_JOB_ID:?}"
{
  echo "schema_version=1"
  echo "source=ICON REA-L-CH1 FDB"
  echo "fdb_image=$image"
  echo "probe_cycle=20200701T0000"
  echo "probe_step_hours=3"
  "$uenv_bin" run --view=rea-l-ch1 "$image" -- \
    grib_ls -p shortName,paramId,name,units,typeOfLevel,level,stepType,stepRange,dataDate,dataTime,validityDate,validityTime,numberOfPoints \
    "$work/current.grib" "$work/precip_start.grib"
} > "$partial"
mv "$partial" "$output"
sha256sum "$output" > "$output.sha256"
touch "$output.ready"
echo "PASS: published $output"
