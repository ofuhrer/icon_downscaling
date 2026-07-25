#!/bin/bash -l
# Publish a bounded recovery archive from an explicit, source-controlled plan.
#SBATCH --job-name=hicar-recovery-archive
#SBATCH --partition=pp-long
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=04:00:00

set -euo pipefail
umask 027
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module load python/3.11.7 >/dev/null

repo_root=${REPO_ROOT:-$SCRATCH/icon_hicar}
plan=${RECOVERY_ARCHIVE_PLAN:?Set RECOVERY_ARCHIVE_PLAN}
archiver=$repo_root/scripts/archive_recovery_plan.py

for path in "$plan" "$archiver"; do
  test -f "$path" || {
    echo "missing required file: $path" >&2
    exit 2
  }
done

exec python3 "$archiver" --plan "$plan"
