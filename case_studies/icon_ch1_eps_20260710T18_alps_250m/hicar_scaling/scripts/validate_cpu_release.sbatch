#!/bin/bash
#SBATCH --job-name=hicar_scaling_cpu_smoke
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=5
#SBATCH --cpus-per-task=1
#SBATCH --time=00:25:00
set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"; module purge || true; module use "$USER_ENV_ROOT/modules"
module load gcc/12.3.0 cray-mpich-gcc/8.1.30 netcdf-c/4.8.1-gcc netcdf-fortran/4.5.4-gcc fftw/3.3.10-gcc
ROOT=${SCALING_ROOT:?}; HICAR_ROOT=${HICAR_ROOT:-$SCRATCH/icon_hicar/HICAR-scaling}
CASE_ROOT=$(dirname "$ROOT")
NML="$SCRATCH/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_ready_smoke/input/icon_hicar_250m_ready_smoke.nml"
test -x "$HICAR_ROOT/bin/HICAR_release"; test -f "$NML"
export HICAR_IO_PER_NODE=1 OMP_NUM_THREADS=1
rm -rf "$ROOT/provenance/cpu_smoke_output" "$ROOT/provenance/cpu_smoke_restart"
mkdir -p "$ROOT/provenance/cpu_smoke_output" "$ROOT/provenance/cpu_smoke_restart"
sed -e "s#${CASE_ROOT}/hicar_ready_smoke/restart/#${ROOT}/provenance/cpu_smoke_restart/#" -e "s#${CASE_ROOT}/hicar_ready_smoke/output/#${ROOT}/provenance/cpu_smoke_output/#" "$NML" > "$ROOT/provenance/cpu_smoke.nml"
srun -u --cpu-bind=cores "$HICAR_ROOT/bin/HICAR_release" "$ROOT/provenance/cpu_smoke.nml" | tee "$ROOT/provenance/cpu_smoke.out"
grep -q 'Timing across all compute images:' "$ROOT/provenance/cpu_smoke.out"
test -n "$(find "$ROOT/provenance/cpu_smoke_output" -name '*.nc' -print -quit)"
touch "$ROOT/provenance/cpu_READY"
