#!/bin/bash
# Production-physics gate: build a representative 10 km real-ICON domain and
# run one simulated hour using the CPU executable and one I/O rank.
#SBATCH --job-name=hicar_prod_cpu_smoke
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=2
#SBATCH --cpus-per-task=1
#SBATCH --time=01:00:00
#SBATCH --output=%x-%j.out
#SBATCH --error=%x-%j.err
set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"; module purge || true; module use "$USER_ENV_ROOT/modules"
module load python/3.11.7 gcc/12.3.0 cray-mpich-gcc/8.1.30 netcdf-c/4.8.1-gcc netcdf-fortran/4.5.4-gcc fftw/3.3.10-gcc
ROOT=${SCALING_ROOT:?}; SRC=${HICAR_WORKSPACE:-$SCRATCH/icon_hicar}; HICAR_ROOT=${HICAR_ROOT:-$SCRATCH/icon_hicar/HICAR-scaling}
PYTHON=${STATIC_PYTHON:-$SCRATCH/icon_hicar/venv_static/bin/python}; test -x "$PYTHON"
export PATH="$(dirname "$PYTHON"):$PATH"
ICON_ROOT=${ICON_ROOT:-$SCRATCH/icon_hicar/data/icon_ch1_eps_20260710T18_ctrl_oper_icon_000}
DOMAIN="$ROOT/domains/10x10km"; RUN="$ROOT/runs/weak_cpu_10x10_p1/repeat_1"
rm -rf "$DOMAIN" "$RUN/output" "$RUN/restart"
mkdir -p "$DOMAIN/static" "$DOMAIN/forcing" "$RUN/output" "$RUN/restart" "$RUN/logs"
"$PYTHON" "$SRC/scripts/prepare_static_inputs.py" --output "$DOMAIN/static/domain_static_unrelaxed.nc" --center-lat 46.75 --center-lon 8.15 --width-km 10 --height-km 10 --dx-m 250 --public-sources --static-field-set land-surface
for hour in $(seq 0 8); do
  native=$(printf 'lfff%04d0000' "$hour"); forcing=$(printf 'hicar_forcing_f%03d.nc' "$hour")
  "$SRC/scripts/prepare_icon_inputs.sh" --input "$ICON_ROOT/$native" --static "$ICON_ROOT/lfff00000000c" --output "$DOMAIN/forcing/$forcing" --domain-file "$DOMAIN/static/domain_static_unrelaxed.nc" --load-balfrin-modules
done
"$PYTHON" "$SRC/scripts/prepare_static_inputs.py" --output "$DOMAIN/static/domain_static_relaxed.nc" --center-lat 46.75 --center-lon 8.15 --width-km 10 --height-km 10 --dx-m 250 --public-sources --static-field-set land-surface --boundary-topo-source "$DOMAIN/forcing/hicar_forcing_f000.nc" --write-topo-blend-diagnostics
for hour in $(seq 0 8); do
  printf '"%s/forcing/hicar_forcing_f%03d.nc"\n' "$DOMAIN" "$hour"
done > "$DOMAIN/forcing_file_list.txt"
test -f "$DOMAIN/static/domain_static_relaxed.nc.ready"; test -f "$DOMAIN/forcing/hicar_forcing_f008.nc.ready"
test -x "$HICAR_ROOT/bin/HICAR_release"; test -f "$HICAR_ROOT/run/NoahmpTable.TBL"; test -d "$HICAR_ROOT/run/rrtmgp_support"; test -d "$HICAR_ROOT/run/mp_support"
cp -f "$HICAR_ROOT/run/NoahmpTable.TBL" "$RUN/input/"
rm -rf "$RUN/input/rrtmgp_support" "$RUN/input/mp_support"
cp -a "$HICAR_ROOT/run/rrtmgp_support" "$RUN/input/"; cp -a "$HICAR_ROOT/run/mp_support" "$RUN/input/"
export HICAR_IO_PER_NODE=1 OMP_NUM_THREADS=1
cd "$RUN/input"
srun -u --cpu-bind=cores "$HICAR_ROOT/bin/HICAR_release" "$RUN/input/run.nml" | tee "$RUN/logs/slurm_${SLURM_JOB_ID}.out"
python3 "$ROOT/analysis/validate_run.py" --run "$RUN" --expected-hours 1
touch "$ROOT/provenance/production_cpu_physics_READY"
