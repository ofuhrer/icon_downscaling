#!/bin/bash -l
# Retire one superseded restart and publish a durable lifecycle report.
#SBATCH --job-name=hicar-rst-retire
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=00:30:00

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

previous=${PREVIOUS_MODEL_COMPLETION:?Set PREVIOUS_MODEL_COMPLETION}
successor=${NEXT_MODEL_COMPLETION:?Set NEXT_MODEL_COMPLETION}
report=${RESTART_RETIREMENT_REPORT:?Set RESTART_RETIREMENT_REPORT}
repo_root=${REPO_ROOT:-$SCRATCH/icon_hicar}
python=${HICAR_VALIDATION_PYTHON:-$repo_root/venv_static/bin/python}
retirer=$repo_root/case_studies/swiss_200m/streaming/retire_restart_boundary.py

for path in "$python" "$retirer" "$previous" "$previous.ready" \
  "$successor" "$successor.ready"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done

"$python" "$retirer" \
  --previous-completion "$previous" \
  --next-completion "$successor" \
  --execute \
  --report "$report"
test -f "$report.ready"
echo "restart retirement published: $report"
