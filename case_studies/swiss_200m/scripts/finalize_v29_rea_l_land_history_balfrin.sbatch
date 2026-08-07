#!/bin/bash -l
# Verify and publish the source-only V29 REA-L land-state history.
#SBATCH --job-name=rea-l-v29-land-finalize
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=2G
#SBATCH --time=00:10:00

set -euo pipefail

history_dir=${V29_REA_L_LAND_HISTORY_DIR:-$SCRATCH/icon_hicar/mechanism_diagnosis/v2/rea_l_land_history}
report=${V29_REA_L_LAND_HISTORY_REPORT:-$history_dir/rea_l_land_history_publication.json}

python3 - "$history_dir" "$report" <<'PY'
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile

history_dir, report = map(Path, __import__("sys").argv[1:])
records = []
for step in range(0, 25, 3):
    valid = datetime(2020, 7, 1, tzinfo=timezone.utc) + timedelta(hours=step)
    date = valid.strftime("%Y%m%d")
    hour = valid.strftime("%H%M")
    payload = history_dir / f"rea_l_land_state_{date}_{hour}.nc"
    manifest = history_dir / f"rea_l_land_state_{date}_{hour}.manifest.json"
    for path in (payload, manifest):
        if not path.is_file() or not path.stat().st_size:
            raise SystemExit(f"missing or empty published land-history input: {path}")
    for marker in (Path(f"{payload}.ready"), Path(f"{manifest}.ready")):
        if not marker.is_file():
            raise SystemExit(f"missing published land-history ready marker: {marker}")
    item = json.loads(manifest.read_text())
    request = item.get("request", {})
    if item.get("status") != "PASS" or request != {
        "class": "rd", "stream": "reanl", "expver": "r001",
        "model": "icon-rea-l-ch1", "type": "cf", "cycle_date": "20200701",
        "cycle_time": "0000", "step_hours": step,
        "valid_date": date, "valid_time": hour,
        "fields": ["SKT", "T_SO", "W_SO", "W_SNOW", "RHO_SNOW"],
    }:
        raise SystemExit(f"invalid source contract in {manifest}")
    digest = hashlib.sha256(payload.read_bytes()).hexdigest()
    if digest != item.get("output_sha256"):
        raise SystemExit(f"payload hash mismatch: {payload}")
    records.append({"valid_time": valid.isoformat().replace("+00:00", ""), "step_hours": step,
                    "payload": str(payload), "sha256": digest})

publication = {"schema_version": 1, "status": "PASS", "source": "ICON REA-L-CH1 FDB",
               "purpose": "V29 causal diagnosis: time-matched REA-L soil and snow states",
               "cycle": "2020-07-01T00:00:00", "records": records}
report.parent.mkdir(parents=True, exist_ok=True)
with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=report.parent,
                                 prefix=f".{report.name}.", delete=False) as stream:
    json.dump(publication, stream, indent=2, sort_keys=True)
    stream.write("\n")
    temporary = Path(stream.name)
os.replace(temporary, report)
Path(f"{report}.ready").touch()
print(f"PASS: published {report}")
PY
