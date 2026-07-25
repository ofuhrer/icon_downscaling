#!/bin/bash -l
# Diagnose the first timestamp shared by continuous and restarted output files.
# This is diagnostic-only: it deliberately does not publish a ready marker.
#SBATCH --job-name=hicar-rst-boundary
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=00:30:00

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module load netcdf/4.9.2-openmpi-4.1.4 netcdf-fortran/4.6.1-openmpi-4.1.4

repo_root=${REPO_ROOT:-$SCRATCH/icon_hicar}
python=${HICAR_VALIDATION_PYTHON:-$repo_root/HICAR-radiation-cloud-fraction/tests/Test_Cases/venv/bin/python}
continuous=${CONTINUOUS_OUTPUT:?Set CONTINUOUS_OUTPUT}
restarted=${RESTARTED_OUTPUT:?Set RESTARTED_OUTPUT}
report=${RESTART_BOUNDARY_DIAGNOSTIC:?Set RESTART_BOUNDARY_DIAGNOSTIC}

for path in "$python" "$continuous" "$restarted"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done

"$python" - "$continuous" "$restarted" "$report" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

from netCDF4 import Dataset
import numpy as np


continuous_path = Path(sys.argv[1])
restarted_path = Path(sys.argv[2])
report_path = Path(sys.argv[3])


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def numeric_stats(left, right):
    left = np.asarray(left)
    right = np.asarray(right)
    if left.shape != right.shape:
        return {"status": "SHAPE_MISMATCH", "left_shape": left.shape, "right_shape": right.shape}
    if not (np.issubdtype(left.dtype, np.number) and np.issubdtype(right.dtype, np.number)):
        exact = np.array_equal(left, right)
        return {"status": "PASS" if exact else "FAIL", "exact": bool(exact)}
    finite = np.isfinite(left) & np.isfinite(right)
    exact = np.array_equal(left, right, equal_nan=True)
    if np.any(finite):
        difference = np.abs(left.astype(np.float64) - right.astype(np.float64))
        maximum = float(np.max(difference[finite]))
        unequal = int(np.count_nonzero((left != right) & finite))
        count = int(np.count_nonzero(finite))
    else:
        maximum = 0.0
        unequal = 0
        count = 0
    return {
        "status": "PASS" if exact else "FAIL",
        "exact": bool(exact),
        "finite_count": count,
        "unequal_count": unequal,
        "unequal_fraction": unequal / count if count else 0.0,
        "max_abs_difference": maximum,
    }


with Dataset(continuous_path) as continuous, Dataset(restarted_path) as restarted:
    continuous.set_auto_maskandscale(False)
    restarted.set_auto_maskandscale(False)
    continuous_times = np.asarray(continuous.variables["time"][:], dtype=np.float64)
    restarted_times = np.asarray(restarted.variables["time"][:], dtype=np.float64)
    restarted_index = 0
    continuous_index = int(
        np.argmin(np.abs(continuous_times - restarted_times[restarted_index]))
    )
    time_difference = float(
        abs(continuous_times[continuous_index] - restarted_times[restarted_index])
    )
    if time_difference > 1.0e-6:
        raise RuntimeError(
            "continuous and restarted files have no common timestamp within 0.0864 s"
        )
    common = sorted(set(continuous.variables) & set(restarted.variables))
    results = {}
    for name in common:
        left = continuous.variables[name]
        right = restarted.variables[name]
        if not left.dimensions or left.dimensions[0] != "time":
            continue
        if left.shape[0] <= continuous_index or right.shape[0] <= restarted_index:
            continue
        results[name] = numeric_stats(
            left[continuous_index], right[restarted_index]
        )

payload = {
    "schema_version": 1,
    "status": "DIAGNOSTIC",
    "continuous_output": str(continuous_path),
    "continuous_sha256": sha256(continuous_path),
    "restarted_output": str(restarted_path),
    "restarted_sha256": sha256(restarted_path),
    "comparison": "first restarted output record versus the continuous record at the same timestamp",
    "continuous_record_index": continuous_index,
    "restarted_record_index": restarted_index,
    "time_difference_days": time_difference,
    "compared_field_count": len(results),
    "mismatch_count": sum(value["status"] != "PASS" for value in results.values()),
    "fields": results,
    "interpretation": (
        "This comparison begins at the first timestamp actually written by the restarted run. "
        "It does not by itself distinguish initialization discontinuity from post-boundary growth."
    ),
}
report_path.parent.mkdir(parents=True, exist_ok=True)
tmp = report_path.with_name(f".{report_path.name}.tmp")
tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
tmp.replace(report_path)
print(report_path)
PY
