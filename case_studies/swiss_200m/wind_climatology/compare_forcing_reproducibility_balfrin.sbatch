#!/usr/bin/env bash
#SBATCH --job-name=forcing-repro-compare
#SBATCH --partition=pp-short
#SBATCH --time=00:20:00
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --output=forcing-repro-compare-%j.out
#SBATCH --error=forcing-repro-compare-%j.err

set -euo pipefail

: "${HICAR_PROBE_PLAN:?set HICAR_PROBE_PLAN}"
: "${HICAR_ANALYSIS_RUNTIME:?set HICAR_ANALYSIS_RUNTIME}"
: "${HICAR_ANALYSIS_PYTHON:?set HICAR_ANALYSIS_PYTHON}"
: "${HICAR_PROBE_REPORT:?set HICAR_PROBE_REPORT}"

IFS=$'\t' read -r first second < <(
  "$HICAR_ANALYSIS_PYTHON" - "$HICAR_PROBE_PLAN" <<'PY'
import json
import sys
plan = json.load(open(sys.argv[1]))
print(*(record["forcing_file"] for record in plan["records"]), sep="\t")
PY
)

exec "$HICAR_ANALYSIS_PYTHON" \
  "$HICAR_ANALYSIS_RUNTIME/case_studies/swiss_200m/wind_climatology/compare_netcdf_arrays.py" \
  --first "$first" \
  --second "$second" \
  --output "$HICAR_PROBE_REPORT"
