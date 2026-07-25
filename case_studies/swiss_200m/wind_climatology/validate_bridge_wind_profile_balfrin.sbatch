#!/bin/bash -l
# Validate fixed-height and surface/PBL fields and reduce one bridge interval.
#SBATCH --job-name=hicar-wind-check
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=32G
#SBATCH --time=00:30:00
#SBATCH --output=/scratch/mch/olifu/icon_hicar/validation/wind-climatology/bridge-check-%j.out
#SBATCH --error=/scratch/mch/olifu/icon_hicar/validation/wind-climatology/bridge-check-%j.err

set -euo pipefail

[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

validation_root=${VALIDATION_ROOT:-${SCRATCH:?}/icon_hicar/validation/wind-climatology}
bridge_job=${BRIDGE_JOB_ID:?Set BRIDGE_JOB_ID to the completed model job}
expected_commit=${HICAR_EXPECTED_COMMIT:-2999c9bdf6e0ed50a7f44311e2c8555e26848d31}
run_root="${validation_root}/bridge-runs/${bridge_job}"
case_root="${SCRATCH}/icon_hicar/case_studies/alpine_bridge_200m"
static_file="${case_root}/static/domain_static_alpine_bridge_200m.nc"
model_output="${run_root}/output/domain_static_alpine_bridge_200m_2010-01-01_00-00-00.nc"
python=${HICAR_VALIDATION_PYTHON:-${SCRATCH}/icon_hicar/venv_static/bin/python}
fixed_validator="${SCRATCH}/icon_hicar/scripts/validate_hicar_wind_climatology.py"
fixed_reference="${SCRATCH}/icon_hicar/scripts/derive_hicar_wind_climatology.py"
reducer="${SCRATCH}/icon_hicar/scripts/reduce_hicar_wind_climatology.py"
validation_dir="${run_root}/validation"
fixed_report="${validation_dir}/fixed_height_reference.json"
pbl_report="${validation_dir}/surface_pbl.json"
reduced_output="${validation_dir}/wind_statistics_1800s.nc"
reduced_report="${validation_dir}/wind_statistics_1800s.json"
manifest="${validation_dir}/bridge_wind_profile_qualification.json"

for path in \
  "${python}" "${fixed_validator}" "${fixed_reference}" "${reducer}" \
  "${static_file}" \
  "${static_file}.ready" "${model_output}" "${run_root}/model.out" \
  "${run_root}/source_commit.txt"; do
  test -e "${path}" || {
    echo "missing required path: ${path}" >&2
    exit 2
  }
done
test "$(cat "${run_root}/source_commit.txt")" = "${expected_commit}"
mkdir -p "${validation_dir}"

"${python}" "${fixed_validator}" \
  --input "${model_output}" \
  --static-domain "${static_file}" \
  --absolute-tolerance 2e-6 \
  --y-block-size 64 \
  --report "${fixed_report}"

"${python}" - "${model_output}" "${pbl_report}" <<'PY'
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

import netCDF4
import numpy as np

source_path = Path(sys.argv[1]).resolve()
report_path = Path(sys.argv[2]).resolve()
requirements = {
    "ustar": (
        "magnitude_of_surface_friction_velocity_in_air",
        "m s-1",
        0.0,
        20.0,
    ),
    "surface_roughness": (
        "surface_roughness_length_for_momentum_in_air",
        "m",
        0.0,
        100.0,
    ),
    # HICAR stores the raw MM5-revised bulk Richardson number. Only the
    # internal similarity-function inversion is clipped at SBRLIM=250.
    "sfc_Ri": (None, "1", -1.0e4, 1.0e4),
    "hpbl": (
        "atmosphere_boundary_layer_thickness",
        "m",
        0.0,
        20000.0,
    ),
}
failures = []
ranges = {}
with netCDF4.Dataset(source_path) as dataset:
    expected_shape = (
        len(dataset.dimensions["time"]),
        len(dataset.dimensions["lat_y"]),
        len(dataset.dimensions["lon_x"]),
    )
    for name, (standard_name, units, lower, upper) in requirements.items():
        if name not in dataset.variables:
            failures.append(f"missing {name}")
            continue
        variable = dataset[name]
        if variable.dimensions != ("time", "lat_y", "lon_x"):
            failures.append(f"{name} dimensions are {variable.dimensions}")
        if variable.shape != expected_shape:
            failures.append(f"{name} shape is {variable.shape}")
        if standard_name is not None and getattr(
            variable, "standard_name", None
        ) != standard_name:
            failures.append(f"{name} lacks standard_name={standard_name}")
        if getattr(variable, "units", None) != units:
            failures.append(f"{name} lacks units={units}")
        values = np.asarray(
            np.ma.filled(np.ma.asarray(variable[:]), np.nan),
            dtype=np.float64,
        )
        if not np.all(np.isfinite(values)):
            failures.append(f"{name} contains non-finite values")
            continue
        minimum = float(np.min(values))
        maximum = float(np.max(values))
        ranges[name] = [minimum, maximum]
        if minimum < lower or maximum > upper:
            failures.append(
                f"{name} range {minimum}..{maximum} is outside {lower}..{upper}"
            )
payload = {
    "status": "PASS" if not failures else "FAIL",
    "created_utc": datetime.now(timezone.utc).isoformat(),
    "input": str(source_path),
    "ranges": ranges,
    "metadata_policy": (
        "CF standard names where available; sfc_Ri uses a descriptive "
        "long_name because CF has no matching standard name."
    ),
    "mol_policy": (
        "Excluded: HICAR mol is a surface-layer temperature scale in K, "
        "not Monin-Obukhov length."
    ),
    "failures": failures,
}
temporary = report_path.with_name(f".{report_path.name}.tmp-{os.getpid()}")
temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
os.replace(temporary, report_path)
if failures:
    raise SystemExit("\n".join(failures))
PY

"${python}" "${reducer}" \
  --input "${model_output}" \
  --output "${reduced_output}" \
  --interval-seconds 1800 \
  --static-file "${static_file}" \
  --start-time "2010-01-01T00:00:00" \
  --y-block-size 64 \
  --report "${reduced_report}"

"${python}" - \
  "${bridge_job}" "${expected_commit}" "${model_output}" \
  "${run_root}/model.out" "${fixed_report}" "${pbl_report}" \
  "${reduced_output}" "${reduced_report}" "${static_file}" "${manifest}" <<'PY'
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

(
    bridge_job,
    source_commit,
    model_output,
    model_log,
    fixed_report,
    pbl_report,
    reduced_output,
    reduced_report,
    static_file,
    manifest,
) = sys.argv[1:]

def digest(path: str) -> str:
    value = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()

fixed = json.load(open(fixed_report))
pbl = json.load(open(pbl_report))
reduced = json.load(open(reduced_report))
failures = []
for name, payload in (
    ("fixed-height reference", fixed),
    ("surface/PBL", pbl),
    ("interval reduction", reduced),
):
    if payload.get("status") != "PASS":
        failures.append(f"{name} report is not PASS")
if not reduced.get("surface_pbl_statistics"):
    failures.append("reduced product lacks surface/PBL statistics")
if not reduced.get("wind_distribution_statistics"):
    failures.append("reduced product lacks wind-distribution counts")
static_domain = reduced.get("static_domain")
if not static_domain or static_domain.get("static_domain_path") == "":
    failures.append("reduced product lacks a hash-bound static-domain companion")
elif static_domain.get("static_domain_sha256") != digest(static_file):
    failures.append("reduced product static-domain hash does not match its input")
payload = {
    "schema_version": 1,
    "status": "PASS" if not failures else "FAIL",
    "created_utc": datetime.now(timezone.utc).isoformat(),
    "model_job": int(bridge_job),
    "source_commit": source_commit,
    "static_domain": {
        "path": str(Path(static_file).resolve()),
        "size_bytes": Path(static_file).stat().st_size,
        "sha256": digest(static_file),
    },
    "model_output": {
        "path": str(Path(model_output).resolve()),
        "size_bytes": Path(model_output).stat().st_size,
        "sha256": digest(model_output),
    },
    "model_log_sha256": digest(model_log),
    "fixed_height_reference": fixed,
    "surface_pbl": pbl,
    "compact_reduction": {
        "report": reduced,
        "path": str(Path(reduced_output).resolve()),
        "size_bytes": Path(reduced_output).stat().st_size,
        "sha256": digest(reduced_output),
    },
    "gust_policy": (
        "No ICON VMAX. Resolved maxima are HICAR sample maxima, not "
        "3-second gusts."
    ),
    "failures": failures,
}
manifest_path = Path(manifest)
temporary = manifest_path.with_name(
    f".{manifest_path.name}.tmp-{os.getpid()}"
)
temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
os.replace(temporary, manifest_path)
if failures:
    raise SystemExit("\n".join(failures))
Path(f"{manifest_path}.ready").touch()
print(json.dumps(payload, indent=2, sort_keys=True))
PY

printf 'PASS: bridge wind-profile qualification published at %s\n' "${manifest}"
