#!/usr/bin/env bash
# Run one full production-configured CPU timestep for every prepared domain.
set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"; module purge || true; module use "$USER_ENV_ROOT/modules"
module load python/3.11.7 gcc/12.3.0 cray-mpich-gcc/8.1.30 netcdf-c/4.8.1-gcc netcdf-fortran/4.5.4-gcc fftw/3.3.10-gcc
ROOT=${SCALING_ROOT:?}; HICAR_ROOT=${HICAR_ROOT:-$SCRATCH/icon_hicar/HICAR-scaling}
test -f "$ROOT/provenance/cpu_READY"
find "$ROOT/domains" -mindepth 1 -maxdepth 1 -type d | sort | while read -r domain; do
  test -f "$domain/PREFLIGHT_OK" && continue
  name=$(basename "$domain")
  nml=$(find "$ROOT/runs" -path "*_${name%km}_p*/repeat_1/input/run.nml" -print -quit)
  test -n "$nml" || { echo "no scenario template for $name" >&2; exit 2; }
  work="$domain/preflight"; rm -rf "$work"; mkdir -p "$work/output" "$work/restart"
  test -f "$HICAR_ROOT/run/NoahmpTable.TBL" || { echo "missing NoahMP support table" >&2; exit 3; }
  test -d "$HICAR_ROOT/run/rrtmgp_support" || { echo "missing RRTMGP support data" >&2; exit 4; }
  test -d "$HICAR_ROOT/run/mp_support" || { echo "missing microphysics support data" >&2; exit 5; }
  cp -f "$HICAR_ROOT/run/NoahmpTable.TBL" "$work/"
  cp -a "$HICAR_ROOT/run/rrtmgp_support" "$work/"
  cp -a "$HICAR_ROOT/run/mp_support" "$work/"
  sed -e "s/end_date = '2026-07-11 00:00:00'/end_date = '2026-07-10 18:00:05'/" -e "s#output_folder = '.*'#output_folder = '$work/output/'#" -e "s#restart_folder = '.*'#restart_folder = '$work/restart/'#" "$nml" > "$work/preflight.nml"
  export HICAR_IO_PER_NODE=1 OMP_NUM_THREADS=1
  ( cd "$work"; srun -n 13 --input=/dev/null --cpu-bind=cores "$HICAR_ROOT/bin/HICAR_release" "$work/preflight.nml" ) | tee "$work/preflight.out"
  grep -q 'Timing across all compute images:' "$work/preflight.out"
  ! grep -Eq 'HICAR BiCGStab status=[[:space:]]*[1-9]' "$work/preflight.out"
  awk '/^[[:space:]]*physics:/ && $NF > 0 { ok=1 } END { exit !ok }' "$work/preflight.out"
  test -n "$(find "$work/output" -name '*.nc' -print -quit)"
  touch "$domain/PREFLIGHT_OK"
done
