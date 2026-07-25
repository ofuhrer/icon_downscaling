#!/bin/bash
# Publish two one-hour forcing plans for the national wind/restart gate.

set -euo pipefail

validation_root=${VALIDATION_ROOT:-${SCRATCH:?}/icon_hicar/validation/wind-climatology}
case_root=${HICAR_SWISS_CASE:-${SCRATCH}/icon_hicar/case_studies/swiss_200m}
python=${HICAR_VALIDATION_PYTHON:-${SCRATCH}/icon_hicar/venv_static/bin/python}
source_manifest="${case_root}/forcing/rea_l_ch1/forcing_20100101_0000_0600.manifest.json"
stream_root="${validation_root}/national-stream"

for path in "${python}" "${source_manifest}" "${source_manifest}.ready"; do
  test -e "${path}" || {
    echo "missing required path: ${path}" >&2
    exit 2
  }
done

prepare_segment() {
  local chunk_id=$1
  local start=$2
  local end=$3
  local chunk_root="${stream_root}/${chunk_id}"

  "${python}" - \
    "${source_manifest}" "${chunk_root}" "${chunk_id}" "${start}" "${end}" <<'PY'
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile

manifest_path = Path(sys.argv[1]).resolve()
chunk_root = Path(sys.argv[2]).resolve()
chunk_id, start_text, end_text = sys.argv[3:]
start = datetime.fromisoformat(start_text)
end = datetime.fromisoformat(end_text)
source = json.loads(manifest_path.read_text())
if source.get("status") != "PASS":
    raise SystemExit("canonical forcing manifest is not PASS")

entries = [
    entry
    for entry in source["entries"]
    if start <= datetime.fromisoformat(entry["valid_time"]) <= end
]
expected = int((end - start).total_seconds() // 3600) + 1
if len(entries) != expected:
    raise SystemExit(f"expected {expected} forcing records, found {len(entries)}")
for entry in entries:
    forcing = Path(entry["forcing_file"])
    if not forcing.is_file() or not Path(f"{forcing}.ready").is_file():
        raise SystemExit(f"forcing publication is incomplete: {forcing}")

def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()

def publish(path: Path, content: str) -> None:
    ready = Path(f"{path}.ready")
    if path.exists() or ready.exists():
        if path.is_file() and ready.is_file() and path.read_text() == content:
            return
        raise SystemExit(f"refusing to replace non-identical publication: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as stream:
        stream.write(content)
        temporary = Path(stream.name)
    os.replace(temporary, path)
    ready.touch()

forcing_list_path = chunk_root / "forcing_list.txt"
forcing_list = "".join(f'"{entry["forcing_file"]}"\n' for entry in entries)
publish(forcing_list_path, forcing_list)

records = []
for index, entry in enumerate(entries):
    valid = datetime.fromisoformat(entry["valid_time"])
    forcing = Path(entry["forcing_file"]).resolve()
    records.append(
        {
            "index": index,
            "valid_time": entry["valid_time"],
            "cycle_date": valid.strftime("%Y%m%d"),
            "cycle_time": "0000",
            "step_hours": valid.hour,
            "forcing_file": str(forcing),
            "ready_marker": f"{forcing}.ready",
        }
    )
plan_path = chunk_root / "chunk_plan.json"
plan = {
    "schema_version": 1,
    "status": "PLANNED",
    "chunk_id": chunk_id,
    "start": start.isoformat(),
    "end": end.isoformat(),
    "hours": int((end - start).total_seconds() // 3600),
    "record_count": len(records),
    "producer_concurrency": 1,
    "cycle_policy": source.get("source", "canonical qualified forcing list"),
    "transient_policy": "No forcing retirement in the wind qualification gate.",
    "chunk_root": str(chunk_root),
    "forcing_list": str(forcing_list_path),
    "records": records,
}
publish(plan_path, json.dumps(plan, indent=2, sort_keys=True) + "\n")

publication_path = chunk_root / "forcing_publication.json"
publication = {
    "status": "PASS",
    "chunk_id": chunk_id,
    "start": start.isoformat(),
    "end": end.isoformat(),
    "hours": plan["hours"],
    "records": len(records),
    "expected_records": len(records),
    "source_manifest": str(manifest_path),
    "source_manifest_sha256": digest(manifest_path),
    "forcing_list": str(forcing_list_path),
    "forcing_list_sha256": digest(forcing_list_path),
    "entries": entries,
    "failures": [],
}
publish(
    publication_path,
    json.dumps(publication, indent=2, sort_keys=True) + "\n",
)
print(f"PASS: {chunk_id} published with {len(records)} forcing records")
PY
}

prepare_segment \
  "wind-national-v2-20100101-0000-0100" \
  "2010-01-01T00:00:00" \
  "2010-01-01T01:00:00"
prepare_segment \
  "wind-national-v2-20100101-0100-0200" \
  "2010-01-01T01:00:00" \
  "2010-01-01T02:00:00"

printf 'PASS: national wind stream plans published under %s\n' "${stream_root}"
