#!/bin/bash -l
# Swiss-national two-hour uninterrupted-vs-segmented restart qualification.
# NetCDF comparison is deliberately left to a dependent pp-short job.
#SBATCH --job-name=hicar-restart-national
#SBATCH --partition=normal
#SBATCH --nodes=4
#SBATCH --ntasks-per-node=5
#SBATCH --cpus-per-task=1
#SBATCH --gres=gpu:4
#SBATCH --time=01:00:00
#SBATCH --exclusive
#SBATCH --output=/scratch/mch/olifu/icon_hicar/case_studies/swiss_200m/validation/restart_continuity/national_%j.out
#SBATCH --error=/scratch/mch/olifu/icon_hicar/case_studies/swiss_200m/validation/restart_continuity/national_%j.err

set -euo pipefail

[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module purge || true
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module load nvhpc/24.5 cray-mpich-nvhpc/8.1.30 cuda/12.3.0-gcc \
  netcdf-c/4.9.2-nvhpc netcdf-fortran/4.6.1-nvhpc \
  hdf5/1.14.3-nvhpc fftw/3.3.10-gcc

case_root=${HICAR_SWISS_CASE:-${SCRATCH:?}/icon_hicar/case_studies/swiss_200m}
source_root=${HICAR_RESTART_SOURCE_ROOT:?Set HICAR_RESTART_SOURCE_ROOT}
build_root=${HICAR_RESTART_BUILD_ROOT:?Set HICAR_RESTART_BUILD_ROOT}
expected_commit=${HICAR_RESTART_EXPECTED_COMMIT:?Set HICAR_RESTART_EXPECTED_COMMIT}
expected_parent=${HICAR_RESTART_EXPECTED_PARENT:?Set HICAR_RESTART_EXPECTED_PARENT}
renderer=${HICAR_RESTART_RENDERER:?Set HICAR_RESTART_RENDERER}
renderer_sha256=${HICAR_RESTART_RENDERER_SHA256:?Set HICAR_RESTART_RENDERER_SHA256}
renderer_template=$(dirname "$(dirname "$renderer")")/config/hicar_swiss_200m.nml.in
template_sha256=${HICAR_RESTART_TEMPLATE_SHA256:?Set HICAR_RESTART_TEMPLATE_SHA256}
python=${HICAR_VALIDATION_PYTHON:-${SCRATCH}/icon_hicar/venv_static/bin/python}
wrapper=${HICAR_GPU_WRAPPER:-${case_root}/scripts/gpu_rank_wrapper.sh}
base_plan=${HICAR_RESTART_BASE_PLAN:-${SCRATCH}/icon_hicar/streaming/rea_l_ch1/qualification_20200701_00_02/chunk_plan.json}
static_file=${HICAR_RESTART_STATIC_FILE:-${case_root}/static_initializations/domain_static_swiss_200m_rea_l_20200701_0000.nc}
run_root=${HICAR_RESTART_NATIONAL_RUN_ROOT:-${case_root}/validation/restart_continuity/national_${SLURM_JOB_ID:?}}
exe=${build_root}/HICAR_gpu

for path in \
  "$exe" "$renderer" "$renderer_template" "$python" "$wrapper" "$base_plan" \
  "${base_plan}.ready" "$static_file" "${static_file}.ready"; do
  test -e "$path" || {
    echo "missing required input: $path" >&2
    exit 2
  }
done
test "$(sha256sum "$renderer" | awk '{print $1}')" = "$renderer_sha256"
test "$(sha256sum "$renderer_template" | awk '{print $1}')" = "$template_sha256"
test "$(git -C "$source_root" rev-parse HEAD)" = "$expected_commit"
test "$(git -C "$source_root" rev-parse HEAD^)" = "$expected_parent"
test "$(git -C "$source_root" merge-base "$expected_parent" "$expected_commit")" = \
  "$expected_parent"
git -C "$source_root" diff --quiet
git -C "$source_root" diff --cached --quiet
test -z "$(git -C "$source_root" ls-files --others --exclude-standard -- \
  src cmake external tools CMakeLists.txt CMakePresets.json)"
test ! -e "$run_root"
mkdir -p "$run_root"

IFS=$'\t' read -r base_start base_end forcing_list < <(
  "$python" - "$base_plan" <<'PY'
import json
import sys

plan = json.load(open(sys.argv[1], encoding="utf-8"))
if plan.get("start") != "2020-07-01T00:00:00":
    raise SystemExit("unexpected national restart-plan start")
if plan.get("end") != "2020-07-01T02:00:00":
    raise SystemExit("unexpected national restart-plan end")
if len(plan.get("records", [])) != 3:
    raise SystemExit("national restart plan must have three hourly records")
print(plan["start"], plan["end"], plan["forcing_list"], sep="\t")
PY
)
test -f "$forcing_list"
test -f "${forcing_list}.ready"

continuation_plan="$run_root/continuation_plan.json"
continuation_list="$run_root/continuation_forcing_list.txt"
"$python" - "$base_plan" "$continuation_plan" "$continuation_list" <<'PY'
import json
from pathlib import Path
import sys

source = json.load(open(sys.argv[1], encoding="utf-8"))
target = Path(sys.argv[2])
forcing_list = Path(sys.argv[3])
records = source["records"][1:]
if [record["valid_time"] for record in records] != [
    "2020-07-01T01:00:00",
    "2020-07-01T02:00:00",
]:
    raise SystemExit("unexpected continuation forcing records")
list_tmp = forcing_list.with_suffix(".tmp")
list_tmp.write_text(
    "".join(f'"{record["forcing_file"]}"\n' for record in records),
    encoding="utf-8",
)
list_tmp.replace(forcing_list)
plan = dict(source)
plan.update(
    {
        "chunk_id": "restart_national_20200701_01_02",
        "chunk_root": str(target.parent),
        "start": "2020-07-01T01:00:00",
        "end": "2020-07-01T02:00:00",
        "hours": 1,
        "record_count": 2,
        "records": records,
        "forcing_list": str(forcing_list),
    }
)
tmp = target.with_suffix(".tmp")
tmp.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
tmp.replace(target)
Path(f"{forcing_list}.ready").touch()
Path(f"{target}.ready").touch()
PY

for label in continuous restart; do
  mkdir -p "$run_root/$label/output" "$run_root/$label/restart"
done

render_case() {
  local label="$1"
  local plan="$2"
  local forcing="$3"
  local start="$4"
  local end="$5"
  local restart_from="${6:-}"
  local restart_args=()
  if test -n "$restart_from"; then
    restart_args=(--restart-from "$restart_from")
  fi
  "$python" "$renderer" \
    --static-file "$static_file" \
    --forcing-file-list "$forcing" \
    --forcing-plan "$plan" \
    --start-date "$start" \
    --end-date "$end" \
    --output-interval 3600 \
    --output-profile qualification \
    --rea-l-land-initialization \
    --nz 80 \
    --wind-solver-iterations 2500 \
    --restart-interval 1 \
    "${restart_args[@]}" \
    --output-dir "$run_root/$label/output" \
    --restart-dir "$run_root/$label/restart" \
    --output "$run_root/$label/input.nml"
}

render_case continuous "$base_plan" "$forcing_list" \
  "2020-07-01 00:00:00" "2020-07-01 02:00:00"
render_case restart "$continuation_plan" "$continuation_list" \
  "2020-07-01 01:00:00" "2020-07-01 02:00:00" \
  "2020-07-01 01:00:00"

"$python" - "$run_root/continuous/input.nml" "$run_root/restart/input.nml" <<'PY'
from pathlib import Path
import re
import sys

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
for item in sys.argv[1:]:
    path = Path(item)
    text = path.read_text(encoding="utf-8")
    text, count = re.subn(
        r"(?m)^[ \t]*output_vars[ \t]*=.*$",
        f"  output_vars = {output_vars}",
        text,
        count=1,
    )
    if count != 1:
        raise SystemExit(f"expected one output_vars assignment in {path}")
    path.write_text(text, encoding="utf-8")
PY

stage_assets() {
  local run="$1"
  for asset in NoahmpTable.TBL rrtmgp_support mp_support; do
    ln -s "$source_root/run/$asset" "$run/$asset"
  done
}
stage_assets "$run_root/continuous"
stage_assets "$run_root/restart"

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
  "$run_root/continuous/restart/"*"_2020-07-01_01-00-00.nc"
)
shopt -u nullglob
test "${#source_restarts[@]}" -eq 1
ln "${source_restarts[0]}" \
  "$run_root/restart/restart/$(basename "${source_restarts[0]}")"

run_model restart

"$python" - \
  "$run_root" "$source_root" "$exe" "$renderer" "$forcing_list" \
  "$static_file" <<'PY'
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

run_root, source_root, exe, renderer, forcing_list, static_file = map(
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
    run_root / "continuous/restart", "*_2020-07-01_01-00-00.nc"
)
continuous_end = only(
    run_root / "continuous/restart", "*_2020-07-01_02-00-00.nc"
)
restart_end = only(
    run_root / "restart/restart", "*_2020-07-01_02-00-00.nc"
)
payload = {
    "schema_version": 1,
    "status": "MODEL_RUNS_COMPLETE",
    "source_commit": subprocess.check_output(
        ["git", "-C", str(source_root), "rev-parse", "HEAD"], text=True
    ).strip(),
    "run_root": str(run_root),
    "executable": artifact(exe),
    "renderer": artifact(renderer),
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
