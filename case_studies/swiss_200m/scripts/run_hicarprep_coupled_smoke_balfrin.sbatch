#!/bin/bash -l
# Ten-minute coupled HICAR smoke test for one hicarprep runtime domain.
#SBATCH --job-name=hprep-coupled
#SBATCH --partition=short
#SBATCH --nodes=2
#SBATCH --ntasks-per-node=5
#SBATCH --cpus-per-task=1
#SBATCH --gres=gpu:4
#SBATCH --time=00:45:00
#SBATCH --exclusive

set -euo pipefail
repo_root=${REPO_ROOT:-$SCRATCH/icon_hicar}
. "$repo_root/scripts/load_balfrin_site_config.sh"
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module purge || true
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module load nvhpc/24.5 cray-mpich-nvhpc/8.1.30 cuda/12.3.0-gcc \
  netcdf-c/4.9.2-nvhpc netcdf-fortran/4.6.1-nvhpc \
  hdf5/1.14.3-nvhpc fftw/3.3.10-gcc

date=${HICARPREP_CASE_DATE:?Set HICARPREP_CASE_DATE to YYYYMMDD}
method=${HICARPREP_SOIL_WATER_METHOD:?Set HICARPREP_SOIL_WATER_METHOD}
case "$date" in
  20200115)
    forcing_cache=$SCRATCH/icon_hicar/qualification/chronological-overlap-winter-202001/v1/campaign/forcing_cache/records
    ;;
  20200702)
    forcing_cache=$SCRATCH/icon_hicar/qualification/chronological-overlap-summer-202007/v2/campaign/forcing_cache/records
    ;;
  *) echo "no qualified atmospheric forcing cache for $date" >&2; exit 2 ;;
esac
case "$method" in smi|relative_saturation) ;; *) echo "unsupported method: $method" >&2; exit 2 ;; esac

qualification=$SCRATCH/icon_hicar/qualification
surface_root=${HICARPREP_SURFACE_ROOT:-$qualification/hicarprep_surface_cases_v11}
build=${HICARPREP_BUILD_ROOT:-$qualification/wind-spinup-morrison-b514/build-gpu-nccl}
hicar_root=$qualification/wind-spinup-morrison-b514/HICAR
static_file=$surface_root/$date/hicar_runtime_domain_${method}_${date}_0000.nc
run_root=${HICARPREP_COUPLED_ROOT:-$qualification/hicarprep_coupled_v1}
run=$run_root/${date}_${method}
renderer=$repo_root/case_studies/swiss_200m/scripts/render_hicar_namelist.py
wrapper=$repo_root/case_studies/swiss_200m/scripts/gpu_rank_wrapper.sh
exe=$build/HICAR_gpu

for path in "$static_file" "$static_file.ready" \
            "$renderer" "$wrapper" "$exe"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done
test ! -e "$run/model.out" || { echo "refusing to overwrite prior run: $run" >&2; exit 2; }
mkdir -p "$run/output" "$run/restart"

# The original campaign segment directories intentionally retire their large
# NetCDF payloads after use. Build a bounded two-record list from the retained,
# ready-marked shared forcing cache rather than following stale segment lists.
forcing_files=(
  "$forcing_cache/rea_l_hicar_${date}_0000.nc"
  "$forcing_cache/rea_l_hicar_${date}_0100.nc"
)
for path in "${forcing_files[@]}"; do
  test -s "$path" || { echo "missing cached forcing record: $path" >&2; exit 2; }
  test -f "$path.ready" || { echo "unpublished cached forcing record: $path" >&2; exit 2; }
done
forcing_list=$run/forcing_list.txt
printf '"%s"\n' "${forcing_files[@]}" > "$forcing_list"
touch "$forcing_list.ready"
sha256sum "${forcing_files[@]}" > "$run/forcing.sha256"

"$exe" -v soiltexture_var > "$run/soiltexture_var_query.txt" 2>&1
if grep -q "not a valid namelist variable" "$run/soiltexture_var_query.txt"; then
  echo "selected HICAR executable lacks depth-varying soiltexture_var support" >&2
  exit 2
fi

start="${date:0:4}-${date:4:2}-${date:6:2} 00:00:00"
# Run one minute past the requested 10-minute diagnostic boundary. HICAR does
# not emit a scheduled output when end_date equals that boundary exactly.
end="${date:0:4}-${date:4:2}-${date:6:2} 00:11:00"
python_bin=$qualification/wind-spinup-morrison-b514/python-v2/bin/python
"$python_bin" "$renderer" \
  --static-file "$static_file" \
  --forcing-file-list "$forcing_list" \
  --start-date "$start" --end-date "$end" \
  --output-interval 600 \
  --output-profile qualification --rea-l-land-initialization \
  --depth-varying-soil \
  --nz 80 --wind-solver-iterations 2500 \
  --output-dir "$run/output" --restart-dir "$run/restart" \
  --output "$run/input.nml"

for asset in NoahmpTable.TBL rrtmgp_support mp_support; do
  ln -s "$hicar_root/run/$asset" "$run/$asset"
done
sha256sum "$static_file" > "$run/runtime_domain.sha256"
sha256sum "$exe" > "$run/executable.sha256"

export OMP_NUM_THREADS=1
export MPICH_GPU_SUPPORT_ENABLED=0
export MPICH_GPU_MANAGED_MEMORY_SUPPORT_ENABLED=0
export HICAR_WIND_ADJOINT_PROJECTION=1
export HICAR_WIND_RECYCLE_DIM=0
unset HICAR_WIND_MULTILEVEL HICAR_WIND_OPERATOR_AUDIT HICAR_WIND_OPERATOR_AUDIT_MAX_ITERS
cd "$run"
srun --distribution=block:block --cpu-bind=none "$wrapper" "$exe" "$run/input.nml" \
  2>&1 | tee "$run/model.out"
grep -q 'Simulation completed successfully!' "$run/model.out"
grep -q 'Reading Land Variables' "$run/model.out"
grep -q 'Read surface temperature field from: surface_temperature' "$run/model.out"
! grep -q "ERROR reading 'domain' namelist" "$run/model.out"
shopt -s nullglob
outputs=("$run/output"/*.nc)
test "${#outputs[@]}" -gt 0 || { echo "HICAR wrote no NetCDF output" >&2; exit 1; }
"$python_bin" - "${outputs[@]}" <<'PY'
import sys
import netCDF4

times = []
for name in sys.argv[1:]:
    with netCDF4.Dataset(name) as dataset:
        if "time" not in dataset.variables:
            raise SystemExit(f"HICAR output lacks time coordinate: {name}")
        variable = dataset["time"]
        times.extend(
            netCDF4.num2date(
                variable[:],
                variable.units,
                calendar=getattr(variable, "calendar", "standard"),
            ).ravel().tolist()
        )
if len(times) < 2:
    raise SystemExit("HICAR did not write both initial and evolved output records")
elapsed = (max(times) - min(times)).total_seconds()
if not 599.0 <= elapsed <= 720.0:
    raise SystemExit(f"HICAR output span {elapsed} s is not the requested 10-minute state")
PY
touch "$run/model.out.ready"
echo "PASS: coupled hicarprep smoke test $date $method"
