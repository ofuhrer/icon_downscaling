#!/bin/bash -l
# Run one of four independent whole-month scientific validation pathways.
# Submit separately with MONTH_VALIDATION_KIND=physical|rea_l_source|swissmetnet|ogd_grid.
#SBATCH --job-name=hicar-month-validate
#SBATCH --partition=pp-long
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=128G
#SBATCH --time=08:00:00

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

month_plan=${MONTH_PILOT_PLAN:?Set MONTH_PILOT_PLAN}
kind=${MONTH_VALIDATION_KIND:?Set MONTH_VALIDATION_KIND}
repo_root=${REPO_ROOT:-$SCRATCH/icon_hicar}
python=${HICAR_VALIDATION_PYTHON:-$repo_root/venv_static/bin/python}
case_root=$repo_root/case_studies/swiss_200m
ogd_manifest=${OGD_REFERENCE_MANIFEST:-$repo_root/observations/ogd_grid/2020/ogd_grid_reference_2020_events.json}

case "$kind" in
  physical|rea_l_source|swissmetnet|ogd_grid) ;;
  *) echo "unsupported MONTH_VALIDATION_KIND: $kind" >&2; exit 2 ;;
esac
for path in "$month_plan" "$month_plan.ready" "$python"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done

IFS=$'\t' read -r event_name static_file start end interval reference_list \
  observations observation_manifest report < <(
  "$python" - "$month_plan" "$kind" <<'PY'
import json
import sys
p = json.load(open(sys.argv[1]))
if p.get("status") != "PLANNED":
    raise SystemExit("month plan is not PLANNED")
v = p["validation_sources"]
print(
    f"scientific_month_{p['start']}_{p['end']}",
    p["static_file"],
    p["start"],
    p["end"],
    p["output_interval_seconds"],
    v["reference_list"],
    v["swissmetnet_observations"],
    v["swissmetnet_manifest"],
    p["validation_reports"][sys.argv[2]],
    sep="\t",
)
PY
)

mapfile -t output_files < <(
  "$python" - "$month_plan" <<'PY'
import json
import pathlib
import sys
p = json.load(open(sys.argv[1]))
for segment in p["segments"]:
    completion_path = pathlib.Path(segment["model_completion_report"])
    ready = pathlib.Path(f"{completion_path}.ready")
    if not completion_path.is_file() or not ready.is_file():
        raise SystemExit(f"segment completion is not published: {completion_path}")
    completion = json.load(open(completion_path))
    if completion.get("status") != "PASS":
        raise SystemExit(f"segment completion is not PASS: {completion_path}")
    for item in completion["output"]["files"]:
        print(item["path"])
PY
)
test "${#output_files[@]}" -eq 5 || {
  echo "expected one output file from each of five segments" >&2
  exit 2
}
for path in "$static_file" "$static_file.ready" "${output_files[@]}"; do
  test -e "$path" || { echo "missing required input: $path" >&2; exit 2; }
done
mkdir -p "$(dirname "$report")"

case "$kind" in
  physical)
    evaluator=$case_root/validation/evaluate_scientific_event.py
    test -e "$evaluator"
    if ! test -f "$report.ready"; then
      "$python" "$evaluator" \
        --event-name "$event_name" \
        --static-file "$static_file" \
        --output-file "${output_files[@]}" \
        --expected-start "$start" \
        --expected-end "$end" \
        --expected-interval-seconds "$interval" \
        --report "$report"
    fi
    ;;
  rea_l_source)
    comparator=$case_root/validation/compare_hicar_to_rea_l_surface.py
    for path in "$comparator" "$reference_list" "$reference_list.ready"; do
      test -e "$path" || { echo "missing REA-L comparison input: $path" >&2; exit 2; }
    done
    if ! test -f "$report.ready"; then
      args=()
      for path in "${output_files[@]}"; do args+=(--output-file "$path"); done
      "$python" "$comparator" \
        --event-name "$event_name" \
        --static-file "$static_file" \
        "${args[@]}" \
        --reference-list "$reference_list" \
        --report "$report"
    fi
    ;;
  swissmetnet)
    comparator=$case_root/validation/compare_hicar_rea_l_to_smn.py
    for path in "$comparator" "$reference_list" "$reference_list.ready" \
                "$observations" "$observations.ready" \
                "$observation_manifest" "$observation_manifest.ready"; do
      test -e "$path" || { echo "missing SwissMetNet comparison input: $path" >&2; exit 2; }
    done
    if ! test -f "$report.ready"; then
      args=()
      for path in "${output_files[@]}"; do args+=(--output-file "$path"); done
      "$python" "$comparator" \
        --event-name "$event_name" \
        --static-file "$static_file" \
        "${args[@]}" \
        --reference-list "$reference_list" \
        --observations "$observations" \
        --observation-manifest "$observation_manifest" \
        --minimum-core-pairs 1000 \
        --report "$report"
    fi
    ;;
  ogd_grid)
    comparator=$case_root/validation/compare_hicar_rea_l_to_ogd_grids.py
    for path in "$comparator" "$reference_list" "$reference_list.ready" \
                "$ogd_manifest" "$ogd_manifest.ready"; do
      test -e "$path" || { echo "missing OGD comparison input: $path" >&2; exit 2; }
    done
    if ! test -f "$report.ready"; then
      args=()
      for path in "${output_files[@]}"; do args+=(--output-file "$path"); done
      "$python" "$comparator" \
        --event-name "$event_name" \
        --static-file "$static_file" \
        "${args[@]}" \
        --reference-list "$reference_list" \
        --ogd-manifest "$ogd_manifest" \
        --minimum-pairs 1000 \
        --report "$report"
    fi
    ;;
esac
test -f "$report.ready"
echo "PASS: $kind whole-month validation published at $report"
