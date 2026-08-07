#!/bin/bash -l
# Assess the two direct-native cold starts against unchanged July controls.
#SBATCH --job-name=native-land-assess
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=01:00:00

set -euo pipefail
python=${HICAR_VALIDATION_PYTHON:?HICAR_VALIDATION_PYTHON is required}
preparer=${COLD_START_ASSESSMENT_PREPARER:?COLD_START_ASSESSMENT_PREPARER is required}
assessor=${CHRONOLOGICAL_ASSESSOR:?CHRONOLOGICAL_ASSESSOR is required}
baseline_contract=${BASELINE_CONTRACT:?BASELINE_CONTRACT is required}
baseline_completion=${BASELINE_COMPLETION:?BASELINE_COMPLETION is required}
candidate_completion=${CANDIDATE_COMPLETION:?CANDIDATE_COMPLETION is required}
candidate_static_2=${CANDIDATE_STATIC_2:?CANDIDATE_STATIC_2 is required}
candidate_static_3=${CANDIDATE_STATIC_3:?CANDIDATE_STATIC_3 is required}
output_root=${OUTPUT_ROOT:?OUTPUT_ROOT is required}

for path in \
  "$python" "$preparer" "$assessor" \
  "$baseline_contract" "$baseline_contract.ready" \
  "$baseline_completion" "$baseline_completion.ready" \
  "$candidate_completion" "$candidate_completion.ready" \
  "$candidate_static_2" "$candidate_static_2.ready" \
  "$candidate_static_3" "$candidate_static_3.ready"; do
  test -e "$path" || { echo "missing assessment input: $path" >&2; exit 2; }
done

mkdir -p "$output_root"
contract=$output_root/contract.json
completion=$output_root/merged_campaign_completion.json
report=$output_root/native_land_cold_start_assessment.json
for path in "$contract" "$completion" "$report"; do
  test ! -e "$path" && test ! -e "$path.ready" || {
    echo "refusing to overwrite assessment publication: $path" >&2
    exit 2
  }
done

"$python" "$preparer" \
  --baseline-contract "$baseline_contract" \
  --baseline-completion "$baseline_completion" \
  --candidate-completion "$candidate_completion" \
  --candidate-static-2 "$candidate_static_2" \
  --candidate-static-3 "$candidate_static_3" \
  --output-contract "$contract" \
  --output-completion "$completion"

"$python" "$assessor" \
  --contract "$contract" \
  --campaign-completion "$completion" \
  --report "$report"
