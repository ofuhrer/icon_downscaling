#!/bin/bash -l
# Measure compression on representative 2-D fields from the qualified output.
#SBATCH --job-name=hicar-output-compress
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --time=00:30:00

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module load python/3.11.7

repo_root=${REPO_ROOT:-$SCRATCH/icon_hicar}
python=${HICAR_VALIDATION_PYTHON:-$repo_root/venv_static/bin/python}
benchmark="$repo_root/case_studies/swiss_200m/validation/benchmark_routine_compression.py"
source=${HICAR_QUALIFIED_OUTPUT:-$repo_root/case_studies/swiss_200m/runs/adjoint_swiss_6h_4927314/output/domain_static_swiss_200m_2010-01-01_00-00-00.nc}
report="$repo_root/case_studies/swiss_200m/validation/routine_compression_benchmark.json"
work="$repo_root/tmp/routine_compression_${SLURM_JOB_ID}.nc"
mkdir -p "$(dirname "$work")"
rm -f "$report" "$report.ready" "$work"
"$python" "$benchmark" "$source" --report "$report" --work-file "$work"
touch "$report.ready"
