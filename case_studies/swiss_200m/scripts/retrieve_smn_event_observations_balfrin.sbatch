#!/bin/bash -l
# Retrieve quality-controlled SwissMetNet observations for one event via DWH.
#SBATCH --job-name=smn-event-observations
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=00:30:00

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

plan=${STREAM_PLAN:?Set STREAM_PLAN to a published chunk_plan.json}
repo_root=${REPO_ROOT:-$SCRATCH/icon_hicar}
python=${HICAR_VALIDATION_PYTHON:-$repo_root/venv_static/bin/python}
jretrieve=${JRETRIEVE_BIN:-/oprusers/osm/opr.inn/bin/jretrievedwh}
export OPR_HOME=${JRETRIEVE_CONFIG_HOME:-/oprusers/osm}
for path in "$plan" "$plan.ready" "$python" "$jretrieve"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done
test -r "$OPR_HOME/.jretrievedwh-conf.prod.py" || {
  echo "JRetrieve PROD configuration is not readable under OPR_HOME=$OPR_HOME" >&2
  exit 2
}

IFS=$'\t' read -r chunk_root start end start_compact end_compact < <(
  "$python" - "$plan" <<'PY'
import json
import sys
from datetime import datetime
p = json.load(open(sys.argv[1]))
print(
    p["chunk_root"],
    p["start"],
    p["end"],
    datetime.fromisoformat(p["start"]).strftime("%Y%m%d%H%M"),
    datetime.fromisoformat(p["end"]).strftime("%Y%m%d%H%M"),
    sep="\t",
)
PY
)
output_dir="$chunk_root/observations"
output="$output_dir/swissmetnet_hourly.csv"
manifest="$output_dir/swissmetnet_hourly.manifest.json"
mkdir -p "$output_dir"

if test -s "$output" && test -f "$output.ready" && test -s "$manifest" \
  && test -f "$manifest.ready"; then
  "$python" - "$output" "$manifest" "$start" "$end" <<'PY'
import hashlib
import json
import sys
data, manifest_path, start, end = sys.argv[1:]
manifest = json.load(open(manifest_path))
digest = hashlib.sha256(open(data, "rb").read()).hexdigest()
if manifest.get("status") != "PASS":
    raise SystemExit("existing observation manifest is not PASS")
if manifest.get("start") != start or manifest.get("end") != end:
    raise SystemExit("existing observation interval mismatch")
if manifest.get("output_sha256") != digest:
    raise SystemExit("existing observation checksum mismatch")
PY
  echo "SwissMetNet event observations already published and verified: $output"
  exit 0
fi
for path in "$output" "$output.ready" "$manifest" "$manifest.ready"; do
  test ! -e "$path" || {
    echo "incomplete observation publication requires review: $path" >&2
    exit 2
  }
done

partial="$output.partial.${SLURM_JOB_ID:?}"
manifest_partial="$manifest.partial.${SLURM_JOB_ID:?}"
trap 'rm -f "$partial" "$manifest_partial"' EXIT
parameters=(
  tre200h0 tre200hn tre200hx
  ure200h0 prestah0 rre150h0
  fkl010h0 dkl010h0 gre000h0 htoauths
)
parameter_csv=$(IFS=,; echo "${parameters[*]}")

"$jretrieve" \
  --stage prod \
  --timeout 1200 \
  --seq-type surface \
  --groups SMN \
  --time "$start_compact,$end_compact,60" \
  --coordinates "45.7,47.9,5.8,10.7" \
  --parameter-short-name "$parameter_csv" \
  --info "lat,lon,elev,nat_abbr" \
  --quality-info 15 \
  --data-quality-cat-nr-limit 4 \
  --use-limitation 50 \
  --format csv \
  --delimiter CSV \
  --show-records \
  --output "$partial"
test -s "$partial"

"$python" - "$partial" "$manifest_partial" "$output" "$start" "$end" \
  "$parameter_csv" <<'PY'
import csv
import hashlib
import json
from pathlib import Path
import sys

source, manifest_path, published, start, end, parameters = sys.argv[1:]
path = Path(source)
sample = path.read_text(encoding="utf-8", errors="replace")[:32768]
dialect = csv.Sniffer().sniff(sample, delimiters=";,|\t,")
with path.open(encoding="utf-8", errors="replace", newline="") as stream:
    reader = csv.reader(stream, dialect)
    header = next(reader)
    rows = [row for row in reader if row and not row[0].startswith("#")]
if not rows:
    raise SystemExit("JRetrieve returned no observation records")
lower_header = [name.strip().lower() for name in header]
if not any(
    "time" in name or "date" in name or name == "termin"
    for name in lower_header
):
    raise SystemExit(f"observation output has no recognizable time column: {header}")
station_id_name = next(
    (name for name in ("station", "meas_site") if name in lower_header),
    None,
)
station_ids = sorted(
    {
        row[lower_header.index(station_id_name)]
        for row in rows
        if station_id_name is not None
        and lower_header.index(station_id_name) < len(row)
        and row[lower_header.index(station_id_name)]
    }
)
station_abbreviations = sorted(
    {
        row[lower_header.index("nat_abbr")]
        for row in rows
        if "nat_abbr" in lower_header
        and lower_header.index("nat_abbr") < len(row)
        and row[lower_header.index("nat_abbr")]
    }
)
digest = hashlib.sha256(path.read_bytes()).hexdigest()
payload = {
    "schema_version": 2,
    "status": "PASS",
    "source": "MeteoSwiss DWH via JRetrieve PROD",
    "source_documentation": [
        "https://meteoswiss.atlassian.net/wiki/spaces/NO/pages/1906212/jretrievedwh",
        "https://data.geo.admin.ch/api/stac/v1/collections/ch.meteoschweiz.ogd-smn"
    ],
    "start": start,
    "end": end,
    "interval_minutes": 60,
    "geographic_bounds": [45.7, 47.9, 5.8, 10.7],
    "station_group": "SMN",
    "parameters": parameters.split(","),
    "quality_policy": {
        "quality_info_mask": 15,
        "minimum_data_quality_category": 4,
        "maximum_use_limitation": 50
    },
    "header": header,
    "record_count": len(rows),
    "station_count_from_output": len(station_ids),
    "station_ids_from_output": station_ids,
    "station_abbreviations_from_output": station_abbreviations,
    "output": str(Path(published).resolve()),
    "output_size_bytes": path.stat().st_size,
    "output_sha256": digest
}
Path(manifest_path).write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n",
    encoding="utf-8"
)
PY
mv "$partial" "$output"
mv "$manifest_partial" "$manifest"
touch "$output.ready"
touch "$manifest.ready"
echo "PASS: published SwissMetNet observations for $start through $end"
