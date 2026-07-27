#!/bin/bash
# Run one guard process while forwarding Slurm batch-shell termination signals.

run_with_preemption_signal_forwarding() {
  local guard_pid=""
  local guard_status=0
  local pending_signal=""
  local restore_errexit=0

  case "$-" in
    *e*) restore_errexit=1 ;;
  esac

  forward_preemption_signal() {
    local signal_name=$1
    pending_signal=$signal_name
    if test -n "$guard_pid" && kill -0 "$guard_pid" 2>/dev/null; then
      kill "-$signal_name" "$guard_pid"
    fi
  }

  trap 'forward_preemption_signal TERM' TERM
  trap 'forward_preemption_signal USR1' USR1
  "$@" &
  guard_pid=$!
  if test -n "$pending_signal" && kill -0 "$guard_pid" 2>/dev/null; then
    kill "-$pending_signal" "$guard_pid"
  fi

  set +e
  while true; do
    wait "$guard_pid"
    guard_status=$?
    if ! kill -0 "$guard_pid" 2>/dev/null; then
      break
    fi
  done
  if test "$restore_errexit" -eq 1; then
    set -e
  fi
  trap - TERM USR1
  unset -f forward_preemption_signal
  return "$guard_status"
}
