#!/bin/bash -l
#SBATCH --job-name=hicar-bridge-cover
#SBATCH --partition=pp-short
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --time=00:20:00
#SBATCH --output=/scratch/mch/olifu/icon_hicar/case_studies/alpine_bridge_200m/validation/forcing_coverage_%j.out
#SBATCH --error=/scratch/mch/olifu/icon_hicar/case_studies/alpine_bridge_200m/validation/forcing_coverage_%j.err

set -euo pipefail
case_root=${HICAR_BRIDGE_CASE:-$SCRATCH/icon_hicar/case_studies/alpine_bridge_200m}
static_file="$case_root/static/domain_static_alpine_bridge_200m.nc"
forcing_list=${HICAR_BRIDGE_FORCING_LIST:-$SCRATCH/icon_hicar/case_studies/swiss_200m/forcing/rea_l_ch1/forcing_20100101_0000_0200.txt}
validator="$SCRATCH/icon_hicar/case_studies/swiss_200m/validation/validate_forcing.py"
python_exe=${HICAR_STATIC_PYTHON:-$SCRATCH/icon_hicar/venv_static/bin/python}
report="$case_root/validation/forcing_coverage.json"
partial="$case_root/validation/.forcing_coverage.${SLURM_JOB_ID:?}.partial.json"

test -x "$python_exe" && test -f "$validator" && test -f "$static_file" && test -f "$static_file.ready"
forcing_file=$(awk 'NF && $1 !~ /^#/ {gsub(/^"|"$/, "", $0); print; exit}' "$forcing_list")
test -f "$forcing_file" && test -f "$forcing_file.ready"
"$python_exe" "$validator" --forcing-file "$forcing_file" --static-file "$static_file" --report "$partial"
mv "$partial" "$report"

