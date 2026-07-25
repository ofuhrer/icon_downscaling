#!/bin/bash -l
# Publish public RhiresD, TabsD, and monthly SIS references.
#SBATCH --job-name=ogd-grid-reference
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=01:00:00

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

repo_root=${REPO_ROOT:-$SCRATCH/icon_hicar}
python=${HICAR_VALIDATION_PYTHON:-$repo_root/venv_static/bin/python}
retriever=$repo_root/case_studies/swiss_200m/validation/retrieve_ogd_grid_references.py
output_dir=${OGD_REFERENCE_DIR:-$repo_root/observations/ogd_grid/2020}
manifest=${OGD_REFERENCE_MANIFEST:-$output_dir/ogd_grid_reference_2020_events.json}
period_start=${OGD_PERIOD_START:-}
period_end=${OGD_PERIOD_END_EXCLUSIVE:-}

for path in "$python" "$retriever"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done
selection=(--year 2020 --month 1 --month 7)
if [[ -n "$period_start" || -n "$period_end" ]]; then
  [[ -n "$period_start" && -n "$period_end" ]] || {
    echo "set both OGD_PERIOD_START and OGD_PERIOD_END_EXCLUSIVE" >&2
    exit 2
  }
  selection=(
    --period-start "$period_start"
    --period-end-exclusive "$period_end"
  )
fi

"$python" "$retriever" \
  "${selection[@]}" \
  --output-dir "$output_dir" \
  --manifest "$manifest"
test -f "$manifest.ready"
