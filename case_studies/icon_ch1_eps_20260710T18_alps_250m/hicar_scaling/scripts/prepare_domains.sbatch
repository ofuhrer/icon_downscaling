#!/bin/bash
#SBATCH --job-name=hicar_scaling_prepare
#SBATCH --partition=pp-long
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --time=24:00:00
set -euo pipefail
ROOT=${SCALING_ROOT:?}
export HICAR_WORKSPACE="$SCRATCH/icon_hicar"
export STATIC_PYTHON="$SCRATCH/icon_hicar/venv_static/bin/python"
"$ROOT/scripts/prepare_domains.sh"
