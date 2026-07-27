#!/bin/bash -l
# Aggregate a stream chunk after all producer-array records have completed.
#SBATCH --job-name=rea-l-stream-finalize
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=2G
#SBATCH --time=00:15:00

set -euo pipefail
repo_root=${REPO_ROOT:?Set REPO_ROOT to the immutable runtime release}
. "$repo_root/scripts/load_balfrin_site_config.sh"
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module load python/3.11.7

plan=${STREAM_PLAN:?Set STREAM_PLAN to a published chunk_plan.json}
finalizer="$repo_root/case_studies/swiss_200m/streaming/finalize_forcing_chunk.py"
python=${HICAR_VALIDATION_PYTHON:?Set HICAR_VALIDATION_PYTHON to the published runtime interpreter}
for path in "$plan" "$plan.ready" "$finalizer" "$python"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done
"$python" "$finalizer" --plan "$plan"
