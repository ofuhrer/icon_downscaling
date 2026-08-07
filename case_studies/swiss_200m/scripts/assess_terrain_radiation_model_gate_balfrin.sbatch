#!/bin/bash -l
# Assess the small terrain-radiation gate away from GPU compute nodes.
#SBATCH --job-name=terrain-rad-assess
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --time=00:30:00
#SBATCH --output=terrain_rad_assess_%j.out
#SBATCH --error=terrain_rad_assess_%j.err

set -euo pipefail

coordinator=${HICAR_COORDINATOR_ROOT:?Set HICAR_COORDINATOR_ROOT}
run_root=${HICAR_TERRAIN_RUN_ROOT:?Set HICAR_TERRAIN_RUN_ROOT}
input_root=${HICAR_TERRAIN_INPUT_ROOT:?Set HICAR_TERRAIN_INPUT_ROOT}
python=${HICAR_VALIDATION_PYTHON:-${SCRATCH:?}/icon_hicar/venv_static/bin/python}
assessor=$coordinator/case_studies/swiss_200m/fixed_parameters/assess_terrain_radiation_model_gate.py
output=$run_root/assessment.json

for path in \
  "$python" "$assessor" "$run_root/run_manifest.json.ready" \
  "$input_root/execution_plan.json.ready"; do
  test -e "$path" || { echo "missing required input: $path" >&2; exit 2; }
done
if [[ -f "${output}.ready" ]]; then
  printf 'PASS reused_assessment=%s\n' "$output"
  exit 0
fi
if [[ -e "$output" ]]; then
  mv "$output" "${output}.failed-${SLURM_JOB_ID:?}"
fi

"$python" "$assessor" \
  --run-root "$run_root" \
  --execution-plan "$input_root/execution_plan.json" \
  --output "$output"

test -f "${output}.ready"
