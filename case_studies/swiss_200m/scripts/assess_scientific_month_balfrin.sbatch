#!/bin/bash -l
# Apply the frozen month-to-annual scientific and archive decision contract.
#SBATCH --job-name=hicar-month-assess
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=2G
#SBATCH --time=00:10:00

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

month_plan=${MONTH_PILOT_PLAN:?Set MONTH_PILOT_PLAN}
repo_root=${REPO_ROOT:-$SCRATCH/icon_hicar}
python=${HICAR_VALIDATION_PYTHON:-$repo_root/venv_static/bin/python}
assessor=$repo_root/case_studies/swiss_200m/validation/assess_scientific_month.py

for path in "$month_plan" "$month_plan.ready" "$python" "$assessor"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done

"$python" "$assessor" --month-plan "$month_plan"
