#!/bin/bash -l
# Produce one planned hourly HICAR forcing record without retaining native
# REA-L GRIB. Submit as an array over the indices in STREAM_PLAN.
#SBATCH --job-name=rea-l-stream-record
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=8G
#SBATCH --time=01:00:00

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

plan=${STREAM_PLAN:?Set STREAM_PLAN to a published chunk_plan.json}
index=${STREAM_INDEX:-${SLURM_ARRAY_TASK_ID:-}}
[[ "$index" =~ ^[0-9]+$ ]] || { echo "stream record index is required" >&2; exit 2; }
repo_root=${REPO_ROOT:-$SCRATCH/icon_hicar}
site_config=${HICAR_SITE_CONFIG:-$repo_root/config/balfrin.env}
if test -f "$site_config"; then
  while IFS='=' read -r key value; do
    case "$key" in
      ''|\#*) continue ;;
      USER_ENV_ROOT|REA_FDB_IMAGE|FIELD_EXTRA_BIN|FIELD_EXTRA_RESOURCES|FIELD_EXTRA_SAMPLE|ICON_GRID|ICON_DOWNSCALING_DURABLE_ROOT|HICAR_PRODUCTION_BRANCH|HICAR_PRODUCTION_COMMIT|HICAR_PRIMARY_WORKFLOW)
        if test -z "${!key+x}"; then
          printf -v "$key" '%s' "$value"
          export "$key"
        fi
        ;;
      *) echo "unsupported Balfrin site-config key: $key" >&2; exit 2 ;;
    esac
  done < "$site_config"
fi
source_case=${HICAR_FORCING_CASE:-$repo_root/case_studies/swiss_100m}
python=${HICAR_VALIDATION_PYTHON:-$repo_root/venv_static/bin/python}
uenv_bin=${UENV_BIN:-/usr/bin/uenv}
image=${REA_FDB_IMAGE:-fdb/5.19:v2}
validator=${HICAR_FORCING_VALIDATOR:-$repo_root/case_studies/swiss_200m/validation/validate_forcing.py}
case_name=$(basename "$source_case")
static_domain=${HICAR_STATIC_DOMAIN:-$source_case/static/domain_static_${case_name}.nc}
grid_file=${HICAR_FIELD_EXTRA_GRID:-$repo_root/case_studies/swiss_200m/config/fieldextra_target_grid.txt}
converter="$repo_root/scripts/prepare_icon_inputs.sh"

for path in "$plan" "$plan.ready" "$python" "$uenv_bin" "$validator" \
            "$static_domain" "$static_domain.ready" "$grid_file" "$converter"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done

IFS=$'\t' read -r chunk_id chunk_root cycle_date cycle_time step valid_time final < <(
  "$python" - "$plan" "$index" <<'PY'
import json
import pathlib
import sys

plan = json.load(open(sys.argv[1]))
index = int(sys.argv[2])
records = plan["records"]
if index < 0 or index >= len(records):
    raise SystemExit(f"stream index {index} is outside 0..{len(records)-1}")
record = records[index]
print(
    plan["chunk_id"],
    plan["chunk_root"],
    record["cycle_date"],
    record["cycle_time"],
    record["step_hours"],
    record["valid_time"],
    record["forcing_file"],
    sep="\t",
)
PY
)
final=$(realpath -m "$final")
final_dir=$(dirname "$final")
base=${final%.nc}
ready="$final.ready"
report="$base.validation.json"
manifest="$base.manifest.json"
mkdir -p "$final_dir" "$chunk_root/work" "$chunk_root/cache"

verify_stream_publication() {
  "$python" - "$final" "$report" "$manifest" "$valid_time" <<'PY'
import hashlib
import json
import sys

forcing, report_path, manifest_path, expected_time = sys.argv[1:]
report = json.load(open(report_path))
manifest = json.load(open(manifest_path))
digest = hashlib.sha256(open(forcing, "rb").read()).hexdigest()
if report.get("status") != "PASS" or manifest.get("status") != "PASS":
    raise SystemExit("existing stream publication is not PASS")
if manifest.get("valid_time") != expected_time:
    raise SystemExit("existing stream publication has the wrong valid time")
if manifest.get("forcing_sha256") != digest:
    raise SystemExit("existing stream publication hash mismatch")
PY
}

if test -f "$ready"; then
  test -s "$final" && test -s "$report" && test -s "$manifest" || {
    echo "ready marker exists for an incomplete stream publication: $ready" >&2
    exit 2
  }
  verify_stream_publication
  echo "stream record already published and verified: $final"
  exit 0
fi
if test -s "$final" && test -s "$report" && test -s "$manifest"; then
  verify_stream_publication
  touch "$ready"
  echo "recovered stream publication marker: $final"
  exit 0
