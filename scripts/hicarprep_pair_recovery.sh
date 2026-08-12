#!/usr/bin/env bash
# Recover only unpublished forcing/LBC pairs. Ready-marked artifacts are never
# changed here; their scientific validation remains the producer's job.

hicarprep_recover_pair() {
  local forcing=${1:?forcing path required}
  local boundary=${2:?boundary path required}
  local forcing_ready="$forcing.ready"
  local boundary_ready="$boundary.ready"
  local forcing_published=0 boundary_published=0

  test -e "$forcing_ready" && forcing_published=1
  test -e "$boundary_ready" && boundary_published=1
  if test "$forcing_published" -ne "$boundary_published"; then
    echo "forcing pair has inconsistent ready markers: $forcing / $boundary" >&2
    return 2
  fi
  if test "$forcing_published" -eq 1; then
    if ! test -f "$forcing" || ! test -f "$boundary"; then
      echo "ready-marked forcing pair is missing a payload: $forcing / $boundary" >&2
      return 2
    fi
    echo ready
    return 0
  fi

  local forcing_exists=0 boundary_exists=0
  test -e "$forcing" && forcing_exists=1
  test -e "$boundary" && boundary_exists=1
  if test "$forcing_exists" -eq 1 && test "$boundary_exists" -eq 1; then
    echo complete_unmarked
    return 0
  fi
  if test "$forcing_exists" -eq 0 && test "$boundary_exists" -eq 0; then
    echo clean
    return 0
  fi

  local existing parent stem quarantine
  if test "$forcing_exists" -eq 1; then
    existing=$forcing
  else
    existing=$boundary
  fi
  parent=$(dirname "$existing")
  stem=$(basename "${forcing%.nc}")
  quarantine="$parent/.unpublished/$stem.${SLURM_JOB_ID:-$$}"
  mkdir -p "$parent/.unpublished"
  mkdir "$quarantine"
  mv -- "$existing" "$quarantine/"
  echo "quarantined unpublished forcing artifact in $quarantine" >&2
  echo clean
}
