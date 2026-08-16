#!/usr/bin/env bash
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --exclusive
#SBATCH --cpus-per-task=1
#SBATCH --mem=64G
#SBATCH --time=00:20:00
#SBATCH --job-name=lbc-encoding

set -euo pipefail

[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

: "${BENCHMARK_PYTHON:?set BENCHMARK_PYTHON}"
: "${BENCHMARK_SCRIPT:?set BENCHMARK_SCRIPT}"
: "${REFERENCE_BOUNDARY:?set REFERENCE_BOUNDARY}"
: "${OUTPUT_ROOT:?set OUTPUT_ROOT}"

mkdir -p "$OUTPUT_ROOT"
"$BENCHMARK_PYTHON" "$BENCHMARK_SCRIPT" \
  --reference "$REFERENCE_BOUNDARY" \
  --output-dir "$OUTPUT_ROOT/products" \
  --report "$OUTPUT_ROOT/boundary_encoding_benchmark.json"
