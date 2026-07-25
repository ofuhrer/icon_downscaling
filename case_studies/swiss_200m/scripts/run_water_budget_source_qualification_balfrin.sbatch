#!/bin/bash -l
# National parent/child and restart-continuity gate for water-budget diagnostics.
# The three model integrations share one four-node allocation and frozen forcing.
# CPU-heavy NetCDF comparison is deliberately left to a dependent pp-short job.
#SBATCH --job-name=hicar-water-budget-national
#SBATCH --partition=short
#SBATCH --nodes=4
#SBATCH --ntasks-per-node=5
#SBATCH --cpus-per-task=1
#SBATCH --gres=gpu:4
#SBATCH --time=01:00:00
#SBATCH --exclusive
#SBATCH --output=/scratch/mch/olifu/icon_hicar/case_studies/swiss_200m/validation/water_budget/national_source_qualification_%j.out
#SBATCH --error=/scratch/mch/olifu/icon_hicar/case_studies/swiss_200m/validation/water_budget/national_source_qualification_%j.err

set -euo pipefail

[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module purge || true
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module load nvhpc/24.5 cray-mpich-nvhpc/8.1.30 cuda/12.3.0-gcc \
  netcdf-c/4.9.2-nvhpc netcdf-fortran/4.6.1-nvhpc \
  hdf5/1.14.3-nvhpc fftw/3.3.10-gcc

case_root=${HICAR_SWISS_CASE:-${SCRATCH:?}/icon_hicar/case_studies/swiss_200m}
parent_root=${HICAR_WATER_BUDGET_PARENT_ROOT:-${SCRATCH}/icon_hicar/HICAR-radiation-cloud-fraction}
child_root=${HICAR_WATER_BUDGET_CHILD_ROOT:-${SCRATCH}/icon_hicar/HICAR-water-budget-p0}
parent_build=${HICAR_WATER_BUDGET_PARENT_BUILD:-${parent_root}/build_gpu_nccl}
child_build=${HICAR_WATER_BUDGET_CHILD_BUILD:-${child_root}/build_gpu_nccl_water_budget_p0}
parent_commit=${HICAR_WATER_BUDGET_PARENT_COMMIT:?Set HICAR_WATER_BUDGET_PARENT_COMMIT}
child_commit=${HICAR_WATER_BUDGET_CHILD_COMMIT:?Set HICAR_WATER_BUDGET_CHILD_COMMIT}
renderer=${HICAR_WATER_BUDGET_RENDERER:?Set HICAR_WATER_BUDGET_RENDERER}
renderer_sha256=${HICAR_WATER_BUDGET_RENDERER_SHA256:?Set HICAR_WATER_BUDGET_RENDERER_SHA256}
template_sha256=${HICAR_WATER_BUDGET_TEMPLATE_SHA256:?Set HICAR_WATER_BUDGET_TEMPLATE_SHA256}
renderer_template=$(dirname "$(dirname "$renderer")")/config/hicar_swiss_200m.nml.in
python=${HICAR_VALIDATION_PYTHON:-${SCRATCH}/icon_hicar/venv_static/bin/python}
wrapper=${HICAR_GPU_WRAPPER:-${case_root}/scripts/gpu_rank_wrapper.sh}
base_plan=${HICAR_WATER_BUDGET_BASE_PLAN:-${SCRATCH}/icon_hicar/streaming/rea_l_ch1/qualification_20200701_00_02/chunk_plan.json}
static_file=${HICAR_WATER_BUDGET_STATIC_FILE:-${case_root}/static_initializations/domain_static_swiss_200m_rea_l_20200701_0000.nc}
run_root=${HICAR_WATER_BUDGET_RUN_ROOT:-${case_root}/validation/water_budget/national_source_qualification_${SLURM_JOB_ID:?}}
parent_exe=${parent_build}/HICAR_gpu
child_exe=${child_build}/HICAR_gpu
include_water_budget_diagnostics=${HICAR_WATER_BUDGET_INCLUDE_DIAGNOSTICS:-1}

for path in \
  "$parent_exe" "$child_exe" "$renderer" "${renderer}.ready" \
  "$renderer_template" "${renderer_template}.ready" "$python" "$wrapper" \
  "$base_plan" "${base_plan}.ready" "$static_file" "${static_file}.ready"; do
  test -e "$path" || {
    echo "missing required input: $path" >&2
    exit 2
  }
done
test "$(sha256sum "$renderer" | awk '{print $1}')" = "$renderer_sha256"
test "$(sha256sum "$renderer_template" | awk '{print $1}')" = "$template_sha256"
test "$(git -C "$parent_root" rev-parse HEAD)" = "$parent_commit"
test "$(git -C "$child_root" rev-parse HEAD)" = "$child_commit"
test "$(git -C "$child_root" rev-parse HEAD^)" = "$parent_commit"
test "$(git -C "$child_root" merge-base "$parent_commit" "$child_commit")" = "$parent_commit"
for root in "$parent_root" "$child_root"; do
  git -C "$root" diff --quiet
  git -C "$root" diff --cached --quiet
  test -z "$(git -C "$root" ls-files --others --exclude-standard -- \
    src cmake external tools CMakeLists.txt CMakePresets.json)"
done

test ! -e "$run_root"
mkdir -p "$run_root"

IFS=$'\t' read -r base_start base_end forcing_list < <(
  "$python" - "$base_plan" <<'PY'
import json
import sys

plan = json.load(open(sys.argv[1], encoding="utf-8"))
if plan.get("start") != "2020-07-01T00:00:00":
    raise SystemExit("unexpected qualification-plan start")
if plan.get("end") != "2020-07-01T02:00:00":
    raise SystemExit("unexpected qualification-plan end")
if len(plan.get("records", [])) != 3:
    raise SystemExit("qualification plan must contain exactly three hourly records")
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
        "chunk_id": "water_budget_restart_20200701_01_02",
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

render_case() {
  local output="$1"
  local output_dir="$2"
  local restart_dir="$3"
  local plan="$4"
  local forcing="$5"
  local start="$6"
  local end="$7"
  local restart_interval="$8"
  local restart_from="${9:-}"
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
    --restart-interval "$restart_interval" \
    "${restart_args[@]}" \
    --output-dir "$output_dir" \
    --restart-dir "$restart_dir" \
    --output "$output"
}

for label in parent child_continuous child_restart; do
  mkdir -p "$run_root/$label/output" "$run_root/$label/restart"
done

render_case \
  "$run_root/parent/input.nml" \
  "$run_root/parent/output" \
  "$run_root/parent/restart" \
  "$base_plan" "$forcing_list" \
  "2020-07-01 00:00:00" "2020-07-01 02:00:00" 2

strip_water_budget_diagnostics() {
  "$python" - "$1" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
for name in (
    "runoff_surface_cumulative",
    "runoff_subsurface_cumulative",
    "evaporation_net_cumulative",
):
    text = text.replace(f"'{name}', ", "").replace(f", '{name}'", "")
path.write_text(text, encoding="utf-8")
PY
}

strip_water_budget_diagnostics "$run_root/parent/input.nml"

render_case \
  "$run_root/child_continuous/input.nml" \
  "$run_root/child_continuous/output" \
  "$run_root/child_continuous/restart" \
  "$base_plan" "$forcing_list" \
  "2020-07-01 00:00:00" "2020-07-01 02:00:00" 1

render_case \
  "$run_root/child_restart/input.nml" \
  "$run_root/child_restart/output" \
  "$run_root/child_restart/restart" \
  "$continuation_plan" "$continuation_list" \
  "2020-07-01 01:00:00" "2020-07-01 02:00:00" 1 \
  "2020-07-01 01:00:00"

if test "$include_water_budget_diagnostics" = 0; then
  strip_water_budget_diagnostics "$run_root/child_continuous/input.nml"
  strip_water_budget_diagnostics "$run_root/child_restart/input.nml"
fi

stage_assets() {
  local run="$1"
  local source_root="$2"
  for asset in NoahmpTable.TBL rrtmgp_support mp_support; do
    ln -s "$source_root/run/$asset" "$run/$asset"
  done
}
stage_assets "$run_root/parent" "$parent_root"
stage_assets "$run_root/child_continuous" "$child_root"
stage_assets "$run_root/child_restart" "$child_root"

export OMP_NUM_THREADS=1
export MPICH_GPU_SUPPORT_ENABLED=0
export MPICH_GPU_MANAGED_MEMORY_SUPPORT_ENABLED=0
export HICAR_WIND_ADJOINT_PROJECTION=1
export HICAR_WIND_RECYCLE_DIM=0
unset HICAR_WIND_MULTILEVEL
unset HICAR_WIND_OPERATOR_AUDIT
unset HICAR_WIND_OPERATOR_AUDIT_MAX_ITERS

run_model() {
  local run="$1"
  local exe="$2"
  (
    cd "$run"
    srun --distribution=block:block --cpu-bind=none \
      "$wrapper" "$exe" "$run/input.nml" 2>&1 | tee "$run/model.out"
  )
  grep -q 'HICAR discretely adjoint wind projection enabled' "$run/model.out"
  grep -q 'HICAR SLEVE geometry gate:' "$run/model.out"
  grep -q 'Simulation completed successfully!' "$run/model.out"
  grep -q 'Timing across all compute images:' "$run/model.out"
}

run_model "$run_root/parent" "$parent_exe"
run_model "$run_root/child_continuous" "$child_exe"

shopt -s nullglob
start_restarts=(
  "$run_root/child_continuous/restart/"*"_2020-07-01_01-00-00.nc"
)
shopt -u nullglob
test "${#start_restarts[@]}" -eq 1
ln "${start_restarts[0]}" "$run_root/child_restart/restart/$(basename "${start_restarts[0]}")"

run_model "$run_root/child_restart" "$child_exe"

"$python" - \
  "$run_root" "$parent_root" "$child_root" "$parent_exe" "$child_exe" \
  "$renderer" "$base_plan" "$static_file" <<'PY'
import hashlib
import json
from pathlib import Path
import subprocess
import sys

(
    run_root,
    parent_root,
    child_root,
    parent_exe,
    child_exe,
    renderer,
    plan,
    static_file,
) = map(Path, sys.argv[1:])

def sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()

def revision(root: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()

payload = {
    "schema_version": 1,
    "status": "MODEL_RUNS_COMPLETE",
    "parent_commit": revision(parent_root),
    "child_commit": revision(child_root),
    "parent_executable": {
        "path": str(parent_exe),
        "sha256": sha256(parent_exe),
    },
    "child_executable": {
        "path": str(child_exe),
        "sha256": sha256(child_exe),
    },
    "renderer": {"path": str(renderer), "sha256": sha256(renderer)},
    "base_plan": {"path": str(plan), "sha256": sha256(plan)},
    "static_file": {"path": str(static_file), "size_bytes": static_file.stat().st_size},
    "runs": {
        label: {
            "path": str(run_root / label),
            "model_log_sha256": sha256(run_root / label / "model.out"),
        }
        for label in ("parent", "child_continuous", "child_restart")
    },
}
tmp = run_root / "model_runs.json.tmp"
target = run_root / "model_runs.json"
tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
tmp.replace(target)
PY

echo "water-budget national model runs complete: $run_root"
