#!/bin/bash -l
# Quantify the discontinuity between one REA-L cycle's step 24 and the next
# cycle's step 0 at their shared valid time.
#SBATCH --job-name=rea-l-boundary-audit
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=4G
#SBATCH --time=00:20:00

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"

previous_date=${REA_PREVIOUS_DATE:-20091231}
next_date=${REA_NEXT_DATE:-20100101}
image=${REA_FDB_IMAGE:-fdb/5.19:v2}
uenv_bin=${UENV_BIN:-/usr/bin/uenv}
case_root=${HICAR_SWISS_CASE:-$SCRATCH/icon_hicar/case_studies/swiss_200m}
comparator="$case_root/validation/compare_rea_l_cycle_boundary.py"
report="$case_root/validation/rea_l_cycle_boundary_${previous_date}_${next_date}.json"
tmp_parent="$SCRATCH/icon_hicar/tmp"
mkdir -p "$tmp_parent"
work=$(mktemp -d "$tmp_parent/rea_l_boundary.XXXXXX")
trap 'rm -rf "$work"' EXIT

for path in "$uenv_bin" "$comparator"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done

write_requests() {
  local date=$1 step=$2 stem=$3
  cat > "$work/${stem}_full.mars" <<EOF
retrieve,
  class=rd,
  stream=reanl,
  expver=r001,
  model=icon-rea-l-ch1,
  type=cf,
  date=$date,
  time=0000,
  levtype=ml,
  levelist=1/to/80,
  param=500001/500014/500028/500030/500035,
  step=$step
EOF
  cat > "$work/${stem}_w.mars" <<EOF
retrieve,
  class=rd,
  stream=reanl,
  expver=r001,
  model=icon-rea-l-ch1,
  type=cf,
  date=$date,
  time=0000,
  levtype=ml,
  levelist=1/to/81,
  param=500032,
  step=$step
EOF
}

write_requests "$previous_date" 24 previous
write_requests "$next_date" 0 next
for stem in previous next; do
  "$uenv_bin" run --view=rea-l-ch1 "$image" -- \
    fdb-read --statistics "$work/${stem}_full.mars" "$work/${stem}_full.grib"
  "$uenv_bin" run --view=rea-l-ch1 "$image" -- \
    fdb-read --statistics "$work/${stem}_w.mars" "$work/${stem}_w.grib"
  cat "$work/${stem}_full.grib" "$work/${stem}_w.grib" > "$work/${stem}.grib"
  count=$("$uenv_bin" run --view=rea-l-ch1 "$image" -- \
    grib_count "$work/${stem}.grib" | tail -n 1)
  test "$count" = 481
done

rm -f "$report" "$report.ready"
"$uenv_bin" run --view=rea-l-ch1 "$image" -- \
  python3 "$comparator" "$work/previous.grib" "$work/next.grib" --report "$report"
test -s "$report"
touch "$report.ready"
echo "REA-L cycle-boundary audit passed: report=$report"
