#!/bin/bash -l
# Publish the 100 m capacity DAG inputs only after the paired-event GO verdict.
#SBATCH --job-name=hicar-100m-plan
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=2G
#SBATCH --time=00:10:00

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

repo_root=${REPO_ROOT:-$SCRATCH/icon_hicar}
case_root=${HICAR_SWISS_CASE:-$repo_root/case_studies/swiss_100m}
python=${HICAR_VALIDATION_PYTHON:-$repo_root/venv_static/bin/python}
planner=$case_root/streaming/prepare_engineering_capacity_gate.py
config=$case_root/config/engineering_capacity_gate.json
event_assessment=${EVENT_ASSESSMENT_REPORT:-$repo_root/case_studies/swiss_200m/runs/scientific_pilot_event_assessment.json}
source_qualification=${HICAR_MONTH_SOURCE_QUALIFICATION:?Set the published selected 200 m month-source qualification}
geometry=${HICAR_GEOMETRY_REPORT:-$case_root/validation/sleve_geometry_80l.json}
static_file=${HICAR_STATIC_FILE:-$case_root/static/domain_static_swiss_100m.nc}
static_manifest=${HICAR_STATIC_MANIFEST:-$case_root/static/domain_static_swiss_100m.manifest.json}
gate_root=${CAPACITY_GATE_ROOT:-$case_root/capacity_gate/20100101_00_04}

for path in "$python" "$planner" "$config" "$event_assessment" \
            "$event_assessment.ready" "$source_qualification" \
            "$source_qualification.ready" "$geometry" "$geometry.ready" \
            "$static_file" "$static_file.ready" "$static_manifest"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done

"$python" "$planner" \
  --gate-config "$config" \
  --event-assessment "$event_assessment" \
  --source-qualification "$source_qualification" \
  --geometry-report "$geometry" \
  --static-file "$static_file" \
  --static-manifest "$static_manifest" \
  --gate-root "$gate_root"
test -f "$gate_root/capacity_gate_plan.json.ready"
echo "PASS: authorized 100 m gate plan published under $gate_root"
