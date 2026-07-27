#!/bin/bash -l
# Controller-only wrapper for the bounded real-HICAR recovery qualification.
# HICAR attempts are submitted separately on preemptible by the controller.
#SBATCH --job-name=hicar-real-recovery
#SBATCH --partition=pp-long
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=2G
#SBATCH --time=04:00:00
#SBATCH --no-requeue

set -euo pipefail
repo_root=${REPO_ROOT:?Set REPO_ROOT to the immutable runtime release}
. "$repo_root/scripts/load_balfrin_site_config.sh"
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

python=${HICAR_VALIDATION_PYTHON:?Set HICAR_VALIDATION_PYTHON}
campaign=${HICAR_CAMPAIGN_PLAN:?Set HICAR_CAMPAIGN_PLAN}
report=${HICAR_RECOVERY_QUALIFICATION_REPORT:?Set HICAR_RECOVERY_QUALIFICATION_REPORT}
qualifier="$repo_root/orchestration/qualify_hicar_preemptible_recovery.py"

for path in "$python" "$campaign" "$campaign.ready" "$qualifier"; do
  test -e "$path" || { echo "missing recovery qualification input: $path" >&2; exit 2; }
done

exec "$python" "$qualifier" \
  --campaign "$campaign" \
  --repo-root "$repo_root" \
  --report "$report" \
  --execute