fi
if test -e "$final" || test -e "$report" || test -e "$manifest"; then
  recovery="$chunk_root/recovery/record_${index}_$(date -u +%Y%m%dT%H%M%S)_${SLURM_JOB_ID:-$$}"
  mkdir -p "$recovery"
  for path in "$final" "$report" "$manifest"; do
    if test -e "$path"; then
      mv "$path" "$recovery/"
    fi
  done
  echo "quarantined incomplete stream publication under $recovery"
fi

work=$(mktemp -d "$chunk_root/work/record_${index}.XXXXXX")
partial=""
report_partial=""
manifest_partial=""
cleanup() {
  rm -rf "$work"
  for path in "$partial" "$report_partial" "$manifest_partial"; do
    if test -n "$path"; then
      rm -f "$path" "$path.ready"
    fi
  done
}
trap cleanup EXIT
start_epoch=$(date +%s)

static_dir="$chunk_root/cache/$cycle_date"
static="$static_dir/rea_l_static_${cycle_date}_${cycle_time}.grib"
static_ready="$static.ready"
static_manifest="$static_dir/static_manifest.json"
lock="$static_dir/.produce.lock"
mkdir -p "$static_dir"
static_lock_stale_seconds=${STREAM_STATIC_LOCK_STALE_SECONDS:-3700}
case "$static_lock_stale_seconds" in
  ''|*[!0-9]*) echo "STREAM_STATIC_LOCK_STALE_SECONDS must be an integer" >&2; exit 2 ;;
esac
test "$static_lock_stale_seconds" -ge 3700 || {
  echo "STREAM_STATIC_LOCK_STALE_SECONDS must be at least 3700" >&2
  exit 2
}

publish_static() {
  local partial="$static.partial.${SLURM_JOB_ID}"
  cat > "$work/hhl.mars" <<EOF
retrieve,
  class=rd,
  stream=reanl,
  expver=r001,
  model=icon-rea-l-ch1,
  type=cf,
  date=$cycle_date,
  time=$cycle_time,
  levtype=ml,
  levelist=1/to/81,
  param=500008,
  step=0
EOF
  cat > "$work/surface.mars" <<EOF
retrieve,
  class=rd,
  stream=reanl,
  expver=r001,
  model=icon-rea-l-ch1,
  type=cf,
  date=$cycle_date,
  time=$cycle_time,
  levtype=sfc,
  param=500007/500054,
  step=0
EOF
  "$uenv_bin" run --view=rea-l-ch1 "$image" -- \
    fdb-read --statistics "$work/hhl.mars" "$work/hhl.grib"
  "$uenv_bin" run --view=rea-l-ch1 "$image" -- \
    fdb-read --statistics "$work/surface.mars" "$work/surface.grib"
  cat "$work/hhl.grib" "$work/surface.grib" > "$partial"
  count=$("$uenv_bin" run --view=rea-l-ch1 "$image" -- grib_count "$partial" | tail -n 1)
  test "$count" = 83
  static_sha=$(sha256sum "$partial" | awk '{print $1}')
  static_bytes=$(stat -c %s "$partial")
  "$python" - "$static_manifest.partial.${SLURM_JOB_ID}" <<PY
import json
import pathlib
path = pathlib.Path("$static_manifest.partial.${SLURM_JOB_ID}")
path.write_text(json.dumps({
    "status": "PASS",
    "source": "ICON REA-L-CH1 FDB",
    "cycle_date": "$cycle_date",
    "cycle_time": "$cycle_time",
    "fdb_image": "$image",
    "messages": 83,
    "size_bytes": int("$static_bytes"),
    "sha256": "$static_sha",
}, indent=2, sort_keys=True) + "\\n")
PY
  mv "$partial" "$static"
  mv "$static_manifest.partial.${SLURM_JOB_ID}" "$static_manifest"
  touch "$static_ready"
}

acquire_static_lock() {
  local attempt lock_epoch now stale
  for attempt in 1 2; do
    if mkdir "$lock" 2>/dev/null; then
      "$python" - "$lock/owner.json" <<PY
import json
import os
import pathlib
import socket
from datetime import datetime, timezone

path = pathlib.Path("$lock/owner.json")
path.write_text(json.dumps({
    "created_at": datetime.now(timezone.utc).isoformat(),
    "hostname": socket.gethostname(),
    "pid": os.getpid(),
    "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
}, indent=2, sort_keys=True) + "\\n")
PY
      return 0
    fi
    lock_epoch=$(stat -c %Y "$lock" 2>/dev/null || echo 0)
    now=$(date +%s)
    if test "$lock_epoch" -gt 0 &&
       test $((now - lock_epoch)) -gt "$static_lock_stale_seconds"; then
      stale="$static_dir/.produce.lock.stale.${SLURM_JOB_ID:-$$}.${now}"
      mv "$lock" "$stale" 2>/dev/null || true
    fi
  done
  return 1
}

