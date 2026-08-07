#!/bin/bash -l
# Real-state gate for HICAR's shared initialization-only path.
#
# Required submission variables:
#   HICAR_INITIALIZATION_ROOT       isolated qualification directory
#   HICAR_INITIALIZATION_BUILD_ROOT clean GPU-NCCL build
#   HICAR_INITIALIZATION_CASE_DATE  20200115 or 20200702
#   HICAR_INITIALIZATION_SOIL_METHOD smi or relative_saturation
#
# Optional:
#   HICAR_INITIALIZATION_SOURCE_ROOT source matching the build
#   HICAR_INITIALIZATION_RENDERER    renderer with the engineering-state profile
#   HICAR_INITIALIZATION_COORDINATOR coordinator checkout containing hicarprep
#   HICAR_INITIALIZATION_MAX_HYDROSTATIC_RESIDUAL (default 5e-3; provisional)
#SBATCH --job-name=hicar-init-gate
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

partition=${SLURM_JOB_PARTITION:?This gate must run under Slurm}
case "$partition" in debug|short) ;; *) echo "unsupported partition: $partition" >&2; exit 2 ;; esac
allow=$(scontrol show partition "$partition" -o | sed -n 's/.*AllowGroups=\([^ ]*\).*/\1/p')
case ",$allow," in *,ALL,*|*,s83,*) ;; *) echo "partition access rejected: $allow" >&2; exit 2 ;; esac

gate_root=${HICAR_INITIALIZATION_ROOT:?Set HICAR_INITIALIZATION_ROOT}
build_root=${HICAR_INITIALIZATION_BUILD_ROOT:?Set HICAR_INITIALIZATION_BUILD_ROOT}
source_root=${HICAR_INITIALIZATION_SOURCE_ROOT:-$gate_root/source}
coordinator=${HICAR_INITIALIZATION_COORDINATOR:-$gate_root/coordinator}
renderer=${HICAR_INITIALIZATION_RENDERER:-$coordinator/case_studies/swiss_200m/scripts/render_hicar_namelist.py}
date=${HICAR_INITIALIZATION_CASE_DATE:?Set HICAR_INITIALIZATION_CASE_DATE}
method=${HICAR_INITIALIZATION_SOIL_METHOD:?Set HICAR_INITIALIZATION_SOIL_METHOD}
maximum_hydrostatic_residual=${HICAR_INITIALIZATION_MAX_HYDROSTATIC_RESIDUAL:-5e-3}

case "$date" in
  20200115)
    forcing_cache=$SCRATCH/icon_hicar/qualification/chronological-overlap-winter-202001/v1/campaign/forcing_cache/records
    ;;
  20200702)
    forcing_cache=$SCRATCH/icon_hicar/qualification/chronological-overlap-summer-202007/v2/campaign/forcing_cache/records
    ;;
  *) echo "no retained qualified forcing cache for $date" >&2; exit 2 ;;
esac
case "$method" in smi|relative_saturation) ;; *) echo "unsupported method: $method" >&2; exit 2 ;; esac

surface_root=${HICARPREP_SURFACE_ROOT:-$SCRATCH/icon_hicar/qualification/hicarprep_surface_cases_v11}
static_file=$surface_root/$date/hicar_runtime_domain_${method}_${date}_0000.nc
exe=$build_root/HICAR_gpu
wrapper=${HICAR_INITIALIZATION_GPU_WRAPPER:-$coordinator/case_studies/swiss_200m/scripts/gpu_rank_wrapper.sh}
python_bin=$SCRATCH/icon_hicar/qualification/wind-spinup-morrison-b514/python-v2/bin/python
hicarprep=$coordinator/scripts/hicarprep.py
run=$gate_root/runs/${date}_${method}

for path in "$static_file" "$static_file.ready" "$exe" "$wrapper" \
            "$renderer" "$python_bin" "$hicarprep"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done
test ! -e "$run/model.out" || { echo "refusing to overwrite prior gate: $run" >&2; exit 2; }
if test -n "${HICAR_EXPECTED_COMMIT:-}"; then
  test "$(git -C "$source_root" rev-parse HEAD)" = "$HICAR_EXPECTED_COMMIT"
fi
mkdir -p "$run/output" "$run/restart"

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

start="${date:0:4}-${date:4:2}-${date:6:2} 00:00:00"
end="${date:0:4}-${date:4:2}-${date:6:2} 01:00:00"
"$python_bin" "$renderer" \
  --static-file "$static_file" \
  --forcing-file-list "$forcing_list" \
  --start-date "$start" --end-date "$end" \
  --output-interval 3600 --output-profile engineering \
  --rea-l-land-initialization --depth-varying-soil \
  --nz 80 --wind-solver-iterations 2500 \
  --output-dir "$run/output" --restart-dir "$run/restart" \
  --output "$run/input.nml"

for asset in NoahmpTable.TBL rrtmgp_support mp_support; do
  ln -s "$source_root/run/$asset" "$run/$asset"
done
sha256sum "$static_file" "${forcing_files[@]}" "$exe" > "$run/inputs.sha256"

export OMP_NUM_THREADS=1
export MPICH_GPU_SUPPORT_ENABLED=0
export MPICH_GPU_MANAGED_MEMORY_SUPPORT_ENABLED=0
export HICAR_WIND_ADJOINT_PROJECTION=1
export HICAR_WIND_RECYCLE_DIM=0
export HICAR_INITIALIZATION_DIAGNOSTICS=$run/initialization_diagnostics.json
unset HICAR_WIND_MULTILEVEL HICAR_WIND_OPERATOR_AUDIT HICAR_WIND_OPERATOR_AUDIT_MAX_ITERS

cd "$run"
srun --distribution=block:block --cpu-bind=none \
  "$wrapper" "$exe" --initialize-only "$run/input.nml" \
  2>&1 | tee "$run/model.out"

test -s "$run/initialization_diagnostics.json"
grep -q 'HICAR discretely adjoint wind projection enabled' "$run/model.out"
grep -q 'HICAR adjoint conservation: relative_Bq=' "$run/model.out"
shopt -s nullglob
outputs=("$run/output"/*.nc)
test "${#outputs[@]}" -eq 1 || {
  echo "expected one time-zero HICAR output, found ${#outputs[@]}" >&2
  exit 1
}
initialized_state=${outputs[0]}
"$python_bin" "$hicarprep" certify-initialization \
  --initialized-state "$initialized_state" \
  --diagnostics "$run/initialization_diagnostics.json" \
  --certificate "$run/balance_certificate.json" \
  --maximum-hydrostatic-residual "$maximum_hydrostatic_residual" \
  > "$run/certification.out"
test -s "$run/balance_certificate.json"
touch "$run/balance_certificate.json.ready"

printf 'PASS date=%s method=%s initialized_state=%s certificate=%s\n' \
  "$date" "$method" "$initialized_state" "$run/balance_certificate.json"
