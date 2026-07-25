#!/bin/bash -l
# Regrid REA-L HSURF to the planned Switzerland forcing grid before static build.
# Submit manually, e.g.:
# sbatch --export=ALL,REA_DATE=20100101,REA_TIME=0000 scripts/regrid_rea_l_hsurf.sbatch
# The source bundle must already have its .ready marker.
#SBATCH --job-name=rea_l_hsurf_grid
#SBATCH --partition=pp-short
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --time=00:30:00
#SBATCH --output=%x_%j.out
#SBATCH --error=%x_%j.err

set -euo pipefail

: "${REA_DATE:?set REA_DATE as YYYYMMDD}"
: "${REA_TIME:?set REA_TIME as HHMM}"
[[ "$REA_DATE" =~ ^[0-9]{8}$ ]] || { echo "REA_DATE must be YYYYMMDD" >&2; exit 2; }
[[ "$REA_TIME" =~ ^[0-9]{4}$ ]] || { echo "REA_TIME must be HHMM" >&2; exit 2; }

[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"
module load nco/5.0.1-gcc netcdf-c/4.8.1-gcc >/dev/null

CASE_ROOT="${CASE_ROOT:-$SCRATCH/icon_hicar/case_studies/swiss_100m}"
SOURCE_DIR="$CASE_ROOT/source/rea_l_ch1/${REA_DATE}_${REA_TIME}"
STATIC_GRIB="$SOURCE_DIR/rea_l_ch1_${REA_DATE}_${REA_TIME}_static.grib"
OUT_DIR="$CASE_ROOT/static_boundary"
OUTPUT="$OUT_DIR/rea_l_hsurf_${REA_DATE}_${REA_TIME}.nc"
GRID_SCRIPT="$CASE_ROOT/validation/planned_fieldextra_grid.py"

FX_BIN="${FX_BIN:-/oprusers/osm/opr.inn/abs/fieldextra_gnu_opt_omp-16.0.0-gcc-12.3.0}"
FX_RES="${FX_RES:-/oprusers/osm/opr.inn/config/resources}"
FX_SAMPLE="${FX_SAMPLE:-/oprusers/osm/opr.inn/modules/eccodes_cosmo_resources/2.36.0.3/samples/COSMO_GRIB2_default.tmpl}"
ICON_GRID="${ICON_GRID:-/oprusers/osm/opr.inn/data/grid_descriptions/icon_grid_0001_R19B08_mch.nc}"
# fieldextra's localNumberOfExperiment is an integer metadata field; the
# archive expver r001 remains recorded in the immutable source manifest.
EXPVER="${EXPVER:-639}"

for path in "$STATIC_GRIB" "$STATIC_GRIB.ready" "$GRID_SCRIPT" "$FX_BIN" \
            "$FX_RES/dictionary_icon.txt" "$FX_RES/eccodes_definitions_cosmo" \
            "$FX_RES/eccodes_definitions_vendor" "$FX_SAMPLE" "$ICON_GRID"; do
  [[ -e "$path" ]] || { echo "missing required path: $path" >&2; exit 2; }
done

mkdir -p "$OUT_DIR"
[[ ! -e "$OUTPUT" && ! -e "$OUTPUT.ready" ]] || {
  echo "refusing to overwrite published or candidate output: $OUTPUT" >&2; exit 2;
}

WORK="$OUT_DIR/.regrid_${REA_DATE}_${REA_TIME}_${SLURM_JOB_ID}.partial"
trap 'rc=$?; if [[ $rc -eq 0 ]]; then rm -rf "$WORK"; else echo "failed work retained=$WORK" >&2; fi' EXIT
mkdir -p "$WORK"
cd "$WORK"

TARGET_GRID=$(python3 "$GRID_SCRIPT")
printf 'source=%s\ntarget_grid=%s\noutput=%s\n' "$STATIC_GRIB" "$TARGET_GRID" "$OUTPUT"

cat > hsurf.nl <<EOF
&RunSpecification
 strict_nl_parsing = .true.
 verbosity = "moderate"
 additional_diagnostic = .true.
 diagnostic_length = 110
 soft_memory_limit = 40.0
 n_ompthread_total = 4
 n_ompthread_collect = 1
 n_ompthread_generate = 1
/

&GlobalResource
 dictionary = "$FX_RES/dictionary_icon.txt"
 grib_definition_path = "$FX_RES/eccodes_definitions_cosmo",
                        "$FX_RES/eccodes_definitions_vendor"
 grib2_sample = "$FX_SAMPLE"
 icon_grid_description = "$ICON_GRID"
/

&GlobalSettings
 default_model_name = "rea-l-ch1"
 default_out_type_stdlongitude = .true.
 default_out_type_justontime = "no"
 auxiliary_metainfo = "localNumberOfExperiment=$EXPVER"
/

&ModelSpecification
 model_name = "rea-l-ch1"
 regrid_method = "__ALL__:icontools,nnb_strict"
 earth_axis_large = 6371229.
 earth_axis_small = 6371229.
/

&Process
 in_file = "$STATIC_GRIB"
 out_regrid_target = "$TARGET_GRID"
 out_regrid_method = "default"
 out_file = "hsurf.nc"
 out_type = "NETCDF"
/
&Process in_field = "HSURF" /
EOF

ulimit -s unlimited 2>/dev/null || true
export OMP_STACKSIZE="${OMP_STACKSIZE:-500M}"
"$FX_BIN" --syntaxcheck hsurf.nl > hsurf.syntax.log 2>&1
"$FX_BIN" hsurf.nl > hsurf.run.log 2>&1
test -s hsurf.nc || { echo "fieldextra did not create HSURF output" >&2; exit 1; }

# The static builder consumes HSURF with fieldextra's standard lat_1/lon_1
# coordinate names.  Fail closed if a fieldextra version writes another schema.
ncks -m -v HSURF hsurf.nc >/dev/null
ncks -m -v lat_1 hsurf.nc >/dev/null
ncks -m -v lon_1 hsurf.nc >/dev/null
ncks -H -C -v HSURF -s '%g\n' hsurf.nc | awk '
  NR == 1 { min = max = $1 }
  { if ($1 < min) min = $1; if ($1 > max) max = $1 }
  END { if (NR == 0 || min != min || max != max) exit 1; printf("HSURF range: %.3f .. %.3f m\\n", min, max) }
'

mv hsurf.nc "$OUTPUT"
sha256sum "$OUTPUT" > "$OUTPUT.sha256"
touch "$OUTPUT.ready"
ncdump -h "$OUTPUT" | sed -n '1,160p'
echo "published=$OUTPUT"
