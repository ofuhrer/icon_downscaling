#!/bin/bash -l
# Apply the frozen annual-to-20-year scientific and production decision.
#SBATCH --job-name=hicar-annual-assess
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=00:15:00

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

annual_plan=${ANNUAL_PILOT_PLAN:?Set ANNUAL_PILOT_PLAN}
repo_root=${REPO_ROOT:-$SCRATCH/icon_hicar}
python=${HICAR_VALIDATION_PYTHON:-$repo_root/venv_static/bin/python}
assessor=$repo_root/case_studies/swiss_200m/validation/assess_scientific_annual.py

for path in "$annual_plan" "$annual_plan.ready" "$python" "$assessor"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done

"$python" "$assessor" --annual-plan "$annual_plan"
