#!/usr/bin/env bash
#SBATCH --job-name=static-abc-build
#SBATCH --partition=pp-long
#SBATCH --cpus-per-task=8
#SBATCH --time=04:00:00
#SBATCH --mem=14G
#SBATCH --output=/scratch/mch/olifu/icon_hicar/qualification/static_abc_v1/logs/%x_%j.out
#SBATCH --error=/scratch/mch/olifu/icon_hicar/qualification/static_abc_v1/logs/%x_%j.err

set -euo pipefail
export OMP_NUM_THREADS=1
export PYTHONUNBUFFERED=1

qual_root=${STATIC_QUAL_ROOT:-/scratch/mch/olifu/icon_hicar/qualification/static_abc_v1}
runtime_root=${STATIC_RUNTIME_ROOT:?set STATIC_RUNTIME_ROOT to the immutable static runtime}
runtime_id=$(basename "$runtime_root")
environment=${STATIC_PYTHON_ROOT:-$qual_root/python-${runtime_id}}
python=${environment}/bin/python
environment_manifest=${environment}.environment.json
baseline=${STATIC_BASELINE:-/scratch/mch/olifu/icon_hicar/case_studies/swiss_200m/static/domain_static_swiss_200m.nc}
cache=${STATIC_SOURCE_CACHE:-/scratch/mch/olifu/icon_hicar/cache/hicar_static_public}
candidate=${STATIC_CANDIDATE:-$qual_root/domain_static_swiss_200m_modal_wc_soil4_v1.nc}
audit=${STATIC_AUDIT:-$qual_root/static_abc_audit_v2.json}
runtime_manifest=${runtime_root}/runtime_manifest.json

test "${SLURM_JOB_PARTITION:-}" = pp-long || {
  echo "static A/B/C build must run in pp-long" >&2
  exit 2
}
for path in "$runtime_manifest" "$runtime_manifest.ready" "$environment_manifest" \
  "$environment_manifest.ready" "$baseline" "$baseline.ready"; do
  test -f "$path" || { echo "missing prerequisite: $path" >&2; exit 2; }
done
test ! -w "$environment/pyvenv.cfg" || {
  echo "static Python environment is writable" >&2
  exit 2
}
. "$runtime_root/scripts/load_balfrin_site_config.sh"
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"
module load python/3.11.7 >/dev/null
"$python" -c 'import netCDF4,numpy,scipy,pyproj,rasterio'
"$python" - "$environment_manifest" "$runtime_manifest" <<'PY'
import hashlib, json, sys
environment = json.load(open(sys.argv[1]))
runtime_bytes = open(sys.argv[2], "rb").read()
if environment.get("runtime_manifest_sha256") != hashlib.sha256(runtime_bytes).hexdigest():
    raise SystemExit("static Python environment is not bound to this runtime manifest")
PY
mkdir -p "$qual_root/logs" "$cache"

runtime_sha=$(sha256sum "$runtime_manifest" | awk '{print $1}')
coordinator_commit=$("$python" -c 'import json,sys; print(json.load(open(sys.argv[1]))["source_commit"])' "$runtime_manifest")

if test -e "$candidate" && ! test -f "$candidate.ready"; then
  echo "candidate exists without a ready marker; refusing to overwrite: $candidate" >&2
  exit 2
fi
if ! test -f "$candidate.ready"; then
  "$python" "$runtime_root/scripts/prepare_static_inputs.py" \
    --output "$candidate" \
    --center-lat 46.815 --center-lon 8.225 \
    --width-km 454 --height-km 330 --dx-m 200 \
    --public-sources --static-field-set land-surface --cache-dir "$cache" \
    --preserve-topography-from "$baseline" --lu-categories USGS \
    --generating-commit "$coordinator_commit" \
    --runtime-manifest-sha256 "$runtime_sha"
  candidate_sha=$(sha256sum "$candidate" | awk '{print $1}')
  printf 'sha256 %s  %s\n' "$candidate_sha" "$(basename "$candidate")" > "${candidate}.ready.tmp.${SLURM_JOB_ID}"
  mv "${candidate}.ready.tmp.${SLURM_JOB_ID}" "$candidate.ready"
fi

"$python" "$runtime_root/case_studies/swiss_200m/validation/validate_domain_plan.py" \
  --static-file "$candidate"
if test -e "$audit" || test -e "$audit.ready"; then
  echo "refusing to overwrite existing audit publication: $audit" >&2
  exit 2
fi
"$python" "$runtime_root/case_studies/swiss_200m/fixed_parameters/audit_static_candidate.py" \
  --baseline "$baseline" --candidate "$candidate" --source-cache "$cache" \
  --topography-tolerance-m 0 --output "$audit"
"$python" - "$audit" <<'PY'
import json, sys
report = json.load(open(sys.argv[1]))
expected = "STATIC_AUDIT_PASS_PAIRED_COUPLED_CASES_REQUIRED"
if report.get("decision") != expected:
    raise SystemExit(f"static audit did not pass: {report.get('decision')}")
if not report["invariant"]["B_and_C_share_identical_static_bytes"]:
    raise SystemExit("B/C shared-static invariant failed")
print(report["decision"])
PY
