#!/bin/bash -l
# Apply a published REA-L land state to the Switzerland 200 m HICAR domain.
#SBATCH --job-name=hicar-land-init
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=24G
#SBATCH --time=00:30:00

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

repo_root=${REPO_ROOT:-$SCRATCH/icon_hicar}
date=${REA_L_LAND_DATE:-20200701}
time=${REA_L_LAND_TIME:-0000}
case_root=$repo_root/case_studies/swiss_200m
python=${HICAR_VALIDATION_PYTHON:-$repo_root/venv_static/bin/python}
builder=$case_root/validation/build_rea_l_land_initialization.py
template=${HICAR_STATIC_TEMPLATE:-$case_root/static/domain_static_swiss_200m.nc}
source=${REA_L_LAND_STATE:-$repo_root/streaming/rea_l_ch1/land_state/rea_l_land_state_${date}_${time}.nc}
output_dir=${HICAR_LAND_INIT_DIR:-$case_root/static_initializations}
output=$output_dir/domain_static_swiss_200m_rea_l_${date}_${time}.nc
manifest=$output_dir/domain_static_swiss_200m_rea_l_${date}_${time}.manifest.json

for path in "$python" "$builder" "$template" "$template.ready" "$source" \
  "$source.ready"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done
mkdir -p "$output_dir"
args=(
  --static-template "$template"
  --land-state "$source"
  --output "$output"
  --manifest "$manifest"
  --valid-time "${date:0:4}-${date:4:2}-${date:6:2}T${time:0:2}:${time:2:2}:00"
)
if test -n "${REA_L_SNOW_DENSITY_FALLBACK:-}"; then
  args+=(--snow-density-fallback "$REA_L_SNOW_DENSITY_FALLBACK")
fi
"$python" "$builder" "${args[@]}"
echo "PASS: HICAR land initialization published at $output"
