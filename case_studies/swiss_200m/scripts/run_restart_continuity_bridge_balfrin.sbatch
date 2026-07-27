#!/bin/bash -l
# Segmented-vs-uninterrupted restart gate on the 701 x 701 Alpine bridge.
# CPU-heavy NetCDF comparison is intentionally left to a dependent pp-short job.
#SBATCH --job-name=hicar-restart-bridge
#SBATCH --partition=normal
#SBATCH --nodes=2
#SBATCH --ntasks-per-node=5
#SBATCH --cpus-per-task=1
#SBATCH --gres=gpu:4
#SBATCH --time=00:45:00
#SBATCH --exclusive
#SBATCH --output=/scratch/mch/olifu/icon_hicar/case_studies/swiss_200m/validation/restart_continuity/bridge_%j.out
#SBATCH --error=/scratch/mch/olifu/icon_hicar/case_studies/swiss_200m/validation/restart_continuity/bridge_%j.err

set -euo pipefail

[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module purge || true
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module load nvhpc/24.5 cray-mpich-nvhpc/8.1.30 cuda/12.3.0-gcc \
  netcdf-c/4.9.2-nvhpc netcdf-fortran/4.6.1-nvhpc \
  hdf5/1.14.3-nvhpc fftw/3.3.10-gcc

case_root=${HICAR_SWISS_CASE:-${SCRATCH:?}/icon_hicar/case_studies/swiss_200m}
bridge_root=${HICAR_BRIDGE_CASE:-${SCRATCH}/icon_hicar/case_studies/alpine_bridge_200m}
source_root=${HICAR_RESTART_SOURCE_ROOT:?Set HICAR_RESTART_SOURCE_ROOT}
build_root=${HICAR_RESTART_BUILD_ROOT:?Set HICAR_RESTART_BUILD_ROOT}
expected_commit=${HICAR_RESTART_EXPECTED_COMMIT:?Set HICAR_RESTART_EXPECTED_COMMIT}
expected_parent=${HICAR_RESTART_EXPECTED_PARENT:-2ea31109801a2477a946840693934318f8d50c95}
template=${HICAR_BRIDGE_NML_SOURCE:-${case_root}/runs/integration_20100101_0030_dynamic_prec4/hicar.nml}
static_file=${bridge_root}/static/domain_static_alpine_bridge_200m.nc
wrapper=${HICAR_GPU_WRAPPER:-${case_root}/scripts/gpu_rank_wrapper.sh}
python=${HICAR_VALIDATION_PYTHON:-${SCRATCH}/icon_hicar/venv_static/bin/python}
run_root=${HICAR_RESTART_BRIDGE_RUN_ROOT:-${case_root}/validation/restart_continuity/bridge_${SLURM_JOB_ID:?}}
exe=${build_root}/HICAR_gpu

for path in \
  "$template" "$static_file" "${static_file}.ready" "$wrapper" "$python" "$exe"; do
  test -e "$path" || {
    echo "missing required input: $path" >&2
    exit 2
  }
done
test "$(git -C "$source_root" rev-parse HEAD)" = "$expected_commit"
test "$(git -C "$source_root" rev-parse HEAD^)" = "$expected_parent"
git -C "$source_root" diff --quiet
git -C "$source_root" diff --cached --quiet
test ! -e "$run_root"

forcing_list_source=$(awk -F"'" \
  '/^[[:space:]]*forcing_file_list[[:space:]]*=/{print $2; exit}' \
  "$template")
test -s "$forcing_list_source"
forcing_list=${run_root}/forcing_files.txt
mkdir -p "$run_root"
awk 'NF && $1 !~ /^#/ {gsub(/^"|"$/, "", $0); printf "\"%s\"\n", $0}' \
  "$forcing_list_source" > "$forcing_list"
test -s "$forcing_list"
while IFS= read -r forcing; do
  forcing=${forcing#\"}
  forcing=${forcing%\"}
  test -f "$forcing" && test -f "${forcing}.ready"
done < "$forcing_list"

for label in continuous restart; do
  mkdir -p "$run_root/$label/output" "$run_root/$label/restart"
  for asset in NoahmpTable.TBL rrtmgp_support mp_support; do
    ln -s "$source_root/run/$asset" "$run_root/$label/$asset"
  done
done

render_case() {
  local label="$1"
  local start="$2"
  local end="$3"
  local restart_run="$4"
  local restart_date="${5:-}"
  "$python" - \
    "$template" "$run_root/$label/input.nml" "$run_root/$label/output" \
    "$run_root/$label/restart" "$forcing_list" "$static_file" \
    "$start" "$end" "$restart_run" "$restart_date" <<'PY'
from pathlib import Path
import re
import sys

(
    template,
    output,
    output_dir,
    restart_dir,
    forcing_list,
    static_file,
    start,
    end,
    restart_run,
    restart_date,
) = sys.argv[1:]
text = Path(template).read_text(encoding="utf-8")
text = re.sub(
    r"(?ms)^&general\s.*?^/\s*$",
    f"&general\n  start_date = '{start}'\n  end_date = '{end}'\n/",
    text,
    count=1,
)
restart_lines = [
    "&restart",
    f"  restart_folder = '{restart_dir}/'",
    "  restartinterval = 1",
    f"  restart_run = {restart_run}",
]
if restart_date:
    restart_lines.append(f"  restart_date = '{restart_date}'")
restart_lines.extend(["  override_check = .False.", "/"])
text = re.sub(
    r"(?ms)^&restart\s.*?^/\s*$",
    "\n".join(restart_lines),
    text,
    count=1,
)
replacements = {
    "init_conditions_file": static_file,
    "forcing_file_list": forcing_list,
    "output_folder": f"{output_dir}/",
}
for key, value in replacements.items():
    text, count = re.subn(
        rf"(?m)^[ \t]*{key}[ \t]*=.*$",
        f"  {key} = '{value}'",
        text,
        count=1,
    )
    if count != 1:
        raise SystemExit(f"expected one {key} assignment")
text, count = re.subn(
    r"(?m)^[ \t]*outputinterval[ \t]*=.*$",
    "  outputinterval = 3600",
    text,
    count=1,
)
if count != 1:
    raise SystemExit("expected one outputinterval assignment")
output_vars = (
    "'precipitation', 'hus2m', 'canopy_water', 'runoff_surface', "
    "'runoff_subsurface', 'runoff_surface_cumulative', "
    "'runoff_subsurface_cumulative', 'evaporation_net_cumulative', "
    "'soil_water_content', 'soil_temperature', "
    "'water_aquifer', 'storage_gw', 'wetland_h20_store', "
    "'u', 'v', 'w', 'pressure', 'temperature', "
    "'potential_temperature', 'qv', 'density', "
    "'canopy_ice', 'canopy_liquid', 'canopy_fwet', "
    "'coeff_momentum_drag', 'lsm_last_precip', 'lsm_last_snow', "
    "'snow_height', 'swe_0', 'tend_th_lwrad', 'lsm_timestep_counter'"
)
text, count = re.subn(
    r"(?m)^[ \t]*output_vars[ \t]*=.*$",
    f"  output_vars = {output_vars}",
    text,
    count=1,
)
if count != 1:
    raise SystemExit("expected one output_vars assignment")
Path(output).write_text(text, encoding="utf-8")
PY
}

render_case continuous \
  "2010-01-01 00:00:00" "2010-01-01 02:00:00" ".False."
render_case restart \
  "2010-01-01 01:00:00" "2010-01-01 02:00:00" ".True." \
  "2010-01-01 01:00:00"

export OMP_NUM_THREADS=1
export MPICH_GPU_SUPPORT_ENABLED=0
export MPICH_GPU_MANAGED_MEMORY_SUPPORT_ENABLED=0
export HICAR_WIND_ADJOINT_PROJECTION=1
export HICAR_WIND_RECYCLE_DIM=0
unset HICAR_WIND_MULTILEVEL
unset HICAR_WIND_OPERATOR_AUDIT
unset HICAR_WIND_OPERATOR_AUDIT_MAX_ITERS

run_model() {
  local label="$1"
  (
    cd "$run_root/$label"
    srun --distribution=block:block --cpu-bind=none \
      "$wrapper" "$exe" "$run_root/$label/input.nml" 2>&1 |
      tee "$run_root/$label/model.out"
  )
  grep -q 'HICAR discretely adjoint wind projection enabled' \
    "$run_root/$label/model.out"
  grep -q 'HICAR SLEVE geometry gate:' "$run_root/$label/model.out"
  grep -q 'Simulation completed successfully!' "$run_root/$label/model.out"
  grep -q 'Timing across all compute images:' "$run_root/$label/model.out"
  ! grep -q 'Accelerator Fatal Error' "$run_root/$label/model.out"
}

run_model continuous

shopt -s nullglob
source_restarts=(
  "$run_root/continuous/restart/"*"_2010-01-01_01-00-00.nc"
)
shopt -u nullglob
test "${#source_restarts[@]}" -eq 1
ln "${source_restarts[0]}" \
  "$run_root/restart/restart/$(basename "${source_restarts[0]}")"

run_model restart

"$python" - \
  "$run_root" "$source_root" "$exe" "$template" "$forcing_list" \
  "$static_file" <<'PY'
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

run_root, source_root, exe, template, forcing_list, static_file = map(
    Path, sys.argv[1:]
)

def digest(path: Path) -> str:
    hasher = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()

def artifact(path: Path) -> dict[str, object]:
    return {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "sha256": digest(path),
    }

def only(directory: Path, pattern: str) -> Path:
    matches = list(directory.glob(pattern))
    if len(matches) != 1:
        raise SystemExit(f"expected one {pattern} in {directory}, got {matches}")
    return matches[0]

continuous_output = only(run_root / "continuous/output", "*.nc")
restart_output = only(run_root / "restart/output", "*.nc")
source_restart = only(
    run_root / "continuous/restart", "*_2010-01-01_01-00-00.nc"
)
continuous_end = only(
    run_root / "continuous/restart", "*_2010-01-01_02-00-00.nc"
)
restart_end = only(
    run_root / "restart/restart", "*_2010-01-01_02-00-00.nc"
)
payload = {
    "schema_version": 1,
    "status": "MODEL_RUNS_COMPLETE",
    "source_commit": subprocess.check_output(
        ["git", "-C", str(source_root), "rev-parse", "HEAD"], text=True
    ).strip(),
    "run_root": str(run_root),
    "executable": artifact(exe),
    "template": artifact(template),
    "forcing_list": artifact(forcing_list),
    "static_file": artifact(static_file),
    "continuous_output": artifact(continuous_output),
    "restart_output": artifact(restart_output),
    "source_restart": artifact(source_restart),
    "continuous_end_restart": artifact(continuous_end),
    "segmented_end_restart": artifact(restart_end),
}
target = run_root / "model_runs.json"
tmp = target.with_suffix(".tmp")
tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
tmp.replace(target)
print(target)
PY

printf 'PASS source_commit=%s run_root=%s\n' "$expected_commit" "$run_root"
