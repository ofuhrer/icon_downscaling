#!/bin/bash -l
# Matched six-hour hicarprep soil-transfer response arm.
#SBATCH --job-name=hprep-land-6h
#SBATCH --partition=short
#SBATCH --nodes=2
#SBATCH --ntasks-per-node=5
#SBATCH --cpus-per-task=1
#SBATCH --gres=gpu:4
#SBATCH --time=00:45:00
#SBATCH --exclusive

set -euo pipefail
repo_root=${REPO_ROOT:?Set REPO_ROOT to the dedicated coordinator worktree}
. "$repo_root/scripts/load_balfrin_site_config.sh"
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module purge || true
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module load nvhpc/24.5 cray-mpich-nvhpc/8.1.30 cuda/12.3.0-gcc \
  netcdf-c/4.9.2-nvhpc netcdf-fortran/4.6.1-nvhpc \
  hdf5/1.14.3-nvhpc fftw/3.3.10-gcc

method=${HICARPREP_SOIL_WATER_METHOD:?Set HICARPREP_SOIL_WATER_METHOD}
case "$method" in smi|relative_saturation) ;; *) echo "unsupported method: $method" >&2; exit 2 ;; esac
date=20200702
qualification=$SCRATCH/icon_hicar/qualification
surface_root=${HICARPREP_SURFACE_ROOT:-$qualification/hicarprep_surface_cases_v11}
build=${HICARPREP_BUILD_ROOT:-$qualification/hicarprep_hicar_soiltexture_v2/build-gpu-nccl}
hicar_root=${HICARPREP_HICAR_ROOT:-$qualification/hicarprep_hicar_soiltexture_v2/HICAR}
forcing_cache=$qualification/chronological-overlap-summer-202007/v2/campaign/forcing_cache/records
run_root=${HICARPREP_LAND_RESPONSE_ROOT:?Set the dedicated response run root}
run=$run_root/$method
static_file=$surface_root/$date/hicar_runtime_domain_${method}_${date}_0000.nc
renderer=$repo_root/case_studies/swiss_200m/scripts/render_hicar_namelist.py
solver_evaluator=$repo_root/case_studies/swiss_200m/validation/evaluate_hicar_solver_log.py
wrapper=$repo_root/case_studies/swiss_200m/scripts/gpu_rank_wrapper.sh
exe=$build/HICAR_gpu
python_bin=$qualification/wind-spinup-morrison-b514/python-v2/bin/python

for path in "$static_file" "$static_file.ready" "$renderer" "$solver_evaluator" \
            "$wrapper" "$exe" "$python_bin"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done
test ! -e "$run" || { echo "refusing to overwrite response arm: $run" >&2; exit 2; }
mkdir -p "$run/output" "$run/restart"

forcing_list=$run/forcing_list.txt
: > "$forcing_list"
for hour in $(seq 0 7); do
  stamp=$(printf '%02d00' "$hour")
  path=$forcing_cache/rea_l_hicar_${date}_${stamp}.nc
  test -s "$path" || { echo "missing forcing record: $path" >&2; exit 2; }
  test -f "$path.ready" || { echo "unpublished forcing record: $path" >&2; exit 2; }
  printf '"%s"\n' "$path" >> "$forcing_list"
done
touch "$forcing_list.ready"
while IFS='"' read -r _ path _; do sha256sum "$path"; done < "$forcing_list" > "$run/forcing.sha256"

for variable in soiltexture_var soil_water_content_liq; do
  "$exe" -v "$variable" > "$run/${variable}_query.txt" 2>&1
  if grep -q "not a valid namelist variable" "$run/${variable}_query.txt"; then
    echo "selected executable lacks $variable" >&2
    exit 2
  fi
done

"$python_bin" "$renderer" \
  --static-file "$static_file" --forcing-file-list "$forcing_list" \
  --start-date "2020-07-02 00:00:00" --end-date "2020-07-02 06:01:00" \
  --output-interval 1800 --output-profile land_response_30min \
  --rea-l-land-initialization --depth-varying-soil \
  --nz 80 --wind-solver-iterations 2500 \
  --output-dir "$run/output" --restart-dir "$run/restart" \
  --output "$run/input.nml"

for asset in NoahmpTable.TBL rrtmgp_support mp_support; do
  ln -s "$hicar_root/run/$asset" "$run/$asset"
done
sha256sum "$static_file" > "$run/runtime_domain.sha256"
sha256sum "$exe" > "$run/executable.sha256"
git -C "$repo_root" rev-parse HEAD > "$run/coordinator_commit.txt"

export OMP_NUM_THREADS=1 MPICH_GPU_SUPPORT_ENABLED=0 MPICH_GPU_MANAGED_MEMORY_SUPPORT_ENABLED=0
export HICAR_WIND_ADJOINT_PROJECTION=1 HICAR_WIND_RECYCLE_DIM=0
unset HICAR_WIND_MULTILEVEL HICAR_WIND_OPERATOR_AUDIT HICAR_WIND_OPERATOR_AUDIT_MAX_ITERS
cd "$run"
srun --distribution=block:block --cpu-bind=none "$wrapper" "$exe" "$run/input.nml" \
  2>&1 | tee "$run/model.out"
grep -q 'Simulation completed successfully!' "$run/model.out"
"$python_bin" "$solver_evaluator" --model-log "$run/model.out" \
  --expected-hours 6 --report "$run/solver_report.json"
touch "$run/model.out.ready"
echo "PASS: six-hour hicarprep land response $method"
