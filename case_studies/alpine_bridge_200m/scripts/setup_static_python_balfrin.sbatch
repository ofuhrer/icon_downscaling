#!/bin/bash -l
#SBATCH --job-name=hicar-static-py
#SBATCH --partition=pp-short
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --time=00:15:00
#SBATCH --output=/scratch/mch/olifu/icon_hicar/case_studies/alpine_bridge_200m/validation/setup_static_python_%j.out
#SBATCH --error=/scratch/mch/olifu/icon_hicar/case_studies/alpine_bridge_200m/validation/setup_static_python_%j.err

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module purge || true
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module load python/3.11.7

environment="$SCRATCH/icon_hicar/venv_static"
python3 -m venv "$environment"
"$environment/bin/python" -m pip install --upgrade pip
"$environment/bin/python" -m pip install numpy netCDF4
"$environment/bin/python" - <<'PY'
import netCDF4
import numpy
print("validated static-input Python environment", numpy.__version__, netCDF4.__version__)
PY
