#!/bin/bash -l
# Run one task from the campaign-wide, globally throttled CPU array.
#SBATCH --job-name=hicar-campaign-cpu
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=8G
#SBATCH --time=01:00:00
#SBATCH --no-requeue

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

repo_root=${REPO_ROOT:-$SCRATCH/icon_hicar}
python=${HICAR_VALIDATION_PYTHON:-$repo_root/venv_static/bin/python}
task_file=${HICAR_CAMPAIGN_CPU_TASK_FILE:?Set HICAR_CAMPAIGN_CPU_TASK_FILE}
task_sha256=${HICAR_CAMPAIGN_CPU_TASK_SHA256:?Set HICAR_CAMPAIGN_CPU_TASK_SHA256}
runner="$repo_root/orchestration/run_cpu_task.py"

for path in "$python" "$task_file" "$task_file.ready" "$runner"; do
  test -e "$path" || { echo "missing campaign CPU task input: $path" >&2; exit 2; }
done

exec "$python" "$runner" \
  --task-file "$task_file" \
  --expected-sha256 "$task_sha256" \
  --index "${SLURM_ARRAY_TASK_ID:?}" \
  --repo-root "$repo_root"
