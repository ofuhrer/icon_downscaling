#!/bin/bash
# Submit one transition-only 72-hour event and its independent validators.
#
# This script deliberately does not submit the winter event, paired assessor,
# month planner, or production work.  The summer result must settle first.

set -euo pipefail

candidate=${HICAR_BASELINE_CANDIDATE_COMMIT:?Set candidate commit}
candidate_parent=${HICAR_BASELINE_CANDIDATE_PARENT:?Set candidate parent}
source_root=${HICAR_BASELINE_SOURCE_ROOT:?Set clean candidate source root}
build=${HICAR_BASELINE_BUILD:?Set candidate GPU/NCCL build directory}
expected_executable_sha=${HICAR_BASELINE_EXECUTABLE_SHA256:?Set executable SHA-256}
transition_dir=${HICAR_BASELINE_TRANSITION_DIR:?Set published transition directory}
runtime_dir=${HICAR_BASELINE_RUNTIME_DIR:?Set immutable runtime snapshot}
case_root=${HICAR_SWISS_CASE:-$SCRATCH/icon_hicar/case_studies/swiss_200m}
python=${HICAR_VALIDATION_PYTHON:-$SCRATCH/icon_hicar/venv_static/bin/python}
plan=${HICAR_BASELINE_EVENT_PLAN:?Set the preserved event chunk plan}
static_file=${HICAR_STATIC_FILE:?Set the REA-L-initialized event static file}
event_name=${HICAR_BASELINE_EVENT_NAME:-summer}
run=${HICAR_BASELINE_EVENT_RUN:?Set a new immutable event run directory}
dry_run=${HICAR_BASELINE_DRY_RUN:-0}

manifest="$transition_dir/baseline_transition_plan.json"
candidate_plan="$transition_dir/scientific_pilot_plan_candidate.json"
runtime_manifest="$transition_dir/runtime_manifest.json"
exe="$build/HICAR_gpu"
runner="$runtime_dir/run_rea_l_stream_chunk_balfrin.sbatch"
model_validator="$runtime_dir/validate_model_chunk.py"
event_wrapper="$runtime_dir/validate_scientific_event_balfrin.sbatch"
event_evaluator="$runtime_dir/evaluate_scientific_event.py"
rea_l_comparator="$runtime_dir/compare_hicar_to_rea_l_surface.py"
solver_wrapper="$runtime_dir/validate_solver_event_balfrin.sbatch"
solver_evaluator="$runtime_dir/evaluate_hicar_solver_log.py"
checkpoint_wrapper="$runtime_dir/validate_event_restart_checkpoints_balfrin.sbatch"
checkpoint_validator="$runtime_dir/validate_event_restart_checkpoints.py"
smn_wrapper="$runtime_dir/validate_smn_event_balfrin.sbatch"
smn_comparator="$runtime_dir/compare_hicar_rea_l_to_smn.py"
ogd_wrapper="$runtime_dir/validate_ogd_grid_event_balfrin.sbatch"
ogd_comparator="$runtime_dir/compare_hicar_rea_l_to_ogd_grids.py"
receipt="$transition_dir/${event_name}_submission_receipt.json"

case "$event_name" in
  summer) expected_start=2020-07-01T00:00:00 ;;
  winter) expected_start=2020-01-15T00:00:00 ;;
  *) echo "event name must be summer or winter" >&2; exit 2 ;;
esac
case "$dry_run" in
  0|false|FALSE) dry_run=0 ;;
  1|true|TRUE) dry_run=1 ;;
  *) echo "HICAR_BASELINE_DRY_RUN must be true/false or 1/0" >&2; exit 2 ;;
esac

