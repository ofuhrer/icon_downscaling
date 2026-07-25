#!/usr/bin/env bash
set -eo pipefail

# Prepare a minimal HICAR smoke run on Balfrin using ICON-derived forcing.
#
# Prerequisites:
# - HICAR is cloned and built at $SCRATCH/icon_hicar/HICAR.
# - Two packaged forcing files exist under $RUN_ROOT/forcing:
#     hicar_forcing_f000.nc
#     hicar_forcing_f001.nc
#
# Output:
# - $RUN_ROOT/domain/icon_smoke_domain.nc
# - $RUN_ROOT/input/forcing_file_list.txt
# - $RUN_ROOT/input/icon_hicar_minimal.nml
# - $RUN_ROOT/run_hicar_smoke.sbatch

remote_env=()
for name in RUN_ROOT HICAR_ROOT; do
  if [ "${!name+x}" ]; then
    remote_env+=("$name=${!name}")
  fi
done

ssh -o BatchMode=yes -o ConnectTimeout=10 balfrin env "${remote_env[@]}" 'bash -s' <<'REMOTE'
set -eo pipefail

[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"
module purge || true
module use "$USER_ENV_ROOT/modules"
module load gcc/12.3.0 \
  cray-mpich-gcc/8.1.30 \
  netcdf-c/4.8.1-gcc \
  netcdf-fortran/4.5.4-gcc \
  fftw/3.3.10-gcc \
  nco/5.0.1-gcc >/dev/null

RUN_ROOT="${RUN_ROOT:-$SCRATCH/icon_hicar/hicar_smoke_runs/icon_20260710T18_rotlatlon_0p02deg_minimal}"
HICAR_ROOT="${HICAR_ROOT:-$SCRATCH/icon_hicar/HICAR}"

FORCING_DIR="$RUN_ROOT/forcing"
DOMAIN_DIR="$RUN_ROOT/domain"
INPUT_DIR="$RUN_ROOT/input"
OUTPUT_DIR="$RUN_ROOT/output"
RESTART_DIR="$RUN_ROOT/restart"
LOG_DIR="$RUN_ROOT/logs"

mkdir -p "$DOMAIN_DIR" "$INPUT_DIR" "$OUTPUT_DIR" "$RESTART_DIR" "$LOG_DIR"

F000="$FORCING_DIR/hicar_forcing_f000.nc"
F001="$FORCING_DIR/hicar_forcing_f001.nc"
DOMAIN="$DOMAIN_DIR/icon_smoke_domain.nc"
FILE_LIST="$INPUT_DIR/forcing_file_list.txt"
NML="$INPUT_DIR/icon_hicar_minimal.nml"
SBATCH="$RUN_ROOT/run_hicar_smoke.sbatch"

for path in "$HICAR_ROOT/bin/HICAR_debug" "$F000" "$F001"; do
  if [ ! -e "$path" ]; then
    echo "missing required path: $path" >&2
    exit 2
  fi
done

echo "== HICAR smoke input preparation =="
printf 'host=%s\n' "$(hostname)"
printf 'run_root=%s\n' "$RUN_ROOT"
printf 'hicar=%s\n' "$HICAR_ROOT/bin/HICAR_debug"

echo
echo "== forcing times =="
ncks -H -C -v time "$F000" | sed -n '1,20p'
ncks -H -C -v time "$F001" | sed -n '1,20p'

echo
echo "== domain file =="
tmp_domain="$DOMAIN_DIR/.icon_smoke_domain_tmp.nc"
ncks -3 -O -v lat_1,lon_1,HSURF,FR_LAND "$F000" "$tmp_domain"
ncrename -O \
  -v lat_1,lat \
  -v lon_1,lon \
  -v HSURF,topo \
  -v FR_LAND,land_fraction \
  "$tmp_domain"
ncap2 -O -s 'landmask=land_fraction; where(landmask > 0.5f) landmask=1.0f; where(landmask <= 0.5f) landmask=0.0f; landuse=landmask*7.0f; where(landuse == 0.0f) landuse=16.0f;' \
  "$tmp_domain" "$DOMAIN"
ncatted -O \
  -a long_name,topo,o,c,"terrain height" -a units,topo,o,c,"m" \
  -a long_name,landmask,o,c,"land mask" -a units,landmask,o,c,"1" \
  -a long_name,landuse,o,c,"synthetic USGS land-use category" -a units,landuse,o,c,"1" \
  "$DOMAIN"
rm -f "$tmp_domain"
ncks -m "$DOMAIN" | sed -n '1,120p'

echo
echo "== forcing file list =="
{
  printf '"%s"\n' "$F000"
  printf '"%s"\n' "$F001"
} > "$FILE_LIST"
sed -n '1,20p' "$FILE_LIST"

echo
echo "== namelist =="
cat > "$NML" <<EOF
&general
  start_date = '2026-07-10 18:00:00'
  end_date = '2026-07-10 19:00:00'
/

&restart
  restart_folder = '$RESTART_DIR/'
/

&domain
  init_conditions_file = '$DOMAIN'
  dx = 2200.0
  nz = 40
  lat_hi = 'lat'
  lon_hi = 'lon'
  hgt_hi = 'topo'
  landvar = 'landmask'
  use_map_factors = .True.
  auto_level = 3
  model_top_height = 20000.0
  stretch_fac = 1.0
  decay_rate_L_topo = 1.0
  decay_rate_S_topo = 1.0
/

&forcing
  forcing_file_list = '$FILE_LIST'
  inputinterval = 3600
  time_var = 'time'
  pvar = 'P'
  tvar = 'T'
  qvvar = 'QV'
  uvar = 'U'
  vvar = 'V'
  hgtvar = 'HSURF'
  zvar = 'HFL'
  latvar = 'lat_1'
  lonvar = 'lon_1'
  qv_is_spec_humidity = .True.
  t_is_potential = .False.
/

&physics
  wind = 'none'
  mp = 'morrison'
  pbl = 'none'
  lsm = 'none'
  sfc = 'none'
  water = 'none'
  rad = 'none'
/

&time_parameters
  ! RK3 is required for the intended HICAR advection path. The local HICAR
  ! patch transports theta_bar in flux form within each RK3 stage instead of
  ! using the unstable separate pointwise vertical-transport correction.
  RK3 = .True.
  cfl_reduction_factor = 1.6
/

&lt_parameters
/

&mp_parameters
/

&adv_parameters
  h_order = 3
  v_order = 3
  flux_corr = 1
  cz_diff_order = 0
/

&sm_parameters
/

&lsm_parameters
/

&cu_parameters
/

&rad_parameters
/

&pbl_parameters
/

&sfc_parameters
/

&wind
/

&output
  output_folder = '$OUTPUT_DIR/'
  outputinterval = 3600
  output_vars = 'qv','temperature','precipitation','u','v','w'
/
EOF
sed -n '1,220p' "$NML"

echo
echo "== check namelist =="
cd "$HICAR_ROOT"
"$HICAR_ROOT/bin/HICAR_debug" --check-nml "$NML" > "$LOG_DIR/check_nml.out" 2> "$LOG_DIR/check_nml.err"
sed -n '1,160p' "$LOG_DIR/check_nml.out"
sed -n '1,160p' "$LOG_DIR/check_nml.err"

echo
echo "== slurm script =="
cat > "$SBATCH" <<EOF
#!/bin/bash
#SBATCH --job-name=hicar_icon_smoke
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=5
#SBATCH --cpus-per-task=1
#SBATCH --time=00:20:00
#SBATCH --output=$LOG_DIR/hicar_icon_smoke_%j.out
#SBATCH --error=$LOG_DIR/hicar_icon_smoke_%j.err

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "\$USER_ENV_ROOT/modules"
module purge || true
module use "\$USER_ENV_ROOT/modules"
module load gcc/12.3.0 \\
  cray-mpich-gcc/8.1.30 \\
  netcdf-c/4.8.1-gcc \\
  netcdf-fortran/4.5.4-gcc \\
  fftw/3.3.10-gcc

export HICAR_IO_PER_NODE=1
cd "$HICAR_ROOT"
srun -n "\$SLURM_NTASKS" "$HICAR_ROOT/bin/HICAR_debug" "$NML"
EOF
chmod +x "$SBATCH"
sed -n '1,180p' "$SBATCH"

echo
echo "== complete =="
printf 'domain=%s\n' "$DOMAIN"
printf 'forcing_file_list=%s\n' "$FILE_LIST"
printf 'namelist=%s\n' "$NML"
printf 'slurm=%s\n' "$SBATCH"
printf 'check_stdout=%s\n' "$LOG_DIR/check_nml.out"
printf 'check_stderr=%s\n' "$LOG_DIR/check_nml.err"
REMOTE
