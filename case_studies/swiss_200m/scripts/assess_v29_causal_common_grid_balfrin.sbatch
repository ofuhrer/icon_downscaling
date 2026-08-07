#!/bin/bash -l
# Common-grid causal state assessment; reads published diagnostics only.
#SBATCH --job-name=hicar-v29-causal-grid
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=12G
#SBATCH --time=00:30:00

set -euo pipefail

python=${HICAR_VALIDATION_PYTHON:?Set immutable validation Python}
assessor=${V29_CAUSAL_ASSESSOR:?Set immutable common-grid assessor}
history=${V29_HISTORY:?Set validated V29 mechanism history}
static_file=${V29_STATIC_FILE:?Set published HICAR static initialization}
land_publication=${V29_LAND_PUBLICATION:?Set PASS land-history publication}
land_dir=${V29_LAND_HISTORY_DIR:?Set published land-history directory}
reference_dir=${V29_REFERENCE_DIR:?Set extracted REA-L surface-reference directory}
report=${V29_CAUSAL_REPORT:?Set new common-grid report}

for path in "$python" "$assessor" "$history" "$static_file" "$static_file.ready" \
  "$land_publication" "$land_publication.ready" "$land_dir" "$reference_dir"; do
  test -e "$path" || { echo "missing causal-assessment input: $path" >&2; exit 2; }
done

"$python" "$assessor" \
  --hicar-history "$history" \
  --hicar-static "$static_file" \
  --land-publication "$land_publication" \
  --land-dir "$land_dir" \
  --surface-reference-dir "$reference_dir" \
  --report "$report"

test -s "$report"
test -f "$report.ready"