for path in \
  "$manifest" "$manifest.ready" "$candidate_plan" "$candidate_plan.ready" \
  "$runtime_manifest" "$runtime_manifest.ready" "$source_root" "$exe" \
  "$plan" "$plan.ready" "$static_file" "$static_file.ready" "$python" \
  "$runner" "$model_validator" "$event_wrapper" "$event_evaluator" \
  "$rea_l_comparator" "$solver_wrapper" "$checkpoint_wrapper" \
  "$solver_evaluator" "$checkpoint_validator" "$smn_wrapper" \
  "$smn_comparator" "$ogd_wrapper" "$ogd_comparator"; do
  test -e "$path" || { echo "missing transition input: $path" >&2; exit 2; }
done

"$python" - \
  "$manifest" "$candidate_plan" "$runtime_manifest" "$candidate" \
  "$candidate_parent" "$source_root" "$exe" "$expected_executable_sha" \
  "$plan" "$expected_start" "$static_file" "$event_name" <<'PY'
import hashlib
import json
import pathlib
import subprocess
import sys

manifest_path = pathlib.Path(sys.argv[1])
candidate_plan_path = pathlib.Path(sys.argv[2])
runtime_manifest_path = pathlib.Path(sys.argv[3])
candidate = sys.argv[4]
parent = sys.argv[5]
source_root = pathlib.Path(sys.argv[6])
executable = pathlib.Path(sys.argv[7])
expected_executable_sha = sys.argv[8]
event_plan_path = pathlib.Path(sys.argv[9])
expected_start = sys.argv[10]
static_file = pathlib.Path(sys.argv[11])
event_name = sys.argv[12]


