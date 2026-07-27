#!/bin/bash
# Submit the V29 hour-48-to-72 restart continuation and trajectory comparison.

set -euo pipefail

candidate=${HICAR_BASELINE_CANDIDATE_COMMIT:?Set candidate commit}
source_root=${HICAR_BASELINE_SOURCE_ROOT:?Set clean candidate source root}
build=${HICAR_BASELINE_BUILD:?Set candidate GPU/NCCL build directory}
expected_executable_sha=${HICAR_BASELINE_EXECUTABLE_SHA256:?Set executable SHA-256}
transition_dir=${HICAR_BASELINE_TRANSITION_DIR:?Set transition directory}
runtime_dir=${HICAR_BASELINE_OVERLAP_RUNTIME_DIR:?Set overlap runtime snapshot}
case_root=${HICAR_SWISS_CASE:-$SCRATCH/icon_hicar/case_studies/swiss_200m}
python=${HICAR_VALIDATION_PYTHON:-$SCRATCH/icon_hicar/venv_static/bin/python}
plan=${HICAR_BASELINE_OVERLAP_PLAN:?Set the published overlap forcing plan}
static_file=${HICAR_STATIC_FILE:?Set the summer REA-L static file}
continuous_run=${HICAR_BASELINE_CONTINUOUS_RUN:?Set completed summer run}
run=${HICAR_BASELINE_OVERLAP_RUN:?Set new immutable overlap run}
dry_run=${HICAR_BASELINE_DRY_RUN:-0}

contract="$transition_dir/baseline_transition_assessment_contract.json"
candidate_plan="$transition_dir/scientific_pilot_plan_candidate.json"
runtime_manifest="$runtime_dir/runtime_manifest.json"
runner="$runtime_dir/run_rea_l_stream_chunk_balfrin.sbatch"
model_validator="$runtime_dir/validate_model_chunk.py"
restart_input_validator="$runtime_dir/validate_published_restart_input.py"
comparison_wrapper="$runtime_dir/compare_event_restart_trajectory_balfrin.sbatch"
comparator="$runtime_dir/compare_segmented_to_uninterrupted.py"
exe="$build/HICAR_gpu"
continuous_completion="$continuous_run/model_chunk_completion.json"
checkpoint_report="$continuous_run/scientific_validation/restart_checkpoints/checkpoint_048h.json"
static_basename=$(basename "${static_file%.nc}")
checkpoint="$continuous_run/restart/${static_basename}_2020-07-03_00-00-00.nc"
trajectory_report="$run/restart_trajectory_comparison.json"
receipt="$transition_dir/summer_overlap_submission_receipt.json"

case "$dry_run" in
  0|false|FALSE) dry_run=0 ;;
  1|true|TRUE) dry_run=1 ;;
  *) echo "HICAR_BASELINE_DRY_RUN must be true/false or 1/0" >&2; exit 2 ;;
esac

for path in \
  "$contract" "$contract.ready" "$candidate_plan" "$candidate_plan.ready" \
  "$runtime_manifest" "$runtime_manifest.ready" "$source_root" "$exe" \
  "$plan" "$plan.ready" "$static_file" "$static_file.ready" \
  "$continuous_completion" "$continuous_completion.ready" \
  "$checkpoint_report" "$checkpoint_report.ready" "$checkpoint" "$python" \
  "$runner" "$model_validator" "$restart_input_validator" \
  "$comparison_wrapper" "$comparator"; do
  test -e "$path" || { echo "missing overlap input: $path" >&2; exit 2; }
done

"$python" - \
  "$contract" "$runtime_manifest" "$candidate" "$source_root" "$exe" \
  "$expected_executable_sha" "$plan" "$static_file" "$continuous_run" \
  "$continuous_completion" "$checkpoint_report" "$checkpoint" <<'PY'
import hashlib
import json
import pathlib
import subprocess
import sys

contract_path = pathlib.Path(sys.argv[1])
runtime_manifest_path = pathlib.Path(sys.argv[2])
candidate = sys.argv[3]
source_root = pathlib.Path(sys.argv[4])
executable = pathlib.Path(sys.argv[5])
expected_executable_sha = sys.argv[6]
plan_path = pathlib.Path(sys.argv[7])
static_file = pathlib.Path(sys.argv[8])
continuous_run = pathlib.Path(sys.argv[9])
completion_path = pathlib.Path(sys.argv[10])
checkpoint_report_path = pathlib.Path(sys.argv[11])
checkpoint = pathlib.Path(sys.argv[12])


