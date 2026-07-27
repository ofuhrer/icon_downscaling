#!/bin/bash -l
# Engineering-only signal/retry probe. It never runs HICAR or publishes science.
#SBATCH --job-name=hicar-preempt-probe
#SBATCH --partition=preemptible
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --gres=gpu:4
#SBATCH --time=00:10:00
#SBATCH --exclusive
#SBATCH --no-requeue

set -euo pipefail
repo_root=${REPO_ROOT:?Set REPO_ROOT to the immutable runtime release}
. "$repo_root/scripts/load_balfrin_site_config.sh"
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

python=${HICAR_VALIDATION_PYTHON:?Set HICAR_VALIDATION_PYTHON to the published runtime interpreter}
helper=${HICAR_PREEMPTION_HELPER:-$repo_root/orchestration/preemption.py}
signal_forwarder="$repo_root/scripts/preemption_signal_forwarding.sh"
attempt_id=${STREAM_ATTEMPT_ID:?Set STREAM_ATTEMPT_ID}
run=${STREAM_RUN_DIR:?Set STREAM_RUN_DIR}
sleep_seconds=${HICAR_PREEMPTION_PROBE_SLEEP_SECONDS:-600}

case "$sleep_seconds" in
  ''|*[!0-9]*) echo "probe sleep must be an integer" >&2; exit 2 ;;
esac
test "$sleep_seconds" -ge 60 || {
  echo "probe sleep must be at least 60 seconds" >&2
  exit 2
}
for path in "$python" "$helper" "$signal_forwarder"; do
  test -e "$path" || { echo "missing recovery probe input: $path" >&2; exit 2; }
done
mkdir -p "$run"
. "$signal_forwarder"

run_with_preemption_signal_forwarding "$python" "$helper" run \
  --attempt-id "$attempt_id" \
  --report "$run/attempt_interrupted.json" \
  --completion-marker "$run/model_chunk_completion.json.ready" \
  --log "$run/probe.out" \
  -- "$python" -c \
  'import pathlib,sys,time; pathlib.Path(sys.argv[1]).touch(); time.sleep(int(sys.argv[2]))' \
  "$run/probe_started.ready" "$sleep_seconds"
