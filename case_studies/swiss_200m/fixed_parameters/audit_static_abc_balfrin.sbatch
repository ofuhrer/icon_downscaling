#!/usr/bin/env bash
#SBATCH --job-name=static-abc-audit
#SBATCH --partition=pp-short
#SBATCH --cpus-per-task=8
#SBATCH --time=00:30:00
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
baseline=${STATIC_BASELINE:-/scratch/mch/olifu/icon_hicar/case_studies/swiss_200m/static/domain_static_swiss_200m.nc}
cache=${STATIC_SOURCE_CACHE:-/scratch/mch/olifu/icon_hicar/cache/hicar_static_public}
candidate=${STATIC_CANDIDATE:-$qual_root/domain_static_swiss_200m_modal_wc_soil4_v1.nc}
audit=${STATIC_AUDIT:-$qual_root/static_abc_audit_v2_verified.json}

test "${SLURM_JOB_PARTITION:-}" = pp-short || {
  echo "static A/B/C audit must run in pp-short" >&2
  exit 2
}
for path in "$runtime_root/runtime_manifest.json" "$runtime_root/runtime_manifest.json.ready" \
  "$environment.environment.json" "$environment.environment.json.ready" \
  "$baseline" "$baseline.ready" "$candidate" "$candidate.ready"; do
  test -f "$path" || { echo "missing prerequisite: $path" >&2; exit 2; }
done
test ! -w "$environment/pyvenv.cfg" || {
  echo "static Python environment is writable" >&2
  exit 2
}
if test -e "$audit" || test -e "$audit.ready"; then
  echo "refusing to overwrite existing audit publication: $audit" >&2
  exit 2
fi
. "$runtime_root/scripts/load_balfrin_site_config.sh"
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"
module load python/3.11.7 >/dev/null
"$python" - "$environment.environment.json" "$runtime_root/runtime_manifest.json" <<'PY'
import hashlib, json, sys
environment = json.load(open(sys.argv[1]))
runtime_bytes = open(sys.argv[2], "rb").read()
if environment.get("runtime_manifest_sha256") != hashlib.sha256(runtime_bytes).hexdigest():
    raise SystemExit("static Python environment is not bound to this runtime manifest")
PY
"$python" "$runtime_root/case_studies/swiss_200m/validation/validate_domain_plan.py" \
  --static-file "$candidate"
"$python" "$runtime_root/case_studies/swiss_200m/fixed_parameters/audit_static_candidate.py" \
  --baseline "$baseline" --candidate "$candidate" --source-cache "$cache" \
  --topography-tolerance-m 0 --output "$audit"
"$python" - "$audit" <<'PY'
import json, sys
report = json.load(open(sys.argv[1]))
expected = "STATIC_AUDIT_PASS_PAIRED_COUPLED_CASES_REQUIRED"
if report.get("decision") != expected:
    raise SystemExit(f"static audit did not pass: {report.get('decision')}")
if not report["gates"]["atomic_audit_publication"]["verified_after_write"]:
    raise SystemExit("audit publication was not represented as verified in the final payload")
print(report["decision"])
PY