def digest(path: pathlib.Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


contract = json.loads(contract_path.read_text())
runtime = json.loads(runtime_manifest_path.read_text())
plan = json.loads(plan_path.read_text())
completion = json.loads(completion_path.read_text())
checkpoint_report = json.loads(checkpoint_report_path.read_text())
if contract.get("status") != "FROZEN":
    raise SystemExit("transition assessment contract is not FROZEN")
if contract.get("candidate_commit") != candidate:
    raise SystemExit("candidate differs from transition contract")
if contract.get("candidate_executable_sha256") != expected_executable_sha:
    raise SystemExit("executable differs from transition contract")
if digest(executable) != expected_executable_sha:
    raise SystemExit("candidate executable checksum changed")
head = subprocess.check_output(
    ["git", "-C", str(source_root), "rev-parse", "HEAD"], text=True
).strip()
if head != candidate:
    raise SystemExit("candidate source worktree has the wrong commit")
if subprocess.check_output(
    ["git", "-C", str(source_root), "status", "--porcelain", "--untracked-files=no"],
    text=True,
):
    raise SystemExit("candidate source worktree is not clean")
if runtime.get("status") != "PASS":
    raise SystemExit("overlap runtime manifest is not PASS")
for item in runtime.get("files", []):
    path = pathlib.Path(item["path"])
    if not path.is_file() or digest(path) != item["sha256"]:
        raise SystemExit(f"overlap runtime changed: {path}")

if plan.get("status") != "PLANNED":
    raise SystemExit("overlap forcing plan is not PLANNED")
if (
    plan.get("start") != "2020-07-03T00:00:00"
    or plan.get("end") != "2020-07-04T00:00:00"
    or plan.get("hours") != 24
    or plan.get("record_count") != 25
):
    raise SystemExit("overlap forcing plan has the wrong period")
for record in plan.get("records", []):
    payload = pathlib.Path(record["forcing_file"])
    ready = pathlib.Path(record["ready_marker"])
    if not payload.is_file() or not ready.is_file():
        raise SystemExit(f"overlap forcing is not published: {payload}")

if completion.get("status") != "PASS":
    raise SystemExit("continuous summer completion is not PASS")
provenance = completion.get("provenance", {})
if (
    provenance.get("status") != "PASS"
    or provenance.get("source_commit") != candidate
    or provenance.get("executable_sha256") != expected_executable_sha
    or provenance.get("static_sha256")
    != contract["frozen_inputs"]["summer_static_sha256"]
):
    raise SystemExit("continuous summer provenance does not match transition")

for relative in contract["required_event_reports"]:
    path = continuous_run / relative
    if not path.is_file() or not pathlib.Path(f"{path}.ready").is_file():
        raise SystemExit(f"summer validation is incomplete: {path}")
    if json.loads(path.read_text()).get("status") != "PASS":
        raise SystemExit(f"summer validation is not PASS: {path}")
physical = json.loads(
    (
        continuous_run
        / "scientific_validation"
        / "scientific_event_diagnostics.json"
    ).read_text()
)
water_gate = contract["water_budget_gate"]
water = physical.get("water_budget_contract", {})
if (
    water.get("mode") != water_gate["mode"]
    or water.get("production_eligible") is not True
):
    raise SystemExit("summer water budget is not production eligible")
residual = (
    physical.get("classes", {})
    .get(water_gate["gate_class"], {})
    .get("water_diagnostic_kg_m2", {})
    .get("residual")
)
if residual is None or abs(float(residual)) > float(
    water_gate["maximum_absolute_residual_kg_m2_per_event"]
):
    raise SystemExit("summer water residual fails the frozen transition gate")

if checkpoint_report.get("status") != "PASS":
    raise SystemExit("48-hour checkpoint inventory is not PASS")
if checkpoint_report.get("checkpoint_time") != "2020-07-03T00:00:00":
    raise SystemExit("checkpoint inventory has the wrong time")
if checkpoint_report.get("expected_source_commit") != candidate:
    raise SystemExit("checkpoint inventory has the wrong source commit")
if pathlib.Path(checkpoint_report.get("checkpoint", "")).resolve() != checkpoint.resolve():
    raise SystemExit("checkpoint inventory has the wrong payload")
if digest(checkpoint) != checkpoint_report.get("sha256"):
    raise SystemExit("48-hour checkpoint checksum changed")
if digest(static_file) != contract["frozen_inputs"]["summer_static_sha256"]:
    raise SystemExit("summer static checksum changed")
PY

if test -e "$receipt" || test -e "$receipt.ready"; then
  echo "overlap submission receipt already exists: $receipt" >&2
  exit 2
fi
if test -e "$run"; then
  echo "overlap run already exists: $run" >&2
  exit 2
fi
mkdir -p "$run"

model_exports="ALL,STREAM_PLAN=$plan,HICAR_MULTILEVEL_ROOT=$source_root"
model_exports+=",HICAR_MULTILEVEL_BUILD=$build,HICAR_SWISS_CASE=$case_root"
model_exports+=",HICAR_STATIC_FILE=$static_file,HICAR_STREAM_VALIDATOR=$model_validator"
model_exports+=",HICAR_VALIDATION_PYTHON=$python,STREAM_RUN_DIR=$run"
model_exports+=",STREAM_RESTART_DIR=$run/restart,STREAM_OUTPUT_PROFILE=qualification"
model_exports+=",STREAM_OUTPUT_INTERVAL=10800,STREAM_RESTART_INTERVAL_RECORDS=8"
model_exports+=",STREAM_REA_L_LAND_INITIALIZATION=0,HICAR_EXPECTED_COMMIT=$candidate"
model_exports+=",STREAM_RESTART_FROM=2020-07-03T00:00:00"
model_exports+=",STREAM_RESTART_INPUT_FILE=$checkpoint"
model_exports+=",STREAM_RESTART_INPUT_REPORT=$checkpoint_report"
model_exports+=",HICAR_RESTART_INPUT_VALIDATOR=$restart_input_validator"

if test "$dry_run" -eq 1; then
  rmdir "$run"
  echo "PASS: transition-only summer overlap submission preflight"
  exit 0
fi

model_job=$(
  sbatch --parsable --job-name=hicar-v29-overlap \
    --output="$run/slurm_%j.out" --export="$model_exports" "$runner"
)
model_job=${model_job%%;*}

comparison_exports="ALL,REPO_ROOT=$SCRATCH/icon_hicar"
comparison_exports+=",HICAR_VALIDATION_PYTHON=$python"
comparison_exports+=",HICAR_RESTART_TRAJECTORY_COMPARATOR=$comparator"
comparison_exports+=",SCIENTIFIC_PILOT_PLAN=$candidate_plan"
comparison_exports+=",RESTARTED_COMPLETION=$run/model_chunk_completion.json"
comparison_exports+=",CONTINUOUS_COMPLETION=$continuous_completion"
comparison_exports+=",TRAJECTORY_START=2020-07-03T00:00:00"
comparison_exports+=",TRAJECTORY_END=2020-07-04T00:00:00"
comparison_exports+=",EVENT_RESTART_TRAJECTORY_REPORT=$trajectory_report"
comparison_job=$(
  sbatch --parsable --dependency="afterok:$model_job" \
    --output="$run/trajectory_%j.out" --export="$comparison_exports" \
    "$comparison_wrapper"
)
comparison_job=${comparison_job%%;*}

"$python" - "$receipt" "$candidate" "$run" "$runtime_manifest" \
  "$model_job" "$comparison_job" <<'PY'
import json
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile

receipt, candidate, run, runtime_manifest, model_job, comparison_job = sys.argv[1:]
payload = {
    "schema_version": 1,
    "status": "SUBMITTED",
    "classification": "SCIENTIFIC_BASELINE_TRANSITION_OVERLAP_ONLY",
    "candidate_commit": candidate,
    "run_dir": run,
    "runtime_manifest": runtime_manifest,
    "jobs": {"model": model_job, "trajectory_comparison": comparison_job},
    "authorization": {
        "month_compute": False,
        "annual_cycle": False,
        "twenty_year_200m_production": False,
    },
}
path = Path(receipt)
with NamedTemporaryFile("w", dir=path.parent, delete=False) as stream:
    json.dump(payload, stream, indent=2, sort_keys=True)
    stream.write("\n")
    temporary = Path(stream.name)
os.replace(temporary, path)
Path(f"{path}.ready").touch()
PY

echo "submitted summer overlap: model=$model_job comparison=$comparison_job"
