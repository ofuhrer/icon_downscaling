#!/usr/bin/env bash
#SBATCH --partition=pp-long
#SBATCH --nodes=1
#SBATCH --exclusive
#SBATCH --cpus-per-task=8
#SBATCH --mem=400G
#SBATCH --time=01:00:00
#SBATCH --job-name=hprep-opt

set -euo pipefail

[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

: "${OPTIMIZATION_ROOT:?set OPTIMIZATION_ROOT}"
: "${HICARPREP_PYTHON:?set HICARPREP_PYTHON}"
: "${ICON_STATE:?set ICON_STATE}"
: "${STATIC_FILE:?set STATIC_FILE}"
: "${WEIGHTS_FILE:?set WEIGHTS_FILE}"
: "${TARGET_SST_FILE:?set TARGET_SST_FILE}"
: "${REFERENCE_FORCING:?set REFERENCE_FORCING}"
: "${REFERENCE_BOUNDARY:?set REFERENCE_BOUNDARY}"
: "${SOURCE_ID:?set SOURCE_ID}"

source_root="$OPTIMIZATION_ROOT/source"
output_root="$OPTIMIZATION_ROOT/output"
mkdir -p "$output_root" "$OPTIMIZATION_ROOT/numba_cache"
export PYTHONPATH="$source_root:$OPTIMIZATION_ROOT/vendor_core"
export NUMBA_CACHE_DIR="$OPTIMIZATION_ROOT/numba_cache"
column_workers=${COLUMN_WORKERS:-8}
export OMP_NUM_THREADS="$column_workers"

variant=${VARIANT:-numba}
forcing="$output_root/forcing_20201001_0900_${variant}.nc"
boundary="$output_root/forcing_20201001_0900_${variant}.lbc.nc"
profile="$output_root/profile_${variant}.json"

/usr/bin/time -v "$HICARPREP_PYTHON" \
    "$source_root/profile_input_generation.py" profile-record \
    --icon-state "$ICON_STATE" \
    --static "$STATIC_FILE" \
    --weights "$WEIGHTS_FILE" \
    --target-sst "$TARGET_SST_FILE" \
    --output "$forcing" \
    --boundary "$boundary" \
    --report "$profile" \
    --source-commit "$SOURCE_ID" \
    --column-workers "$column_workers" \
    --rbf-backend numba

/usr/bin/time -v "$HICARPREP_PYTHON" "$source_root/validate_forcing.py" \
    --forcing-file "$forcing" \
    --boundary-file "$boundary" \
    --static-file "$STATIC_FILE" \
    --expected-valid-time 2020-10-01T09:00:00

"$HICARPREP_PYTHON" "$source_root/compare_forcing_numerical.py" \
    --reference "$REFERENCE_FORCING" \
    --candidate "$forcing" \
    --report "$output_root/forcing_numerical_comparison_${variant}.json"
"$HICARPREP_PYTHON" "$source_root/compare_forcing_numerical.py" \
    --reference "$REFERENCE_BOUNDARY" \
    --candidate "$boundary" \
    --report "$output_root/boundary_numerical_comparison_${variant}.json"

sha256sum "$forcing" "$boundary" "$profile" \
    "$output_root/forcing_numerical_comparison_${variant}.json" \
    "$output_root/boundary_numerical_comparison_${variant}.json"