if ! test -s "$static" || ! test -f "$static_ready" || ! test -s "$static_manifest"; then
  if acquire_static_lock; then
    trap 'rm -f "$lock/owner.json"; rmdir "$lock" 2>/dev/null || true; cleanup' EXIT
    publish_static
    rm -f "$lock/owner.json"
    rmdir "$lock"
    trap cleanup EXIT
  else
    for _ in $(seq 1 300); do
      test -s "$static" && test -f "$static_ready" && test -s "$static_manifest" && break
      sleep 2
    done
  fi
fi
for path in "$static" "$static_ready" "$static_manifest"; do
  test -e "$path" || { echo "cycle-static publication timed out: $path" >&2; exit 1; }
done
static_epoch=$(date +%s)

cat > "$work/full.mars" <<EOF
retrieve,
  class=rd,
  stream=reanl,
  expver=r001,
  model=icon-rea-l-ch1,
  type=cf,
  date=$cycle_date,
  time=$cycle_time,
  levtype=ml,
  levelist=1/to/80,
  param=500001/500014/500028/500030/500035,
  step=$step
EOF
cat > "$work/w.mars" <<EOF
retrieve,
  class=rd,
  stream=reanl,
  expver=r001,
  model=icon-rea-l-ch1,
  type=cf,
  date=$cycle_date,
  time=$cycle_time,
  levtype=ml,
  levelist=1/to/81,
  param=500032,
  step=$step
EOF
"$uenv_bin" run --view=rea-l-ch1 "$image" -- \
  fdb-read --statistics "$work/full.mars" "$work/full.grib"
"$uenv_bin" run --view=rea-l-ch1 "$image" -- \
  fdb-read --statistics "$work/w.mars" "$work/w.grib"
cat "$work/full.grib" "$work/w.grib" > "$work/dynamic.grib"
dynamic_count=$("$uenv_bin" run --view=rea-l-ch1 "$image" -- \
  grib_count "$work/dynamic.grib" | tail -n 1)
test "$dynamic_count" = 481
dynamic_sha=$(sha256sum "$work/dynamic.grib" | awk '{print $1}')
dynamic_bytes=$(stat -c %s "$work/dynamic.grib")
extract_epoch=$(date +%s)

target_grid=$(<"$grid_file")
partial="$final.partial.${SLURM_JOB_ID}"
report_partial="$report.partial.${SLURM_JOB_ID}"
manifest_partial="$manifest.partial.${SLURM_JOB_ID}"
"$converter" \
  --input "$work/dynamic.grib" \
  --static "$static" \
  --output "$partial" \
  --target-grid "$target_grid" \
  --model-name rea-l-ch1 \
  --expver 639 \
  --load-balfrin-modules \
  --compression 1 \
  --work-dir "$work/converter"
test -s "$partial"
test -f "$partial.ready"
rm "$partial.ready"
convert_epoch=$(date +%s)

"$python" "$validator" \
  --forcing-file "$partial" \
  --published-forcing-file "$final" \
  --static-file "$static_domain" \
  --expected-valid-time "$valid_time" \
  --report "$report_partial"
grep -q '"status": "PASS"' "$report_partial"
forcing_sha=$(sha256sum "$partial" | awk '{print $1}')
forcing_bytes=$(stat -c %s "$partial")
static_sha=$(sha256sum "$static" | awk '{print $1}')
end_epoch=$(date +%s)

"$python" - "$manifest_partial" <<PY
import json
import pathlib
path = pathlib.Path("$manifest_partial")
path.write_text(json.dumps({
    "status": "PASS",
    "chunk_id": "$chunk_id",
    "record_index": int("$index"),
    "source": "ICON REA-L-CH1 FDB",
    "cycle_date": "$cycle_date",
    "cycle_time": "$cycle_time",
    "step_hours": int("$step"),
    "valid_time": "$valid_time",
    "fdb_image": "$image",
    "target_grid": "$target_grid",
    "source_dynamic": {
        "retained": False,
        "messages": 481,
        "size_bytes": int("$dynamic_bytes"),
        "sha256": "$dynamic_sha",
    },
    "source_static": {
        "cache_file": "$static",
        "messages": 83,
        "sha256": "$static_sha",
    },
    "forcing_file": "$final",
    "forcing_size_bytes": int("$forcing_bytes"),
    "forcing_sha256": "$forcing_sha",
    "validation_report": "$report",
    "stage_seconds": {
        "static_wait_or_publish": int("$static_epoch") - int("$start_epoch"),
        "dynamic_extract": int("$extract_epoch") - int("$static_epoch"),
        "convert": int("$convert_epoch") - int("$extract_epoch"),
        "validate_and_publish": int("$end_epoch") - int("$convert_epoch"),
        "total": int("$end_epoch") - int("$start_epoch"),
    },
}, indent=2, sort_keys=True) + "\\n")
PY

mv "$partial" "$final"
mv "$report_partial" "$report"
mv "$manifest_partial" "$manifest"
touch "$ready"
trap - EXIT
rm -rf "$work"
echo "stream record published: index=$index valid=$valid_time forcing=$final"
