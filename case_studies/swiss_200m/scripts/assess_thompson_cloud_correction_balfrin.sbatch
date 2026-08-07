#!/bin/bash
# Convert the completed Thompson causal assessment into a frozen gate decision.
# This post-processing job never invokes HICAR.
#SBATCH --partition=pp-short
#SBATCH --time=00:10:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G

set -euo pipefail

python=${HICAR_VALIDATION_PYTHON:?Set the validated Python interpreter}
assessor=${HICAR_THOMPSON_GATE_ASSESSOR:?Set frozen correction assessor}
correction_contract=${HICAR_THOMPSON_CORRECTION_CONTRACT:?Set correction contract}
acceptance_contract=${HICAR_THOMPSON_ACCEPTANCE_CONTRACT:?Set predeclared acceptance contract}
baseline=${HICAR_THOMPSON_BASELINE_REPORT:?Set Morrison causal report}
candidate=${HICAR_THOMPSON_CANDIDATE_REPORT:?Set Thompson causal report}
model_publication=${HICAR_THOMPSON_MODEL_PUBLICATION:?Set Thompson model publication}
report=${HICAR_THOMPSON_GATE_REPORT:?Set output report}

for path in "$python" "$assessor" "$correction_contract" "$acceptance_contract" "$baseline" \
            "$candidate" "${candidate}.ready" "$model_publication" "${model_publication}.ready"; do
  test -e "$path" || { echo "missing gate input: $path" >&2; exit 2; }
done
"$python" - "$model_publication" <<'PY'
import json
import sys

publication = json.load(open(sys.argv[1]))
if publication.get("status") != "PASS":
    raise SystemExit("Thompson model publication is not PASS")
PY

test ! -e "$report" || { echo "gate report already exists: $report" >&2; exit 2; }
"$python" "$assessor" \
  --correction-contract "$correction_contract" \
  --acceptance-contract "$acceptance_contract" \
  --baseline-report "$baseline" \
  --candidate-report "$candidate" \
  --report "$report"
test -e "$report" && test -e "${report}.ready"
