#!/usr/bin/env bash
set -eo pipefail

# Package fieldextra full-column smoke output into a HICAR-oriented NetCDF file.
#
# Input defaults to:
#   $SCRATCH/icon_hicar/fieldextra_full_column_smoke/latest
#
# Output defaults to:
#   $SCRATCH/icon_hicar/forcing_smoke/<run-id>/hicar_forcing_f001.nc

remote_env=()
for name in SRC DYNAMIC_NC STATIC_NC RUN_ID WORK_PARENT OUT; do
  if [ "${!name+x}" ]; then
    remote_env+=("$name=${!name}")
  fi
done

ssh -o BatchMode=yes -o ConnectTimeout=10 balfrin env "${remote_env[@]}" 'bash -s' <<'REMOTE'
set -eo pipefail

[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"
module load nco/5.0.1-gcc netcdf-c/4.8.1-gcc >/dev/null

SRC="${SRC:-$SCRATCH/icon_hicar/fieldextra_full_column_smoke/latest}"
DYNAMIC_NC="${DYNAMIC_NC:-$SRC/icon_full_column_dynamic.nc}"
STATIC_NC="${STATIC_NC:-$SRC/icon_static_geometry.nc}"
RUN_ID="${RUN_ID:-$(date +%Y%m%dT%H%M%S)}"
WORK_PARENT="${WORK_PARENT:-$SCRATCH/icon_hicar/forcing_smoke}"
WORK="$WORK_PARENT/$RUN_ID"
OUT="${OUT:-$WORK/hicar_forcing_f001.nc}"

mkdir -p "$WORK"
mkdir -p "$(dirname "$OUT")"
cd "$WORK"

echo "== package HICAR forcing smoke =="
printf 'host=%s\n' "$(hostname)"
printf 'source_dynamic=%s\n' "$DYNAMIC_NC"
printf 'source_static=%s\n' "$STATIC_NC"
printf 'work=%s\n' "$WORK"
printf 'out=%s\n' "$OUT"

for path in "$DYNAMIC_NC" "$STATIC_NC"; do
  if [ ! -f "$path" ]; then
    echo "missing required NetCDF file: $path" >&2
    exit 2
  fi
done

cp "$DYNAMIC_NC" dynamic.nc
cp "$STATIC_NC" static.nc

# Drop the singleton ensemble dimension from the dynamic and static outputs.
ncwa -O -a epsd_1 dynamic.nc hicar_work.nc
ncwa -O -a epsd_1 static.nc static_noepsd.nc

# Append 2-D static fields.
ncks -A -v HSURF,FR_LAND static_noepsd.nc hicar_work.nc

# Preserve half-level height for parity with the public COSMO HICAR forcing.
# fieldextra names both static half-level HEIGHT and dynamic full-level fields
# with z_1 in separate files. Rename static HEIGHT to the dynamic half-level
# dimension z_2 before appending.
ncks -O -v HEIGHT static_noepsd.nc hhl_work.nc
ncrename -O -d z_1,z_2 -v z_1,z_2 -v HEIGHT,HHL hhl_work.nc
ncks -A -v HHL hhl_work.nc hicar_work.nc

# Derive full-level height from half-level HEIGHT:
# HFL(k) = 0.5 * (HEIGHT_half(k) + HEIGHT_half(k+1))
ncks -O -v HEIGHT static_noepsd.nc hfl_work.nc
ncap2 -O -s 'defdim("z_hicar",80); HFL[$z_hicar,$y_1,$x_1]=0.5f*(HEIGHT(0:79,:,:)+HEIGHT(1:80,:,:));' \
  hfl_work.nc hfl_work.nc
ncks -O -v HFL hfl_work.nc hfl_only.nc
ncrename -O -d z_hicar,z_1 hfl_only.nc
ncks -A hfl_only.nc hicar_work.nc

# ICON/fieldextra writes atmospheric levels top-to-bottom. HICAR's 4-D forcing
# reader does not reverse z, so package forcing bottom-to-top. Reverse both
# full-level z_1 and half-level z_2; then keep a simple z name for full levels
# while making half-level variables explicit as z_hl.
ncpdq -O -a time,-z_1,-z_2,y_1,x_1 hicar_work.nc hicar_zrev.nc
ncrename -O -d z_1,z -v z_1,z -d z_2,z_hl -v z_2,z_hl hicar_zrev.nc

# Remove stale metadata left by vertical reversal and singleton-dimension
# removal. HICAR uses variable-name mapping and array ordering; stale CF level
# attributes are more misleading than useful here.
ncatted -O \
  -a positive,z,d,, -a positive,z_hl,d,, \
  -a valid_max,z,d,, -a valid_max,z_hl,d,, \
  -a uid,z,d,, -a uid,z_hl,d,, \
  -a uuid,z,d,, -a uuid,z_hl,d,, \
  -a bounds,z,d,, \
  -a cell_methods,P,d,, -a cell_methods,QV,d,, -a cell_methods,T,d,, \
  -a cell_methods,U,d,, -a cell_methods,V,d,, \
  -a cell_methods,W,d,, -a cell_methods,HFL,d,, -a cell_methods,HHL,d,, \
  -a cell_methods,HSURF,d,, -a cell_methods,FR_LAND,d,, \
  -a long_name,HFL,o,c,"geometric height on full levels" -a units,HFL,o,c,"m" \
  -a long_name,HHL,o,c,"geometric height on half levels" -a units,HHL,o,c,"m" \
  hicar_zrev.nc

ncks -4 -L 0 -O -x -v epsd_1,z_bnds_1 hicar_zrev.nc "$OUT"

ln -sfn "$WORK" "$WORK_PARENT/latest"

echo
echo "== output header =="
ncdump -h "$OUT" | sed -n '1,220p'

echo
echo "== complete =="
printf 'work=%s\nout=%s\nlatest=%s\n' "$WORK" "$OUT" "$WORK_PARENT/latest"
REMOTE
