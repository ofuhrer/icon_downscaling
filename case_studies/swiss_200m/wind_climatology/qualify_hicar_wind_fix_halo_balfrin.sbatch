#!/bin/bash -l
#SBATCH --job-name=hicar-wind-fix-halo
#SBATCH --partition=debug
#SBATCH --nodes=1
#SBATCH --ntasks=4
#SBATCH --ntasks-per-node=4
#SBATCH --cpus-per-task=1
#SBATCH --gres=gpu:4
#SBATCH --exclusive
#SBATCH --time=00:20:00

set -euo pipefail

: "${HICAR_TESTER:?set HICAR_TESTER}"
: "${HICAR_EXPECTED_COMMIT:?set HICAR_EXPECTED_COMMIT}"
: "${HICAR_HALO_LOG:?set HICAR_HALO_LOG}"
: "${HICAR_HALO_REPORT:?set HICAR_HALO_REPORT}"

[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module purge || true
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module load nvhpc/24.5 cray-mpich-nvhpc/8.1.30 cuda/12.3.0-gcc \
  netcdf-c/4.9.2-nvhpc netcdf-fortran/4.6.1-nvhpc \
  hdf5/1.14.3-nvhpc fftw/3.3.10-gcc

test -x "${HICAR_TESTER}"
test ! -e "${HICAR_HALO_REPORT}"
test ! -e "${HICAR_HALO_REPORT}.ready"

srun --distribution=block:block --cpu-bind=none \
  bash -c 'export CUDA_VISIBLE_DEVICES="${SLURM_LOCALID}"; export MPICH_GPU_SUPPORT_ENABLED=0; export MPICH_GPU_MANAGED_MEMORY_SUPPORT_ENABLED=0; exec "$0" -v halo_exch' \
  "${HICAR_TESTER}" 2>&1 | tee "${HICAR_HALO_LOG}"

python3 - \
  "${HICAR_TESTER}" \
  "${HICAR_EXPECTED_COMMIT}" \
  "${HICAR_HALO_LOG}" \
  "${HICAR_HALO_REPORT}" \
  "${SLURM_JOB_ID}" <<'PY'
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

tester, commit, log, output, job_id = sys.argv[1:]


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


payload = {
    "schema_version": 1,
    "status": "PASS",
    "purpose": "four-rank-four-gpu-horizontal-wind-fix-halo-qualification",
    "job_id": job_id,
    "source_commit": commit,
    "tester": str(Path(tester).resolve()),
    "tester_sha256": sha256(tester),
    "log": str(Path(log).resolve()),
    "log_sha256": sha256(log),
    "transport": "NCCL build; MPICH GPU support disabled; one visible GPU per rank",
}
output = Path(output)
output.parent.mkdir(parents=True, exist_ok=True)
fd, temporary = tempfile.mkstemp(dir=str(output.parent), prefix=".halo-report.")
with os.fdopen(fd, "w") as stream:
    json.dump(payload, stream, indent=2, sort_keys=True)
    stream.write("\n")
os.rename(temporary, str(output))
Path(str(output) + ".ready").touch()
PY
