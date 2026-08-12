#!/bin/bash -l
# Produce one hourly hicarprep forcing/LBC pair. Native GRIB and the decoded
# ICON state are job-local; .ready files protect concurrent campaign readers.
#SBATCH --job-name=hicarprep-record
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=64G
#SBATCH --time=01:00:00

set -euo pipefail

repo_root=${REPO_ROOT:?}
. "$repo_root/scripts/load_balfrin_site_config.sh"
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

partition=$(scontrol show partition pp-short -o)
case "$partition" in
  *AllowGroups=ALL*|*AllowGroups=s83*) ;;
  *) echo "pp-short is not currently allowed for group s83" >&2; exit 2 ;;
esac

valid=${VALID_TIME:?Use YYYY-MM-DDTHH:MM:SS}
output=${HICAR_FORCING_OUTPUT:?}
static=${HICAR_STATIC_DOMAIN:?}
weights=${HICARPREP_RBF_WEIGHTS:?}
vector_weights=${HICARPREP_VECTOR_WEIGHTS:-}
python=${HICAR_PYTHON:-python3}
column_workers=${HICARPREP_COLUMN_WORKERS:-1}
if ! [[ "$column_workers" =~ ^[1-9][0-9]*$ ]]; then
  echo "HICARPREP_COLUMN_WORKERS must be a positive integer" >&2
  exit 2
fi
if test -n "${SLURM_CPUS_PER_TASK:-}" && (( column_workers > SLURM_CPUS_PER_TASK )); then
  echo "HICARPREP_COLUMN_WORKERS exceeds SLURM_CPUS_PER_TASK" >&2
  exit 2
fi
boundary=${output%.nc}.lbc.nc
recovery="$repo_root/scripts/hicarprep_pair_recovery.sh"
icon_extpar=${ICON_EXTPAR:-/oprusers/osm/opr.inn/data/ICON_INPUT/ICON-CH1-EPS/external_parameter_icon_grid_0001_R19B08_mch_tuned.nc}
image=${REA_FDB_IMAGE_OVERRIDE:-${REA_FDB_IMAGE:?REA_FDB_IMAGE is required}}
uenv_bin=${UENV_BIN:-/usr/bin/uenv}

test -f "$recovery" || { echo "missing pair recovery helper: $recovery" >&2; exit 2; }
. "$recovery"
pair_state=$(hicarprep_recover_pair "$output" "$boundary")
if test "$pair_state" = ready; then
  "$python" "$repo_root/case_studies/swiss_200m/validation/validate_forcing.py" \
    --forcing-file "$output" --boundary-file "$boundary" \
    --static-file "$static" --expected-valid-time "$valid"
  exit 0
fi
if test "$pair_state" = complete_unmarked; then
  "$python" "$repo_root/case_studies/swiss_200m/validation/validate_forcing.py" \
    --forcing-file "$output" --boundary-file "$boundary" \
    --static-file "$static" --expected-valid-time "$valid"
  touch "$output.ready" "$boundary.ready"
  exit 0
fi
for path in "$static" "$weights" "$icon_extpar" "$uenv_bin"; do
  test -e "$path" || { echo "missing input: $path" >&2; exit 2; }
done

cycle_date=${valid:0:10}; cycle_date=${cycle_date//-/}
step=${valid:11:2}
mkdir -p "$(dirname "$output")" "$SCRATCH/icon_hicar/tmp"
work=$(mktemp -d "$SCRATCH/icon_hicar/tmp/hicarprep.XXXXXX")
trap 'rm -rf "$work"' EXIT
native="$work/icon_native.nc"
surface_grib="$work/skt.grib"
target_sst="$work/target_sst.nc"
staged_output="$work/$(basename "$output")"
staged_boundary="$work/$(basename "$boundary")"

REPO_ROOT="$repo_root" \
ICON_ATMOSPHERE_OUTPUT="$native" \
ICON_ATMOSPHERE_CYCLE_DATE="$cycle_date" \
ICON_ATMOSPHERE_CYCLE_TIME=0000 \
ICON_ATMOSPHERE_STEP_HOURS="$((10#$step))" \
ICON_ATMOSPHERE_VALID_TIME="${valid}Z" \
ICON_MISSING_QI_POLICY=source-absent-zero \
bash "$repo_root/case_studies/swiss_200m/scripts/decode_rea_l_atmosphere_balfrin.sbatch"

cat > "$work/skt.mars" <<EOF
retrieve,
  class=rd,
  stream=reanl,
  expver=r001,
  model=icon-rea-l-ch1,
  type=cf,
  date=$cycle_date,
  time=0000,
  levtype=sfc,
  param=502336,
  step=$((10#$step))
EOF
"$uenv_bin" run --view=rea-l-ch1 "$image" -- \
  fdb-read --statistics "$work/skt.mars" "$surface_grib"
message_count=$("$uenv_bin" run --view=rea-l-ch1 "$image" -- \
  grib_count "$surface_grib" | tail -n 1)
test "$message_count" = 1
"$uenv_bin" run --view=rea-l-ch1 "$image" -- python \
  "$repo_root/case_studies/swiss_200m/validation/package_rea_l_target_sst.py" \
  --surface-grib "$surface_grib" --icon-extpar "$icon_extpar" \
  --static "$static" --weights "$weights" --valid-time "${valid}Z" \
  --output "$target_sst"

vector_args=()
test -z "$vector_weights" || vector_args=(--vector-weights "$vector_weights")
"$python" "$repo_root/scripts/hicarprep.py" prepare-hicar-forcing \
  --icon-state "$native" --static "$static" --weights "$weights" \
  --target-sst "$target_sst" "${vector_args[@]}" \
  --output "$staged_output" --boundary "$staged_boundary" \
  --column-workers "$column_workers"
"$python" "$repo_root/case_studies/swiss_200m/validation/validate_forcing.py" \
  --forcing-file "$staged_output" --boundary-file "$staged_boundary" \
  --static-file "$static" --expected-valid-time "$valid"
mv "$staged_output" "$output"
mv "$staged_boundary" "$boundary"
touch "$output.ready" "$boundary.ready"
