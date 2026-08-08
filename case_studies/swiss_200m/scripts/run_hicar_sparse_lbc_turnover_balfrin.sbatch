#!/bin/bash -l
# Two-hour Storm Sabine integration crossing the 01Z sparse-LBC turnover.
#SBATCH --job-name=hicar-lbc-turnover
#SBATCH --partition=short
#SBATCH --nodes=2
#SBATCH --ntasks-per-node=5
#SBATCH --cpus-per-task=1
#SBATCH --gres=gpu:4
#SBATCH --time=00:45:00
#SBATCH --exclusive

set -euo pipefail

root=${HICAR_LBC_QUALIFICATION_ROOT:?Set HICAR_LBC_QUALIFICATION_ROOT}
coordinator=${HICAR_LBC_COORDINATOR_ROOT:?Set HICAR_LBC_COORDINATOR_ROOT}
source_root=${HICAR_LBC_SOURCE_ROOT:?Set HICAR_LBC_SOURCE_ROOT}
build_root=${HICAR_LBC_BUILD_ROOT:?Set HICAR_LBC_BUILD_ROOT}
date=${HICAR_LBC_CASE_DATE:?Set HICAR_LBC_CASE_DATE}
method=${HICAR_LBC_SOIL_METHOD:-smi}
forcing_cache=${HICAR_LBC_FORCING_CACHE:?Set HICAR_LBC_FORCING_CACHE}
surface_root=${HICARPREP_SURFACE_ROOT:-$SCRATCH/icon_hicar/qualification/hicarprep_surface_cases_v11}
static=$surface_root/$date/hicar_runtime_domain_${method}_${date}_0000.nc
python_bin=${HICAR_VALIDATION_PYTHON:-$SCRATCH/icon_hicar/qualification/wind-spinup-morrison-b514/python-v2/bin/python}
renderer=$coordinator/case_studies/swiss_200m/scripts/render_hicar_namelist.py
validator=$coordinator/case_studies/swiss_200m/validation/validate_sparse_lbc_runtime_output.py
wrapper=$coordinator/case_studies/swiss_200m/scripts/gpu_rank_wrapper.sh
exe=$build_root/HICAR_gpu
sparse_list=$root/sparse_lbc/sparse_lbc_list.txt
run=${HICAR_LBC_TURNOVER_RUN:-$root/turnover}

. "$coordinator/scripts/load_balfrin_site_config.sh"
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module purge || true
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module load nvhpc/24.5 cray-mpich-nvhpc/8.1.30 cuda/12.3.0-gcc \
  netcdf-c/4.9.2-nvhpc netcdf-fortran/4.6.1-nvhpc \
  hdf5/1.14.3-nvhpc fftw/3.3.10-gcc

partition=${SLURM_JOB_PARTITION:?This gate must run under Slurm}
case "$partition" in debug|short) ;; *) echo "unsupported partition: $partition" >&2; exit 2 ;; esac
allow=$(scontrol show partition "$partition" -o | sed -n 's/.*AllowGroups=\([^ ]*\).*/\1/p')
case ",$allow," in *,ALL,*|*,s83,*) ;; *) echo "partition access rejected: $allow" >&2; exit 2 ;; esac
for path in "$static" "$static.ready" "$python_bin" "$renderer" "$validator" "$wrapper" \
            "$exe" "$sparse_list" "$sparse_list.ready"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done
if test -n "${HICAR_EXPECTED_COMMIT:-}"; then
  test "$(git -C "$source_root" rev-parse HEAD)" = "$HICAR_EXPECTED_COMMIT"
fi
test ! -e "$run" || { echo "refusing to overwrite $run" >&2; exit 2; }
mkdir -p "$run/output" "$run/restart"

forcing_list=$run/forcing_list.txt
forcing_files=()
for time in 0000 0100 0200; do
  path=$forcing_cache/rea_l_hicar_${date}_${time}.nc
  test -s "$path" && test -f "$path.ready" || { echo "unpublished forcing: $path" >&2; exit 2; }
  forcing_files+=("$path")
done
printf '"%s"\n' "${forcing_files[@]}" > "$forcing_list"
touch "$forcing_list.ready"

start="${date:0:4}-${date:4:2}-${date:6:2} 00:00:00"
end="${date:0:4}-${date:4:2}-${date:6:2} 02:00:00"
soil_texture_arguments=()
if "$python_bin" - "$static" <<'PY'
import netCDF4
import sys
with netCDF4.Dataset(sys.argv[1]) as dataset:
    raise SystemExit(0 if "soil_type_layer" in dataset.variables else 1)
PY
then
  soil_texture_arguments=(--depth-varying-soil)
fi
"$python_bin" "$renderer" \
  --static-file "$static" --forcing-file-list "$forcing_list" \
  --sparse-lbc-file-list "$sparse_list" \
  --start-date "$start" --end-date "$end" \
  --output-interval 600 --output-profile engineering \
  --rea-l-land-initialization "${soil_texture_arguments[@]}" \
  --nz 80 --wind-solver-iterations 2500 \
  --output-dir "$run/output" --restart-dir "$run/restart" \
  --output "$run/input.nml"

for asset in NoahmpTable.TBL rrtmgp_support mp_support; do
  ln -s "$source_root/run/$asset" "$run/$asset"
done
sha256sum "$static" "${forcing_files[@]}" "$sparse_list" "$exe" > "$run/inputs.sha256"

export OMP_NUM_THREADS=1
export MPICH_GPU_SUPPORT_ENABLED=0
export MPICH_GPU_MANAGED_MEMORY_SUPPORT_ENABLED=0
export HICAR_WIND_ADJOINT_PROJECTION=1
export HICAR_WIND_RECYCLE_DIM=0
unset HICAR_WIND_MULTILEVEL HICAR_WIND_OPERATOR_AUDIT HICAR_WIND_OPERATOR_AUDIT_MAX_ITERS

cd "$run"
srun --distribution=block:block --cpu-bind=none \
  "$wrapper" "$exe" "$run/input.nml" 2>&1 | tee "$run/model.out"
grep -q 'Sparse target-grid LBC runtime enabled' "$run/model.out"
grep -q 'Sparse LBC bracket advanced: left= *2 *right= *3' "$run/model.out"
shopt -s nullglob
outputs=("$run/output"/*.nc)
test "${#outputs[@]}" -eq 1
"$python_bin" "$validator" \
  --output "${outputs[0]}" --model-log "$run/model.out" \
  --expected-records 13 --expected-interval-seconds 600 \
  --report "$run/validation.json"

printf 'PASS output=%s validation=%s\n' "${outputs[0]}" "$run/validation.json"
