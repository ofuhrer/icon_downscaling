#!/usr/bin/env bash
#SBATCH --job-name=hicar-terrain-geometry
#SBATCH --partition=pp-long
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=128
#SBATCH --exclusive
#SBATCH --time=04:00:00
#SBATCH --mem=220G
#SBATCH --output=terrain-radiation-%j.out
#SBATCH --error=terrain-radiation-%j.err

set -euo pipefail

[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"

: "${HICAR_COORDINATOR_ROOT:?set immutable coordinator checkout}"
: "${HICAR_TERRAIN_PYTHON:?set Python from terrain_radiation_environment.yml}"
: "${BASE_STATIC:?set selected 2061x1431 Swiss static file}"
: "${DRIVING_TOPOGRAPHY:?set REA-L HSURF file covering target plus 20.2 km}"
: "${EGM2008_GRID:?set official us_nga_egm08_25.tif path}"
: "${TERRAIN_RADIATION_DIR:?set output directory below SCRATCH/icon_hicar}"

case "$TERRAIN_RADIATION_DIR" in
  "$SCRATCH/icon_hicar"/*) ;;
  *) echo "TERRAIN_RADIATION_DIR must be below $SCRATCH/icon_hicar" >&2; exit 2 ;;
esac

mkdir -p "$TERRAIN_RADIATION_DIR"
TERRAIN_ENV=$(CDPATH= cd -- "$(dirname -- "$HICAR_TERRAIN_PYTHON")/.." && pwd)
export LD_LIBRARY_PATH="$TERRAIN_ENV/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export OMP_NUM_THREADS="$SLURM_CPUS_PER_TASK"
export OMP_PROC_BIND=spread
export OMP_PLACES=cores
export TBB_NUM_THREADS="$SLURM_CPUS_PER_TASK"

"$HICAR_TERRAIN_PYTHON" \
  "$HICAR_COORDINATOR_ROOT/case_studies/swiss_200m/scripts/prepare_terrain_radiation_geometry.py" \
  --base-static "$BASE_STATIC" \
  --driving-topography "$DRIVING_TOPOGRAPHY" \
  --driving-latitude-variable lat_1 \
  --driving-longitude-variable lon_1 \
  --egm2008-grid "$EGM2008_GRID" \
  --search-distance-km 20 \
  --extended-dem "$TERRAIN_RADIATION_DIR/swiss_200m_extended_terrain.nc" \
  --geometry "$TERRAIN_RADIATION_DIR/swiss_200m_terrain_radiation_geometry.nc" \
  --output-static "$TERRAIN_RADIATION_DIR/domain_static_swiss_200m_terrain_radiation.nc" \
  --report "$TERRAIN_RADIATION_DIR/terrain_radiation_geometry_report.json"
