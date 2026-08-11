#!/usr/bin/env bash
# Rebuild only the selected SLEVE geometry on the unchanged national static grid.
#SBATCH --job-name=hicar-sleve15
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=128G
#SBATCH --time=01:00:00

set -euo pipefail

[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module load nco/5.0.1-gcc

: "${HICAR_COORDINATOR_ROOT:?set clean coordinator checkout}"
: "${BASE_STATIC_12KM:?set selected 2061x1431 national static}"
: "${BASE_STATIC_15KM:?set new 15 km static path}"
python=${HICAR_PYTHON:-python3}

test -f "$BASE_STATIC_12KM"
test ! -e "$BASE_STATIC_15KM"
test ! -e "$BASE_STATIC_15KM.ready"
mkdir -p "$(dirname "$BASE_STATIC_15KM")"
partial="$BASE_STATIC_15KM.partial.$SLURM_JOB_ID"
trap 'rm -f "$partial"' EXIT

# NetCDF cannot delete variables in place. Copy every invariant field except
# the old vertical geometry, then append geometry using hicarprep's exact
# HICAR-compatible construction.
ncks -O -4 -x -v \
terrain_large_scale,terrain_small_scale,HHL,HFL,SLEVE_JACOBIAN,LAYER_THICKNESS,reference_layer_thickness \
  "$BASE_STATIC_12KM" "$partial"

PYTHONPATH="$HICAR_COORDINATOR_ROOT" "$python" - "$partial" <<'PY'
import sys
from pathlib import Path

import netCDF4
import numpy as np

from preprocessing.hicarprep.geometry import SleveConfig
from preprocessing.hicarprep.products import append_sleve_geometry

path = Path(sys.argv[1])
config = SleveConfig(
    nz=80,
    model_top_m=15_000.0,
    lowest_layer_m=20.0,
    stretch_factor=0.65,
    decay_rate_large=2.0,
    decay_rate_small=6.0,
    exponent=1.35,
    smooth_window_radius=5,
    smooth_cycles=10,
    minimum_layer_thickness_m=12.0,
)
append_sleve_geometry(path, config=config)
with netCDF4.Dataset(path) as dataset:
    thickness = np.asarray(dataset["LAYER_THICKNESS"][:], dtype=np.float64)
    jacobian = np.asarray(dataset["SLEVE_JACOBIAN"][:], dtype=np.float64)
    hhl = dataset["HHL"]
    if hhl.shape != (81, 1431, 2061):
        raise SystemExit(f"unexpected HHL shape {hhl.shape}")
    if not np.isclose(np.min(thickness), 17.007648212575077, atol=1.0e-6):
        raise SystemExit(f"unexpected minimum thickness {np.min(thickness)}")
    if np.min(jacobian) <= 0.0 or not np.allclose(hhl[-1], 15_000.0, atol=1.0e-8):
        raise SystemExit("invalid 15 km SLEVE geometry")
    print("minimum_layer_thickness_m", float(np.min(thickness)))
    print("minimum_jacobian", float(np.min(jacobian)))
PY

mv "$partial" "$BASE_STATIC_15KM"
sha256sum "$BASE_STATIC_15KM" > "$BASE_STATIC_15KM.ready"
trap - EXIT
