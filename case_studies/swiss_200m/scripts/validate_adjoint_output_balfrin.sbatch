#!/bin/bash -l
# Validate the initial and 30-minute evolved national records away from GPU
# nodes. HICAR appends both records to one start-time-named NetCDF file.
# Submit with HICAR_SWISS_RUN set to an accepted adjoint_swiss_<jobid>.
#SBATCH --job-name=hicar-adjoint-output
#SBATCH --partition=pp-long
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=32G
#SBATCH --time=04:00:00

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module purge || true
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module load python/3.11.7

run=${HICAR_SWISS_RUN:?Set HICAR_SWISS_RUN to the accepted Swiss run directory}
validator=${HICAR_OUTPUT_VALIDATOR:-$SCRATCH/icon_hicar/scripts/validate_hicar_wind_output.py}
python=${HICAR_VALIDATION_PYTHON:-$SCRATCH/icon_hicar/venv_static/bin/python}
output="$run/output/domain_static_swiss_200m_2010-01-01_00-00-00.nc"
report="$run/output_validation.json"

test -x "$python"
test -f "$validator"
test -s "$output"
rm -f "$report.ready"
"$python" "$validator" \
  --expected-levels 80 \
  --expected-times 2 \
  --max-elements 2000000 \
  --report "$report" \
  "$output"
test -s "$report"
touch "$report.ready"

echo "Swiss adjoint output validation passed: report=$report"
