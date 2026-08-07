#!/bin/bash -l
# Prepare the small exact-grid forcing set for the terrain-radiation model gate.
#SBATCH --job-name=terrain-rad-input
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --time=00:30:00
#SBATCH --output=terrain_rad_input_%j.out
#SBATCH --error=terrain_rad_input_%j.err

set -euo pipefail

coordinator=${HICAR_COORDINATOR_ROOT:?Set HICAR_COORDINATOR_ROOT}
static_file=${HICAR_TERRAIN_FLAT_STATIC:?Set HICAR_TERRAIN_FLAT_STATIC}
source_dir=${HICAR_TERRAIN_SOURCE_FORCING:?Set HICAR_TERRAIN_SOURCE_FORCING}
output_dir=${HICAR_TERRAIN_INPUT_ROOT:?Set HICAR_TERRAIN_INPUT_ROOT}
python=${HICAR_VALIDATION_PYTHON:-${SCRATCH:?}/icon_hicar/venv_static/bin/python}
preparer=$coordinator/case_studies/swiss_200m/fixed_parameters/prepare_terrain_radiation_model_gate.py

for path in "$python" "$preparer" "$static_file" "${static_file}.ready"; do
  test -e "$path" || { echo "missing required input: $path" >&2; exit 2; }
done
if [[ -f "$output_dir/execution_plan.json.ready" ]]; then
  printf 'PASS reused_input_root=%s\n' "$output_dir"
  exit 0
fi
if [[ -e "$output_dir" ]]; then
  mv "$output_dir" "${output_dir}.failed-${SLURM_JOB_ID:?}"
fi

"$python" "$preparer" \
  --static-file "$static_file" \
  --source-dir "$source_dir" \
  --output-dir "$output_dir"

test -f "$output_dir/execution_plan.json.ready"
printf 'PASS input_root=%s\n' "$output_dir"
