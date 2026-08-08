#!/bin/bash -l
# Source-consistency and symmetric assessment for the matched response pair.
#SBATCH --job-name=hprep-land-assess
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --time=00:45:00

set -euo pipefail
repo_root=${REPO_ROOT:?Set REPO_ROOT to the dedicated coordinator worktree}
. "$repo_root/scripts/load_balfrin_site_config.sh"
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
run_root=${HICARPREP_LAND_RESPONSE_ROOT:?Set the dedicated response run root}
reference_list=${HICARPREP_LAND_RESPONSE_REFERENCE_LIST:?Set the published reference list}
python_bin=$SCRATCH/icon_hicar/qualification/wind-spinup-morrison-b514/python-v2/bin/python
compare=$repo_root/case_studies/swiss_200m/validation/compare_hicar_to_rea_l_surface.py
assessor=$repo_root/case_studies/swiss_200m/validation/assess_hicarprep_land_response.py
definition=$repo_root/case_studies/swiss_200m/config/hicarprep_land_response_6h_v1.json
noahmp=$SCRATCH/icon_hicar/qualification/hicarprep_hicar_soiltexture_v1/source/run/NoahmpTable.TBL

for method in smi relative_saturation; do
  run=$run_root/$method
  test -f "$run/model.out.ready" || { echo "unpublished arm: $run" >&2; exit 2; }
  mapfile -t outputs < <(find "$run/output" -maxdepth 1 -type f -name '*.nc' -print | sort)
  test "${#outputs[@]}" -gt 0
  static_file=$(awk '{print $2}' "$run/runtime_domain.sha256")
  test -s "$static_file"
  args=()
  for output in "${outputs[@]}"; do args+=(--output-file "$output"); done
  "$python_bin" "$compare" --event-name "hicarprep-land-response-${method}" \
    --static-file "$static_file" \
    "${args[@]}" --reference-list "$reference_list" \
    --report "$run/source_consistency.json"
done

"$python_bin" "$assessor" --definition "$definition" \
  --run "smi=$run_root/smi" --run "relative_saturation=$run_root/relative_saturation" \
  --source-report "smi=$run_root/smi/source_consistency.json" \
  --source-report "relative_saturation=$run_root/relative_saturation/source_consistency.json" \
  --noahmp-table "$noahmp" --output "$run_root/assessment.json"
