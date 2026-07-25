#!/bin/bash -l
# Independently reproduce the 80-level SLEVE geometry on the national 100 m DEM.
# This CPU-only check is safe to run before the event-pilot promotion decision.
#SBATCH --job-name=hicar-100m-geometry
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --time=00:30:00

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

repo_root=${REPO_ROOT:-$SCRATCH/icon_hicar}
case_root=${HICAR_SWISS_CASE:-$repo_root/case_studies/swiss_100m}
static_file=${HICAR_STATIC_FILE:-$case_root/static/domain_static_swiss_100m.nc}
manifest=${HICAR_STATIC_MANIFEST:-$case_root/static/domain_static_swiss_100m.manifest.json}
report=${HICAR_GEOMETRY_REPORT:-$case_root/validation/sleve_geometry_80l.json}
validator=$repo_root/scripts/validate_sleve_geometry.py
python=${HICAR_VALIDATION_PYTHON:-$repo_root/venv_static/bin/python}

for path in "$static_file" "$static_file.ready" "$manifest" "$validator" "$python"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done

expected_sha=$(
  "$python" - "$manifest" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1]))["static_sha256"])
PY
)
actual_sha=$(sha256sum "$static_file" | awk '{print $1}')
test "$actual_sha" = "$expected_sha" || {
  echo "static checksum differs from the manifest" >&2
  exit 2
}

if test -f "$report.ready"; then
  "$python" - "$report" "$actual_sha" <<'PY'
import json
import sys
payload = json.load(open(sys.argv[1]))
if payload.get("status") != "PASS":
    raise SystemExit("published geometry report is not PASS")
if payload.get("static_sha256") != sys.argv[2]:
    raise SystemExit("published geometry report references another static file")
if payload["minimum_mass_jacobian"]["value"] < 0.1:
    raise SystemExit("published geometry Jacobian is below 0.1")
if payload["minimum_interface_layer_thickness"]["value_m"] < 5.0:
    raise SystemExit("published interface thickness is below 5 m")
PY
  echo "geometry report already published: $report"
  exit 0
fi

mkdir -p "$(dirname "$report")"
"$python" "$validator" "$static_file" \
  --terrain-variable topo \
  --nz 80 \
  --model-top-height 12000 \
  --height-lowest-level 15 \
  --stretch-factor 0.65 \
  --decay-rate-large 2 \
  --decay-rate-small 6 \
  --sleve-exponent 1.35 \
  --smooth-window-radius 5 \
  --smooth-cycles 10 \
  --minimum-mass-jacobian 0.1 \
  --minimum-interface-thickness 5 \
  --minimum-mass-spacing 5 \
  --report "$report"
test -s "$report"
touch "$report.ready"
echo "PASS: national 100 m SLEVE geometry published at $report"
