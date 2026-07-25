#!/bin/bash -l
# Revalidate and publish the continuous 00--06 UTC REA-L forcing series.
#SBATCH --job-name=rea-l-series-6h
#SBATCH --partition=pp-short
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=00:30:00

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

repo=${ICON_HICAR_ROOT:-$SCRATCH/icon_hicar}
source_case="$repo/case_studies/swiss_100m"
target_case="$repo/case_studies/swiss_200m"
forcing_root="$source_case/forcing/rea_l_ch1"
source_root="$source_case/source/rea_l_ch1"
static="$source_case/static/domain_static_swiss_100m.nc"
validator="$source_case/validation/validate_forcing.py"
writer="$target_case/scripts/write_forcing_list.py"
python=${HICAR_VALIDATION_PYTHON:-$repo/venv_static/bin/python}
publication="$target_case/forcing/rea_l_ch1"
list="$publication/forcing_20100101_0000_0600.txt"
manifest="$publication/forcing_20100101_0000_0600.manifest.json"
manifest_partial="$publication/.forcing_20100101_0000_0600.manifest.json.partial.${SLURM_JOB_ID:?}"

for path in "$python" "$validator" "$writer" "$static" "$static.ready"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done
mkdir -p "$publication"
for path in "$list" "$list.ready" "$manifest" "$manifest.ready"; do
  test ! -e "$path" || {
    echo "refusing to overwrite published series artifact: $path" >&2
    exit 2
  }
done
rm -f "$manifest_partial"
trap 'rm -f "$manifest_partial"' EXIT

forcing_files=()
source_manifests=()
validation_reports=()
for step in 0 1 2 3 4 5 6; do
  if (( step == 0 )); then
    tag=20100101_0000
  else
    tag=$(printf '20100101_0000_s%02d' "$step")
  fi
  forcing="$forcing_root/$tag/rea_l_hicar_${tag}.nc"
  source_manifest="$source_root/$tag/source_manifest.json"
  report="$forcing_root/$tag/rea_l_hicar_${tag}.validation.json"
  for path in "$forcing" "$forcing.ready" "$source_manifest"; do
    test -e "$path" || { echo "missing forcing publication input: $path" >&2; exit 2; }
  done
  expected=$(printf '2010-01-01T%02d:00:00' "$step")
  "$python" "$validator" \
    --forcing-file "$forcing" \
    --static-file "$static" \
    --expected-valid-time "$expected" \
    --report "$report"
  grep -q '"status": "PASS"' "$report"
  forcing_files+=("$forcing")
  source_manifests+=("$source_manifest")
  validation_reports+=("$report")
done

"$python" "$writer" \
  --output "$list" \
  --expected-start 2010-01-01T00:00:00 \
  --expected-end 2010-01-01T06:00:00 \
  --expected-interval-seconds 3600 \
  "${forcing_files[@]}"
test -s "$list"
test -f "$list.ready"

"$python" - "$manifest_partial" "$list" \
  "${forcing_files[@]}" -- "${source_manifests[@]}" -- "${validation_reports[@]}" <<'PY'
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sys

output = Path(sys.argv[1])
forcing_list = Path(sys.argv[2])
parts = sys.argv[3:]
first = parts.index("--")
second = parts.index("--", first + 1)
forcing = [Path(value) for value in parts[:first]]
source = [Path(value) for value in parts[first + 1:second]]
reports = [Path(value) for value in parts[second + 1:]]
if not (len(forcing) == len(source) == len(reports) == 7):
    raise SystemExit("series manifest requires seven forcing/source/report triplets")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


entries = []
origin = datetime(2010, 1, 1)
for index, (forcing_path, source_path, report_path) in enumerate(
    zip(forcing, source, reports)
):
    report = json.loads(report_path.read_text())
    expected = origin + timedelta(hours=index)
    if report.get("status") != "PASS" or report.get("valid_time") != expected.isoformat():
        raise SystemExit(f"invalid forcing report: {report_path}")
    entries.append(
        {
            "valid_time": expected.isoformat(),
            "forcing_file": str(forcing_path),
            "forcing_size_bytes": forcing_path.stat().st_size,
            "forcing_sha256": sha256(forcing_path),
            "source_manifest": str(source_path),
            "source_manifest_sha256": sha256(source_path),
            "validation_report": str(report_path),
            "validation_report_sha256": sha256(report_path),
        }
    )
record = {
    "status": "PASS",
    "source": "ICON REA-L-CH1 FDB",
    "cycle": "2010-01-01T00:00:00",
    "start": entries[0]["valid_time"],
    "end": entries[-1]["valid_time"],
    "interval_seconds": 3600,
    "records": len(entries),
    "forcing_list": str(forcing_list),
    "forcing_list_sha256": sha256(forcing_list),
    "entries": entries,
}
output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
PY

test -s "$manifest_partial"
grep -q '"status": "PASS"' "$manifest_partial"
mv "$manifest_partial" "$manifest"
touch "$manifest.ready"
trap - EXIT
printf 'REA-L six-hour series published: list=%s manifest=%s\n' "$list" "$manifest"
