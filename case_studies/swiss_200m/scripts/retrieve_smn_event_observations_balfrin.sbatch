#!/bin/bash -l
# Retrieve hourly SwissMetNet measurements for an R&D comparison window.
#SBATCH --job-name=smn-observations
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=00:30:00

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

start=${START_TIME:?Use YYYY-MM-DDTHH:MM:SS}
end=${END_TIME:?Use YYYY-MM-DDTHH:MM:SS}
output=${OBSERVATION_OUTPUT:?}
jretrieve=${JRETRIEVE_BIN:-/oprusers/osm/opr.inn/bin/jretrievedwh}
export OPR_HOME=${JRETRIEVE_CONFIG_HOME:-/oprusers/osm}
test -x "$jretrieve" || { echo "missing JRetrieve: $jretrieve" >&2; exit 2; }
test -r "$OPR_HOME/.jretrievedwh-conf.prod.py" || { echo "missing JRetrieve config" >&2; exit 2; }
test ! -e "$output" || exit 0
mkdir -p "$(dirname "$output")"
partial="$output.partial.${SLURM_JOB_ID:?}"
trap 'rm -f "$partial"' EXIT

compact() { printf '%s' "$1" | tr -d -- '-:T'; }
parameters=tre200h0,tre200hn,tre200hx,ure200h0,prestah0,rre150h0,fkl010h0,dkl010h0,gre000h0,htoauths
"$jretrieve" --stage prod --timeout 1200 --seq-type surface --groups SMN \
  --time "$(compact "$start"),$(compact "$end"),60" \
  --coordinates "45.7,47.9,5.8,10.7" --parameter-short-name "$parameters" \
  --info "lat,lon,elev,nat_abbr" --quality-info 15 \
  --data-quality-cat-nr-limit 4 --use-limitation 50 \
  --format csv --delimiter CSV --show-records --output "$partial"
test -s "$partial"
mv "$partial" "$output"
