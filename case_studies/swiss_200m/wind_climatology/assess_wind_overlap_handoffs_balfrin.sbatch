#!/bin/bash
#SBATCH --job-name=hicar-overlap-handoffs
#SBATCH --partition=pp-short
#SBATCH --time=01:00:00
#SBATCH --cpus-per-task=8
#SBATCH --ntasks=1
#SBATCH --no-requeue

set -euo pipefail

[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh

: "${HICAR_OVERLAP_RUNTIME:?set HICAR_OVERLAP_RUNTIME}"
: "${HICAR_VALIDATION_PYTHON:?set HICAR_VALIDATION_PYTHON}"
: "${HICAR_WIND_CAMPAIGN:?set HICAR_WIND_CAMPAIGN}"
: "${HICAR_OVERLAP_REPORT:?set HICAR_OVERLAP_REPORT}"

runtime_manifest="$HICAR_OVERLAP_RUNTIME/runtime_manifest.json"
test -f "$runtime_manifest"
test -f "$runtime_manifest.ready"
test -x "$HICAR_VALIDATION_PYTHON"

exec "$HICAR_VALIDATION_PYTHON" \
    "$HICAR_OVERLAP_RUNTIME/assess_wind_overlap_handoffs.py" \
    --experiment "$HICAR_WIND_CAMPAIGN/manifests/experiment.json" \
    --results "$HICAR_WIND_CAMPAIGN/analysis/wind_spinup_results.json" \
    --convergence-decision \
        "$HICAR_WIND_CAMPAIGN/analysis/wind_spinup_convergence_decision.json" \
    --report "$HICAR_OVERLAP_REPORT"
