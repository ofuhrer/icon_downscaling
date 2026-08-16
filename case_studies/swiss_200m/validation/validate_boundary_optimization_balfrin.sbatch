#!/usr/bin/env bash
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=64G
#SBATCH --time=00:20:00
#SBATCH --job-name=lbc-validate

set -euo pipefail

[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

: "${VALIDATION_PYTHON:?set VALIDATION_PYTHON}"
: "${SOURCE_ROOT:?set SOURCE_ROOT}"
: "${STATIC_FILE:?set STATIC_FILE}"
: "${REFERENCE_FORCING:?set REFERENCE_FORCING}"
: "${REFERENCE_BOUNDARY:?set REFERENCE_BOUNDARY}"
: "${CANDIDATE_FORCING:?set CANDIDATE_FORCING}"
: "${CANDIDATE_BOUNDARY:?set CANDIDATE_BOUNDARY}"
: "${REPORT_ROOT:?set REPORT_ROOT}"

mkdir -p "$REPORT_ROOT"
export PYTHONPATH="$SOURCE_ROOT:${PYTHONPATH:-}"

"$VALIDATION_PYTHON" "$SOURCE_ROOT/validate_forcing.py" \
  --forcing-file "$CANDIDATE_FORCING" \
  --boundary-file "$CANDIDATE_BOUNDARY" \
  --static-file "$STATIC_FILE" \
  --expected-valid-time 2020-10-01T09:00:00
"$VALIDATION_PYTHON" "$SOURCE_ROOT/compare_forcing_numerical.py" \
  --reference "$REFERENCE_FORCING" \
  --candidate "$CANDIDATE_FORCING" \
  --report "$REPORT_ROOT/forcing_numerical_comparison_bc_final_accepted.json"
"$VALIDATION_PYTHON" "$SOURCE_ROOT/compare_forcing_numerical.py" \
  --reference "$REFERENCE_BOUNDARY" \
  --candidate "$CANDIDATE_BOUNDARY" \
  --report "$REPORT_ROOT/boundary_numerical_comparison_bc_final_accepted.json"

sha256sum "$CANDIDATE_FORCING" "$CANDIDATE_BOUNDARY" \
  "$REPORT_ROOT/forcing_numerical_comparison_bc_final_accepted.json" \
  "$REPORT_ROOT/boundary_numerical_comparison_bc_final_accepted.json"
