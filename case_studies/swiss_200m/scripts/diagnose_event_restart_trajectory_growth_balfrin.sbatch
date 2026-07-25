#!/bin/bash -l
# Diagnose growth of the failed 24-hour event restart-trajectory comparison.
# This publishes no promotion-ready marker and does not weaken the frozen gate.
#SBATCH --job-name=hicar-restart-growth
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --time=00:30:00
#SBATCH --output=/scratch/mch/olifu/icon_hicar/case_studies/swiss_200m/validation/restart_trajectory_growth_%j.out
#SBATCH --error=/scratch/mch/olifu/icon_hicar/case_studies/swiss_200m/validation/restart_trajectory_growth_%j.err

set -euo pipefail

[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module purge || true
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module load netcdf-c/4.8.1-gcc netcdf-fortran/4.5.4-gcc

case_root=${HICAR_SWISS_CASE:-${SCRATCH:?}/icon_hicar/case_studies/swiss_200m}
python=${HICAR_VALIDATION_PYTHON:-${SCRATCH}/icon_hicar/venv_static/bin/python}
comparator=${HICAR_RESTART_COMPARATOR:-${case_root}/streaming/compare_segmented_to_uninterrupted.py}
comparator_sha256=${HICAR_RESTART_COMPARATOR_SHA256:?Set HICAR_RESTART_COMPARATOR_SHA256}
plan=${HICAR_SCIENTIFIC_PLAN:-${case_root}/config/scientific_pilot_plan.json}
reference=${HICAR_RESTART_REFERENCE_COMPLETION:-${case_root}/runs/science_summer_radiation_recovery_20200701_00_20200704_00/model_chunk_completion.json}
segmented=${HICAR_RESTART_SEGMENTED_COMPLETION:-${case_root}/runs/science_summer_restart_overlap_20200703_00_20200704_00/model_chunk_completion.json}
output_dir=${HICAR_RESTART_GROWTH_DIR:-${case_root}/runs/science_summer_restart_overlap_20200703_00_20200704_00/restart_trajectory_growth}

for path in \
  "$python" "$comparator" "$plan" "$reference" "${reference}.ready" \
  "$segmented" "${segmented}.ready"; do
  test -e "$path" || {
    echo "missing diagnostic input: $path" >&2
    exit 2
  }
done
test "$(sha256sum "$comparator" | awk '{print $1}')" = "$comparator_sha256"
test ! -e "$output_dir"
mkdir -p "$output_dir"

diagnostic_plan="$output_dir/scientific_plan_minimum_3h.json"
"$python" - "$plan" "$diagnostic_plan" <<'PY'
import json
from pathlib import Path
import sys

source = Path(sys.argv[1])
target = Path(sys.argv[2])
payload = json.loads(source.read_text(encoding="utf-8"))
criteria = payload["promotion_criteria"]["month_to_annual_cycle"]
criteria["minimum_post_restart_overlap_hours"] = 3
payload["diagnostic_only"] = (
    "Minimum overlap shortened only to localize growth of an already failed "
    "frozen 24-hour gate; this plan cannot promote."
)
tmp = target.with_suffix(".tmp")
tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
tmp.replace(target)
PY

for hours in 3 6 12 24; do
  report="$output_dir/through_${hours}h.json"
  case "$hours" in
    3) end_time="2020-07-03T03:00:00" ;;
    6) end_time="2020-07-03T06:00:00" ;;
    12) end_time="2020-07-03T12:00:00" ;;
    24) end_time="2020-07-04T00:00:00" ;;
  esac
  set +e
  "$python" "$comparator" \
    --segmented-completion "$segmented" \
    --reference-completion "$reference" \
    --scientific-plan "$diagnostic_plan" \
    --start "2020-07-03T00:00:00" \
    --end "$end_time" \
    --report "$report"
  status=$?
  set -e
  test "$status" -eq 0 || test "$status" -eq 1
  test -f "$report"
done

"$python" - "$output_dir" "$plan" "$comparator" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

root = Path(sys.argv[1])
plan = Path(sys.argv[2])
comparator = Path(sys.argv[3])

def sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()

intervals = {}
for hours in (3, 6, 12, 24):
    path = root / f"through_{hours}h.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    intervals[str(hours)] = {
        "status": report["status"],
        "record_count": len(report["expected_times"]),
        "report": str(path),
        "report_sha256": sha256(path),
        "metrics": report["metrics"],
        "failures": report["failures"],
    }
payload = {
    "schema_version": 1,
    "status": "DIAGNOSTIC",
    "promotion_eligible": False,
    "interpretation": (
        "Cumulative windows localize divergence growth after the exact hour-48 "
        "restart. The unchanged frozen 24-hour comparison remains authoritative."
    ),
    "scientific_plan": {"path": str(plan), "sha256": sha256(plan)},
    "comparator": {"path": str(comparator), "sha256": sha256(comparator)},
    "intervals_hours": intervals,
}
target = root / "restart_trajectory_growth_diagnostic.json"
tmp = target.with_suffix(".tmp")
tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
tmp.replace(target)
print(target)
PY

echo "restart trajectory growth diagnostic complete: $output_dir"
