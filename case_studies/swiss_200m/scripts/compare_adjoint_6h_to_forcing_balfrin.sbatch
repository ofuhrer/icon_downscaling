#!/bin/bash -l
# Compare the initial and six-hour HICAR records with the matching REA-L fields.
# Submit with HICAR_SWISS_RUN set to the completed six-hour run directory.
#SBATCH --job-name=hicar-source-compare
#SBATCH --partition=pp-long
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=24G
#SBATCH --time=02:00:00

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module purge || true
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module load python/3.11.7

run=${HICAR_SWISS_RUN:?Set HICAR_SWISS_RUN to the completed six-hour run directory}
case_root=${HICAR_SWISS_CASE:-$SCRATCH/icon_hicar/case_studies/swiss_200m}
python=${HICAR_VALIDATION_PYTHON:-$SCRATCH/icon_hicar/venv_static/bin/python}
validator="$case_root/validation/validate_hicar_against_forcing.py"
output="$run/output/domain_static_swiss_200m_2010-01-01_00-00-00.nc"
forcing_root="$SCRATCH/icon_hicar/case_studies/swiss_100m/forcing/rea_l_ch1"
forcing_start="$forcing_root/20100101_0000/rea_l_hicar_20100101_0000.nc"
forcing_end="$forcing_root/20100101_0000_s06/rea_l_hicar_20100101_0000_s06.nc"
if [ "${HICAR_COMPARE_INITIAL_ONLY:-0}" = 1 ]; then
  report="$run/source_aware_initial_validation.json"
  forcing_arguments=(--forcing "$forcing_start" --hicar-time-index 0)
else
  report="$run/source_aware_validation.json"
  forcing_arguments=(--forcing "$forcing_start" --forcing "$forcing_end")
fi

for path in "$python" "$validator" "$output" "$forcing_start" "$forcing_start.ready" \
            "$forcing_end" "$forcing_end.ready"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done

rm -f "$report" "$report.ready"
"$python" "$validator" \
  "${forcing_arguments[@]}" \
  --horizontal-stride 40 \
  --report "$report" \
  "$output"
test "$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["status"])' "$report")" = PASS
touch "$report.ready"
echo "Swiss source-aware physical validation passed: report=$report"
