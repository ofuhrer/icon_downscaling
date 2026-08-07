#!/bin/bash
#SBATCH --job-name=hicar-v29-diag
#SBATCH --partition=pp-short
#SBATCH --time=00:20:00
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --output=/scratch/mch/olifu/icon_hicar/diagnostics/v29_surface_regime_%j.out
#SBATCH --error=/scratch/mch/olifu/icon_hicar/diagnostics/v29_surface_regime_%j.err

# Artifact-only diagnostic: it must never invoke HICAR or alter the frozen V29
# assessment.  All inputs are published V29 artifacts or the recorded static.
set -euo pipefail

[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

archive_root=${V29_ARCHIVE_ROOT:-/store_new/mch/msopr/olifu/icon_downscaling/recovery/v1/artifacts/qualification/v29-summer}
static_file=${V29_STATIC_FILE:-/scratch/mch/olifu/icon_hicar/case_studies/swiss_200m/static_initializations/domain_static_swiss_200m_rea_l_20200701_0000.nc}
python=${HICAR_VALIDATION_PYTHON:-/scratch/mch/olifu/icon_hicar/venv_static/bin/python}
diagnostic=${HICAR_DIAGNOSTIC_SCRIPT:-${HICAR_COORDINATOR_ROOT:?set HICAR_COORDINATOR_ROOT}/case_studies/swiss_200m/validation/diagnose_v29_surface_regime.py}
report=${V29_DIAGNOSTIC_REPORT:-$archive_root/diagnostics/v29_surface_regime.json}
input_namelist=${V29_INPUT_NAMELIST:-$archive_root/input.nml}

for path in "$python" "$diagnostic" "$static_file" "$input_namelist" \
  "$archive_root/output/domain_static_swiss_200m_rea_l_20200701_0000_2020-07-01_00-00-00.nc" \
  "$archive_root/output/domain_static_swiss_200m_rea_l_20200701_0000_2020-07-04_00-00-00.nc" \
  "$archive_root/summer_transition_event_assessment.json" \
  "$archive_root/swissmetnet_comparison.json" \
  "$archive_root/ogd_grid_comparison.json" \
  "$archive_root/rea_l_source_comparison.json"; do
  test -r "$path" || { echo "missing diagnostic input: $path" >&2; exit 2; }
done

"$python" "$diagnostic" \
  --static-file "$static_file" \
  --history "$archive_root/output/domain_static_swiss_200m_rea_l_20200701_0000_2020-07-01_00-00-00.nc" \
  --history "$archive_root/output/domain_static_swiss_200m_rea_l_20200701_0000_2020-07-04_00-00-00.nc" \
  --assessment "$archive_root/summer_transition_event_assessment.json" \
  --input-namelist "$input_namelist" \
  --station-report "$archive_root/swissmetnet_comparison.json" \
  --ogd-report "$archive_root/ogd_grid_comparison.json" \
  --source-report "$archive_root/rea_l_source_comparison.json" \
  --report "$report"

test -s "$report"
test -s "$report.manifest.json"
test -f "$report.ready"
