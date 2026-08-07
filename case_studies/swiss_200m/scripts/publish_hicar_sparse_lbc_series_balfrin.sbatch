#!/bin/bash -l
# Publish a certified 00/01/02 HICAR sparse-LBC sequence from independent
# initialization-only runs for the same target grid.
#SBATCH --job-name=hicar-lbc-publish
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=00:30:00

set -euo pipefail

root=${HICAR_LBC_QUALIFICATION_ROOT:?Set HICAR_LBC_QUALIFICATION_ROOT}
coordinator=${HICAR_LBC_COORDINATOR_ROOT:?Set HICAR_LBC_COORDINATOR_ROOT}
date=${HICAR_LBC_CASE_DATE:?Set HICAR_LBC_CASE_DATE}
method=${HICAR_LBC_SOIL_METHOD:-smi}
times=${HICAR_LBC_CASE_TIMES:-"0000 0100 0200"}
surface_root=${HICARPREP_SURFACE_ROOT:-$SCRATCH/icon_hicar/qualification/hicarprep_surface_cases_v11}
static=$surface_root/$date/hicar_runtime_domain_${method}_${date}_0000.nc
weights=$surface_root/icon_rea_l_to_alpine_bridge_200m_rbf.nc
python_bin=${HICAR_VALIDATION_PYTHON:-$SCRATCH/icon_hicar/qualification/wind-spinup-morrison-b514/python-v2/bin/python}
hicarprep=$coordinator/scripts/hicarprep.py
output_root=$root/sparse_lbc

partition=${SLURM_JOB_PARTITION:?This publisher must run under Slurm}
case "$partition" in pp-short|pp-long) ;; *) echo "unsupported partition: $partition" >&2; exit 2 ;; esac
allow=$(scontrol show partition "$partition" -o | sed -n 's/.*AllowGroups=\([^ ]*\).*/\1/p')
case ",$allow," in *,ALL,*|*,s83,*) ;; *) echo "partition access rejected: $allow" >&2; exit 2 ;; esac
for path in "$coordinator" "$static" "$static.ready" "$weights" "$python_bin" "$hicarprep"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done
test ! -e "$output_root" || { echo "refusing to overwrite $output_root" >&2; exit 2; }
mkdir -p "$output_root"

boundary_arguments=()
boundary_files=()
for time in $times; do
  run=$root/initialization/runs/${date}_${time}_${method}
  certificate=$run/balance_certificate.json
  shopt -s nullglob
  states=("$run/output"/*.nc)
  shopt -u nullglob
  test "${#states[@]}" -eq 1 || {
    echo "expected one initialized state under $run/output" >&2
    exit 2
  }
  state=${states[0]}
  for path in "$state" "$certificate"; do
    test -s "$path" || { echo "missing certified input: $path" >&2; exit 2; }
  done
  test -e "$certificate.ready" || {
    echo "unpublished certificate: $certificate.ready" >&2
    exit 2
  }
  initial=$output_root/hicar_input_${date}_${time}.nc
  boundary=$output_root/hicar_bdy_${date}_${time}.nc
  manifest=$output_root/hicar_publication_${date}_${time}.json
  "$python_bin" "$hicarprep" publish-certified-initialization \
    --initialized-state "$state" \
    --balance-certificate "$certificate" \
    --static "$static" --weights "$weights" \
    --initial "$initial" --boundary "$boundary" --manifest "$manifest"
  touch "$initial.ready" "$boundary.ready" "$manifest.ready"
  boundary_arguments+=(--boundary "$boundary")
  boundary_files+=("$boundary")
done

sequence=$output_root/hicar_bdy_sequence.json
"$python_bin" "$hicarprep" validate-boundaries \
  "${boundary_arguments[@]}" --maximum-interval-seconds 3600 \
  --manifest "$sequence"
touch "$sequence.ready"
list=$output_root/sparse_lbc_list.txt
printf '"%s"\n' "${boundary_files[@]}" > "$list"
touch "$list.ready"

printf 'PASS frames=%s list=%s sequence=%s\n' "${#boundary_files[@]}" "$list" "$sequence"
