#!/bin/bash -l
# Independently read back a published recovery archive on a CPU node.
#SBATCH --job-name=hicar-recovery-verify
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
manifest=${RECOVERY_ARCHIVE_MANIFEST:?Set RECOVERY_ARCHIVE_MANIFEST}
verifier=$repo_root/scripts/verify_recovery_archive.py

for path in "$manifest" "$verifier"; do
  test -f "$path" || {
    echo "missing required file: $path" >&2
    exit 2
  }
done

exec python3 "$verifier" --manifest "$manifest"
