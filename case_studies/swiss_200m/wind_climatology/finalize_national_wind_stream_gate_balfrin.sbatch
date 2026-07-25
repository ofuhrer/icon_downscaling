#!/bin/bash -l
# Merge the two national wind intervals and publish the restart/archive gate.
#SBATCH --job-name=hicar-wind-final
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=64G
#SBATCH --time=01:00:00
#SBATCH --output=/scratch/mch/olifu/icon_hicar/validation/wind-climatology/national-final-%j.out
#SBATCH --error=/scratch/mch/olifu/icon_hicar/validation/wind-climatology/national-final-%j.err

set -euo pipefail

[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

validation_root=${VALIDATION_ROOT:-${SCRATCH:?}/icon_hicar/validation/wind-climatology}
stream_root="${validation_root}/national-stream"
python=${HICAR_VALIDATION_PYTHON:-${SCRATCH}/icon_hicar/venv_static/bin/python}
merger="${SCRATCH}/icon_hicar/scripts/merge_hicar_wind_statistics.py"
retirer="${SCRATCH}/icon_hicar/case_studies/swiss_200m/streaming/retire_wind_interval_statistics.py"
first_run="${stream_root}/runs/segment-00-01"
second_run="${stream_root}/runs/segment-01-02"
first_id="wind-national-v2-20100101-0000-0100"
second_id="wind-national-v2-20100101-0100-0200"
first_completion="${first_run}/model_chunk_completion.json"
second_completion="${second_run}/model_chunk_completion.json"
first_interval="${first_run}/wind_climatology/wind_statistics_${first_id}.nc"
second_interval="${second_run}/wind_climatology/wind_statistics_${second_id}.nc"
merged_output="${stream_root}/national_wind_statistics_2h.nc"
merged_report="${stream_root}/national_wind_statistics_2h.json"
retirement_report="${stream_root}/interval_retirement_dry_run.json"
manifest="${stream_root}/national_wind_stream_qualification.json"

for path in \
  "${python}" "${merger}" "${retirer}" \
  "${first_completion}" "${first_completion}.ready" \
  "${second_completion}" "${second_completion}.ready" \
  "${first_interval}" "${first_interval}.ready" \
  "${second_interval}" "${second_interval}.ready"; do
  test -e "${path}" || {
    echo "missing required path: ${path}" >&2
    exit 2
  }
done

"${python}" "${merger}" \
  --input "${first_interval}" "${second_interval}" \
  --output "${merged_output}" \
  --group-by all \
  --y-block-size 32 \
  --report "${merged_report}"
"${python}" "${retirer}" \
  --merged-publication "${merged_report}" > "${retirement_report}"

"${python}" - \
  "${first_completion}" "${second_completion}" \
  "${first_run}/input.nml" "${second_run}/input.nml" \
  "${first_interval}" "${second_interval}" \
  "${merged_output}" "${merged_report}" "${retirement_report}" \
  "${manifest}" <<'PY'
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

(
    first_completion_path,
    second_completion_path,
    first_namelist_path,
    second_namelist_path,
    first_interval_path,
    second_interval_path,
    merged_output_path,
    merged_report_path,
    retirement_report_path,
    manifest_path,
) = sys.argv[1:]

def digest(path: str) -> str:
    value = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()

first = json.load(open(first_completion_path))
second = json.load(open(second_completion_path))
merged = json.load(open(merged_report_path))
retirement = json.load(open(retirement_report_path))
failures = []
for label, completion in (("first", first), ("second", second)):
    if completion.get("status") != "PASS":
        failures.append(f"{label} model completion is not PASS")
    if completion.get("output_profile") != "wind_climatology":
        failures.append(f"{label} model profile is not wind_climatology")
    if completion.get("output_interval_seconds") != 1800:
        failures.append(f"{label} model sampling interval is not 1800 s")
if first.get("restart_continuation"):
    failures.append("first segment is unexpectedly a restart continuation")
if not second.get("restart_continuation"):
    failures.append("second segment did not validate as a restart continuation")
if first.get("end") != second.get("start"):
    failures.append("segment boundary timestamps differ")
first_namelist = Path(first_namelist_path).read_text()
second_namelist = Path(second_namelist_path).read_text()
if "restart_run = .True." in first_namelist:
    failures.append("first namelist is unexpectedly a restart run")
if "restart_run = .True." not in second_namelist:
    failures.append("second namelist does not activate restart")
if merged.get("status") != "PASS":
    failures.append("merged product is not PASS")
if merged.get("samples_per_group") != [4]:
    failures.append("merged product does not contain four 30-minute samples")
if not merged.get("surface_pbl_statistics"):
    failures.append("merged product lacks surface/PBL statistics")
if not merged.get("wind_distribution_statistics"):
    failures.append("merged product lacks wind-distribution counts")
if retirement.get("status") != "READY_TO_RETIRE":
    failures.append("interval retirement dry run did not pass")
if retirement.get("execute"):
    failures.append("interval retirement unexpectedly executed")

def artifact(path: str) -> dict:
    resolved = Path(path).resolve()
    return {
        "path": str(resolved),
        "size_bytes": resolved.stat().st_size,
        "sha256": digest(str(resolved)),
    }

payload = {
    "schema_version": 1,
    "status": "PASS" if not failures else "FAIL",
    "created_utc": datetime.now(timezone.utc).isoformat(),
    "source_commit": "2999c9bdf6e0ed50a7f44311e2c8555e26848d31",
    "segments": [
        {
            "completion": first,
            "compact_interval": artifact(first_interval_path),
        },
        {
            "completion": second,
            "compact_interval": artifact(second_interval_path),
        },
    ],
    "merged_product": artifact(merged_output_path),
    "merged_report": merged,
    "interval_retirement_dry_run": retirement,
    "restart_scope": (
        "The second national segment read the first segment's final restart "
        "and passed output/restart validation. A segmented-versus-"
        "uninterrupted trajectory comparison remains a separate scientific gate."
    ),
    "gust_policy": (
        "No ICON VMAX. Resolved maxima are HICAR sample maxima, not "
        "3-second gusts."
    ),
    "failures": failures,
}
manifest = Path(manifest_path)
temporary = manifest.with_name(f".{manifest.name}.tmp-{os.getpid()}")
temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
os.replace(temporary, manifest)
if failures:
    raise SystemExit("\n".join(failures))
Path(f"{manifest}.ready").touch()
print(json.dumps(payload, indent=2, sort_keys=True))
PY

printf 'PASS: national wind stream qualification published at %s\n' "${manifest}"
