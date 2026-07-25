#!/bin/bash -l
# Validate and publish all three-hourly REA-L references after the producer array.
#SBATCH --job-name=rea-l-reference-finalize
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=00:15:00

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

plan=${STREAM_PLAN:?Set STREAM_PLAN to a published chunk_plan.json}
repo_root=${REPO_ROOT:-$SCRATCH/icon_hicar}
python=${HICAR_VALIDATION_PYTHON:-$repo_root/venv_static/bin/python}
finalizer=$repo_root/case_studies/swiss_200m/validation/finalize_rea_l_event_reference.py
for path in "$plan" "$plan.ready" "$python" "$finalizer"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done
"$python" "$finalizer" --plan "$plan"