def digest(path: pathlib.Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


manifest = json.loads(manifest_path.read_text())
candidate_plan = json.loads(candidate_plan_path.read_text())
runtime = json.loads(runtime_manifest_path.read_text())
event_plan = json.loads(event_plan_path.read_text())
if manifest.get("status") != "PLANNED":
    raise SystemExit("baseline transition manifest is not PLANNED")
if manifest.get("classification") != "SCIENTIFIC_BASELINE_CANDIDATE":
    raise SystemExit("baseline transition classification is wrong")
if manifest.get("candidate_commit") != candidate:
    raise SystemExit("candidate commit disagrees with transition manifest")
if manifest.get("candidate_parent_commit") != parent:
    raise SystemExit("candidate parent disagrees with transition manifest")
if any(manifest.get("authorization", {}).values()):
    raise SystemExit("transition manifest unexpectedly authorizes production")
configuration = candidate_plan.get("configuration", {})
if configuration.get("event_expected_hicar_commit") != candidate:
    raise SystemExit("candidate event plan has the wrong source commit")
if configuration.get("month_expected_hicar_commit") is not None:
    raise SystemExit("candidate event plan unexpectedly freezes a month source")
if (
    configuration.get("baseline_transition", {}).get("mode")
    != "SCIENTIFIC_BASELINE_REQUALIFICATION_ONLY"
):
    raise SystemExit("candidate event plan is not transition-only")

head = subprocess.check_output(
    ["git", "-C", str(source_root), "rev-parse", "HEAD"], text=True
).strip()
if head != candidate:
    raise SystemExit(f"candidate source is at {head}, expected {candidate}")
tracked = subprocess.check_output(
    ["git", "-C", str(source_root), "status", "--porcelain", "--untracked-files=no"],
    text=True,
)
untracked_build_inputs = subprocess.check_output(
    [
        "git",
        "-C",
        str(source_root),
        "ls-files",
        "--others",
        "--exclude-standard",
        "--",
        "src",
        "cmake",
        "external",
        "tools",
        "CMakeLists.txt",
        "CMakePresets.json",
    ],
    text=True,
)
if tracked or untracked_build_inputs:
    raise SystemExit("candidate source tree is not clean")
if digest(executable) != expected_executable_sha:
    raise SystemExit("candidate executable checksum changed")

if runtime.get("status") != "PASS":
    raise SystemExit("runtime snapshot manifest is not PASS")
for item in runtime.get("files", []):
    path = pathlib.Path(item["path"])
    if not path.is_file() or digest(path) != item["sha256"]:
        raise SystemExit(f"runtime snapshot changed: {path}")

if event_plan.get("status") != "PLANNED":
    raise SystemExit("event forcing plan is not PLANNED")
if event_plan.get("start") != expected_start or event_plan.get("hours") != 72:
    raise SystemExit("event forcing plan has the wrong period")
if event_plan.get("record_count") != 73 or len(event_plan.get("records", [])) != 73:
    raise SystemExit("event forcing plan does not contain 73 hourly records")
for record in event_plan["records"]:
    payload = pathlib.Path(record["forcing_file"])
    ready = pathlib.Path(record["ready_marker"])
    if not payload.is_file() or not ready.is_file():
        raise SystemExit(f"forcing record is not published: {payload}")
forcing_publication = pathlib.Path(event_plan["chunk_root"]) / "forcing_publication.json"
if not forcing_publication.is_file() or not pathlib.Path(
    f"{forcing_publication}.ready"
).is_file():
    raise SystemExit("forcing publication is missing")
if json.loads(forcing_publication.read_text()).get("status") != "PASS":
    raise SystemExit("forcing publication is not PASS")
event_root = event_plan_path.parent
for relative in (
    "reference/reference_list.txt",
    "observations/swissmetnet_hourly.csv",
    "observations/swissmetnet_hourly.manifest.json",
):
    path = event_root / relative
    if not path.is_file() or not pathlib.Path(f"{path}.ready").is_file():
        raise SystemExit(f"{event_name} reference input is not published: {path}")
if not static_file.is_file() or not pathlib.Path(f"{static_file}.ready").is_file():
    raise SystemExit("REA-L-initialized static file is not published")
PY

if test -e "$receipt" || test -e "$receipt.ready"; then
  echo "submission receipt already exists: $receipt" >&2
  exit 2
fi
if test -e "$run"; then
  echo "event run directory already exists: $run" >&2
  exit 2
fi

mkdir -p "$run"
model_exports="ALL,STREAM_PLAN=$plan,HICAR_MULTILEVEL_ROOT=$source_root"
model_exports+=",HICAR_MULTILEVEL_BUILD=$build,HICAR_SWISS_CASE=$case_root"
model_exports+=",HICAR_STATIC_FILE=$static_file,HICAR_STREAM_VALIDATOR=$model_validator"
model_exports+=",HICAR_VALIDATION_PYTHON=$python,STREAM_RUN_DIR=$run"
model_exports+=",STREAM_RESTART_DIR=$run/restart,STREAM_OUTPUT_PROFILE=qualification"
model_exports+=",STREAM_OUTPUT_INTERVAL=10800,STREAM_RESTART_INTERVAL_RECORDS=8"
model_exports+=",STREAM_REA_L_LAND_INITIALIZATION=1,HICAR_EXPECTED_COMMIT=$candidate"

if test "$dry_run" -eq 1; then
  rmdir "$run"
  echo "PASS: transition-only $event_name event submission preflight"
  exit 0
fi

model_job=$(
  sbatch --parsable --job-name="hicar-v29-${event_name}" \
    --output="$run/slurm_%j.out" --export="$model_exports" "$runner"
)
model_job=${model_job%%;*}

common_exports="ALL,STREAM_PLAN=$plan,EVENT_RUN_DIR=$run"
common_exports+=",REPO_ROOT=$SCRATCH/icon_hicar,HICAR_VALIDATION_PYTHON=$python"
common_exports+=",HICAR_STATIC_FILE=$static_file"

scientific_exports="$common_exports,HICAR_EVENT_EVALUATOR=$event_evaluator"
scientific_exports+=",HICAR_REA_L_COMPARATOR=$rea_l_comparator"
scientific_job=$(
  sbatch --parsable --dependency="afterok:$model_job" \
    --output="$run/scientific_%j.out" --export="$scientific_exports" \
    "$event_wrapper"
)
scientific_job=${scientific_job%%;*}
solver_exports="$common_exports,HICAR_SOLVER_EVALUATOR=$solver_evaluator"
solver_job=$(
  sbatch --parsable --dependency="afterok:$model_job" \
    --output="$run/solver_%j.out" --export="$solver_exports" "$solver_wrapper"
)
solver_job=${solver_job%%;*}
smn_exports="$common_exports,HICAR_SMN_COMPARATOR=$smn_comparator"
smn_job=$(
  sbatch --parsable --dependency="afterok:$model_job" \
    --output="$run/smn_%j.out" --export="$smn_exports" "$smn_wrapper"
)
smn_job=${smn_job%%;*}
ogd_exports="$common_exports,HICAR_OGD_COMPARATOR=$ogd_comparator"
ogd_job=$(
  sbatch --parsable --dependency="afterok:$model_job" \
    --output="$run/ogd_%j.out" --export="$ogd_exports" "$ogd_wrapper"
)
ogd_job=${ogd_job%%;*}

static_basename=$(basename "${static_file%.nc}")
checkpoint_exports="ALL,REPO_ROOT=$SCRATCH/icon_hicar"
checkpoint_exports+=",HICAR_VALIDATION_PYTHON=$python,EVENT_RUN_DIR=$run"
checkpoint_exports+=",HICAR_STATIC_BASENAME=$static_basename"
checkpoint_exports+=",EVENT_START=$expected_start,EVENT_DURATION_HOURS=72"
checkpoint_exports+=",EVENT_RESTART_INTERVAL_HOURS=24,HICAR_EXPECTED_COMMIT=$candidate"
checkpoint_exports+=",HICAR_RESTART_CHECKPOINT_VALIDATOR=$checkpoint_validator"
checkpoint_job=$(
  sbatch --parsable --dependency="afterok:$model_job" \
    --output="$run/restarts_%j.out" --export="$checkpoint_exports" \
    "$checkpoint_wrapper"
)
checkpoint_job=${checkpoint_job%%;*}

"$python" - \
  "$receipt" "$event_name" "$candidate" "$run" "$manifest" \
  "$candidate_plan" "$runtime_manifest" "$model_job" "$scientific_job" \
  "$solver_job" "$smn_job" "$ogd_job" "$checkpoint_job" <<'PY'
import json
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile

(
    receipt,
    event_name,
    candidate,
    run,
    manifest,
    candidate_plan,
    runtime_manifest,
    model_job,
    scientific_job,
    solver_job,
    smn_job,
    ogd_job,
    checkpoint_job,
) = sys.argv[1:]
payload = {
    "schema_version": 1,
    "status": "SUBMITTED",
    "classification": "SCIENTIFIC_BASELINE_TRANSITION_ONLY",
    "event": event_name,
    "candidate_commit": candidate,
    "run_dir": run,
    "transition_manifest": manifest,
    "candidate_event_plan": candidate_plan,
    "runtime_manifest": runtime_manifest,
    "jobs": {
        "model": model_job,
        "scientific": scientific_job,
        "solver": solver_job,
        "swissmetnet": smn_job,
        "ogd": ogd_job,
        "restart_checkpoints": checkpoint_job,
    },
    "authorization": {
        "month_compute": False,
        "annual_cycle": False,
        "twenty_year_200m_production": False,
    },
}
path = Path(receipt)
path.parent.mkdir(parents=True, exist_ok=True)
with NamedTemporaryFile("w", dir=path.parent, delete=False) as stream:
    json.dump(payload, stream, indent=2, sort_keys=True)
    stream.write("\n")
    temporary = Path(stream.name)
os.replace(temporary, path)
Path(f"{path}.ready").touch()
PY

echo "submitted $event_name transition event: model=$model_job"
echo "validators: $scientific_job $solver_job $smn_job $ogd_job $checkpoint_job"
