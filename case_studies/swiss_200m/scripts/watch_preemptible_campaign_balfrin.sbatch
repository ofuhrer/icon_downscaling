#!/bin/bash -l
# Persistent lightweight reconciler; heavy HICAR attempts use preemptible.
#SBATCH --job-name=hicar-campaign-watch
#SBATCH --partition=pp-long
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=2G
#SBATCH --time=23:30:00
#SBATCH --no-requeue

set -euo pipefail
. "$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)/scripts/load_balfrin_site_config.sh"
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

repo_root=${REPO_ROOT:-$SCRATCH/icon_hicar}
python=${HICAR_VALIDATION_PYTHON:-$repo_root/venv_static/bin/python}
campaign=${HICAR_CAMPAIGN_PLAN:?Set HICAR_CAMPAIGN_PLAN}
controller="$repo_root/orchestration/preemptible_campaign.py"

for path in "$python" "$campaign" "$campaign.ready" "$controller"; do
  test -e "$path" || { echo "missing campaign controller input: $path" >&2; exit 2; }
done

"$python" "$controller" init \
  --campaign "$campaign" \
  --repo-root "$repo_root" >/dev/null
state_path=$("$python" - "$campaign" <<'PY'
import json
import sys

print(json.load(open(sys.argv[1]))["controller"]["state"])
PY
)
campaign_status=$("$python" - "$state_path" <<'PY'
import json
import sys

print(json.load(open(sys.argv[1]))["status"])
PY
)
case "${HICAR_CAMPAIGN_CHAIN_WATCHER:-1}" in
  1|true|TRUE)
    if test "$campaign_status" = ACTIVE; then
      successor=""
      for _attempt in 1 2 3; do
        if successor=$(sbatch --parsable --no-requeue \
          --dependency="afterany:${SLURM_JOB_ID:?}" \
          --export="ALL,REPO_ROOT=$repo_root,HICAR_CAMPAIGN_PLAN=$campaign,HICAR_CAMPAIGN_POLL_SECONDS=${HICAR_CAMPAIGN_POLL_SECONDS:-60},HICAR_CAMPAIGN_WATCH_SECONDS=${HICAR_CAMPAIGN_WATCH_SECONDS:-82800},HICAR_CAMPAIGN_CHAIN_WATCHER=1" \
          "$0"); then
          break
        fi
        sleep 2
      done
      if test -n "$successor"; then
        echo "campaign watcher successor submitted: $successor"
      else
        echo "warning: watcher successor submission failed after three attempts" >&2
      fi
    fi
    ;;
  0|false|FALSE) ;;
  *) echo "HICAR_CAMPAIGN_CHAIN_WATCHER must be true/false or 1/0" >&2; exit 2 ;;
esac

exec "$python" "$controller" watch \
  --campaign "$campaign" \
  --repo-root "$repo_root" \
  --execute \
  --poll-seconds "${HICAR_CAMPAIGN_POLL_SECONDS:-60}" \
  --max-seconds "${HICAR_CAMPAIGN_WATCH_SECONDS:-82800}"
