#!/bin/bash
#SBATCH --job-name=hicar_scaling_preflight
#SBATCH --partition=pp-long
#SBATCH --nodes=1
# 12 CPU compute ranks plus one I/O rank keeps the largest preflight shapes
# representative without making variational-wind initialization prohibitive.
#SBATCH --ntasks=13
#SBATCH --cpus-per-task=1
#SBATCH --time=06:00:00
set -euo pipefail
ROOT=${SCALING_ROOT:?}
"$ROOT/scripts/preflight_domains.sh"
