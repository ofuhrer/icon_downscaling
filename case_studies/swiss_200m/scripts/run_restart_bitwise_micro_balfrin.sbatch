#!/bin/bash -l
# Cheap whole-model restart exactness screen on HICAR's bundled Alpine case.
#
# This is deliberately smaller than the Swiss bridge/national gates.  It runs
# ten minutes continuously, restarts at five minutes, and requires the final
# history state to be bitwise identical.  The five-minute boundary exercises
# an output/restart event and the standard-case land-surface cadence.
#
# Required submission variables:
#   HICAR_SOURCE_ROOT      clean source at HICAR_EXPECTED_COMMIT
#   HICAR_BUILD_ROOT       successful canonical CPU build
#   HICAR_EXPECTED_COMMIT  exact source identity
#   HICAR_MICRO_REPORT     output JSON report path
#SBATCH --job-name=hicar-rst-bitwise
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=21
#SBATCH --cpus-per-task=1
#SBATCH --time=01:00:00
#SBATCH --output=hicar_restart_bitwise_%j.out
#SBATCH --error=hicar_restart_bitwise_%j.err

set -euo pipefail

[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module purge || true
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module load gcc/12.3.0 cray-mpich-gcc/8.1.30 \
  netcdf-c/4.8.1-gcc netcdf-fortran/4.5.4-gcc \
  fftw/3.3.10-gcc openblas/0.3.26-gcc python/3.11.7

source_root=${HICAR_SOURCE_ROOT:?Set HICAR_SOURCE_ROOT}
build_root=${HICAR_BUILD_ROOT:?Set HICAR_BUILD_ROOT}
expected_commit=${HICAR_EXPECTED_COMMIT:?Set HICAR_EXPECTED_COMMIT}
report=${HICAR_MICRO_REPORT:?Set HICAR_MICRO_REPORT}
executable="$build_root/HICAR"
tester="$build_root/tests/HICAR-tester"
provenance="$build_root/hicar_build_provenance.txt"

test "$(git -C "$source_root" rev-parse HEAD)" = "$expected_commit"
git -C "$source_root" diff --quiet
git -C "$source_root" diff --cached --quiet
test -x "$executable"
test -x "$tester"
test -f "$provenance"
grep -qx "source_commit=$expected_commit" "$provenance"

mkdir -p "$source_root/bin" "$(dirname "$report")"
ln -sfn "$executable" "$source_root/bin/HICAR_debug"

export OMP_NUM_THREADS=1
export PYTHON=python

unit_log="${report%.json}.unit_test.log"
tester_cwd="$source_root/.tester-cwd"
mkdir -p "$tester_cwd"
(
  # Several legacy test cases open ../tests/Test_Cases at runtime.  Keep the
  # canonical out-of-source build while launching one directory below the
  # source root so that relative path resolves to the fetched test data.
  cd "$tester_cwd"
  srun --nodes=1 --ntasks=4 "$tester" all
) >"$unit_log" 2>&1
test "$(grep -c '\[PASSED\]' "$unit_log")" -eq 49
! grep -Eq '\[FAILED\]|^STOP ' "$unit_log"

# A cancelled first-time dependency installation may leave an executable but
# incomplete virtual environment.  Quarantine that derived cache so HICAR's
# test runner creates a fresh one; never accept it based on bin/python alone.
test_venv="$source_root/tests/Test_Cases/venv"
if [[ -x "$test_venv/bin/python" ]] && \
   ! "$test_venv/bin/python" -c 'import numpy, netCDF4, xarray, matplotlib, yaml'
then
  mv "$test_venv" "${test_venv}.invalid.${SLURM_JOB_ID}"
fi

test_log="${report%.json}.model_test.log"
(
  cd "$source_root"
  bash tests/scripts/test_reproducibility.sh . restart
) >"$test_log" 2>&1

case_name=Gaudergrat_250m_2017-02-14_00-00-00.nc
continuous="$source_root/tests/Test_Cases/output/Repro_Restart_continuous_backup/$case_name"
restarted="$source_root/tests/Test_Cases/output/Repro_Restart/$case_name"
test -f "$continuous"
test -f "$restarted"

python_exe="$source_root/tests/Test_Cases/venv/bin/python"
test -x "$python_exe"
exact_log="${report%.json}.exact_compare.log"
set +e
"$python_exe" "$source_root/tests/scripts/compare_outputs.py" \
  "$continuous" "$restarted" \
  --tolerance 0.0 \
  --last-timestep-only >"$exact_log" 2>&1
exact_status=$?
set -e

tmp_report="${report}.tmp.$$"
python - "$tmp_report" "$expected_commit" "$executable" "$tester" "$provenance" \
  "$continuous" "$restarted" "$unit_log" "$test_log" "$exact_log" \
  "$exact_status" <<'PY'
import hashlib
import json
import pathlib
import re
import sys

(
    output,
    commit,
    executable,
    tester,
    provenance,
    continuous,
    restarted,
    unit_log,
    test_log,
    exact_log,
    exact_status,
) = sys.argv[1:]


def identity(path):
    path = pathlib.Path(path)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": digest.hexdigest(),
    }


ansi = re.compile(r"\x1b\[[0-9;]*m")
comparison = ansi.sub("", pathlib.Path(exact_log).read_text(errors="replace"))
different = []
for line in comparison.splitlines():
    match = re.match(r"\s*(\S.*?)\s*:\s*DIFF\b", line)
    if match:
        different.append(match.group(1).strip())

status = "PASS" if int(exact_status) == 0 else "FAIL"
payload = {
    "schema": "hicar_restart_bitwise_micro_v1",
    "status": status,
    "scope": (
        "Ten-minute bundled Alpine whole-model screen; five-minute restart; "
        "bitwise final history-state comparison."
    ),
    "hicar_commit": commit,
    "executable": identity(executable),
    "tester": identity(tester),
    "build_provenance": identity(provenance),
    "continuous_output": identity(continuous),
    "restarted_output": identity(restarted),
    "unit_test_log": identity(unit_log),
    "model_test_log": identity(test_log),
    "exact_compare_log": identity(exact_log),
    "exact_compare_exit_code": int(exact_status),
    "different_variables": different,
    "different_variable_count": len(different),
    "limitations": [
        "This is not a Swiss-national or production-cadence qualification.",
        "Restart-only fields require the later endpoint-restart comparison gate.",
    ],
}
pathlib.Path(output).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
PY

mv "$tmp_report" "$report"
sha256sum "$report" >"${report}.sha256"
if [[ "$exact_status" -eq 0 ]]; then
  touch "${report}.ready"
  printf 'PASS report=%s\n' "$report"
  exit 0
fi

rm -f "${report}.ready"
printf 'FAIL report=%s\n' "$report"
exit 1
