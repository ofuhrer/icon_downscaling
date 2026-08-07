#!/bin/bash -l
# Retrieve and audit bounded authoritative Swiss static references.
#SBATCH --job-name=static-ref-audit
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --time=01:00:00
#SBATCH --output=static_ref_audit_%j.out
#SBATCH --error=static_ref_audit_%j.err

set -euo pipefail

coordinator=${HICAR_COORDINATOR_ROOT:?Set HICAR_COORDINATOR_ROOT}
cache=${HICAR_STATIC_REFERENCE_CACHE:?Set HICAR_STATIC_REFERENCE_CACHE}
output_root=${HICAR_STATIC_REFERENCE_AUDIT_ROOT:?Set HICAR_STATIC_REFERENCE_AUDIT_ROOT}
baseline=${HICAR_STATIC_BASELINE:?Set HICAR_STATIC_BASELINE}
candidate=${HICAR_STATIC_CANDIDATE:?Set HICAR_STATIC_CANDIDATE}
python=${HICAR_VALIDATION_PYTHON:-${SCRATCH:?}/icon_hicar/venv_static/bin/python}
audit=$coordinator/case_studies/swiss_200m/fixed_parameters/audit_glamos_glacier_mask.py
inventory=$coordinator/case_studies/swiss_200m/fixed_parameters/publish_authoritative_reference_inventory.py
glacier_url=https://doi.glamos.ch/data/inventory/inventory_sgi2016_r2020.zip
tlm_api=https://ogd.swisstopo.admin.ch/services/swiseld/services/collections/ch.swisstopo.swisstlm3d/assets

for path in "$python" "$audit" "$inventory" "$baseline" "$candidate"; do
  test -e "$path" || { echo "missing required input: $path" >&2; exit 2; }
done
mkdir -p "$cache" "$output_root"
if [[ -f "$output_root/reference_inventory.json.ready" ]]; then
  printf 'PASS reused_audit_root=%s\n' "$output_root"
  exit 0
fi

publish_download() {
  local url=$1 target=$2 expected_size=${3:-}
  if [[ -f "$target" && -f "${target}.ready" ]]; then
    local expected actual
    expected=$(awk '$1=="sha256" {print $2; exit}' "${target}.ready")
    actual=$(sha256sum "$target" | awk '{print $1}')
    test -n "$expected" && test "$actual" = "$expected"
    return
  fi
  test ! -e "$target"
  local partial=${target}.partial.${SLURM_JOB_ID:?}
  curl --fail --location --retry 5 --retry-all-errors --output "$partial" "$url"
  if [[ -n "$expected_size" ]]; then
    test "$(stat -c %s "$partial")" = "$expected_size"
  fi
  mv "$partial" "$target"
  printf 'sha256 %s  %s\n' "$(sha256sum "$target" | awk '{print $1}')" "$(basename "$target")" > "${target}.ready.partial"
  mv "${target}.ready.partial" "${target}.ready"
}

glacier_zip=$cache/inventory_sgi2016_r2020.zip
publish_download "$glacier_url" "$glacier_zip" 9787779
unzip -t "$glacier_zip" >/dev/null

api_snapshot=$output_root/swisstlm3d_assets.json
if [[ ! -f "$api_snapshot" || ! -f "${api_snapshot}.ready" ]]; then
  test ! -e "$api_snapshot" || mv "$api_snapshot" "${api_snapshot}.failed-${SLURM_JOB_ID:?}"
  curl --fail --location --retry 5 --retry-all-errors --output "${api_snapshot}.partial" "$tlm_api"
  "$python" -m json.tool "${api_snapshot}.partial" >/dev/null
  mv "${api_snapshot}.partial" "$api_snapshot"
  printf 'sha256 %s  %s\n' "$(sha256sum "$api_snapshot" | awk '{print $1}')" "$(basename "$api_snapshot")" > "${api_snapshot}.ready"
else
  test "$(sha256sum "$api_snapshot" | awk '{print $1}')" = "$(awk '$1=="sha256" {print $2; exit}' "${api_snapshot}.ready")"
fi

glacier_report=$output_root/glamos_sgi2016_audit.json
if [[ ! -f "${glacier_report}.ready" ]]; then
  test ! -e "$glacier_report" || mv "$glacier_report" "${glacier_report}.failed-${SLURM_JOB_ID:?}"
  "$python" "$audit" \
    --source-zip "$glacier_zip" --source-url "$glacier_url" \
    --baseline "$baseline" --candidate "$candidate" --output "$glacier_report"
fi
test -f "${glacier_report}.ready"

inventory_output=$output_root/reference_inventory.json
test ! -e "$inventory_output" || mv "$inventory_output" "${inventory_output}.failed-${SLURM_JOB_ID:?}"
"$python" "$inventory" \
  --api-snapshot "$api_snapshot" \
  --glacier-zip "$glacier_zip" \
  --glacier-audit "$glacier_report" \
  --output "$inventory_output"
test -f "$output_root/reference_inventory.json.ready"

printf 'PASS audit_root=%s\n' "$output_root"
