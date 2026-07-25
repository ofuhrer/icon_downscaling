#!/bin/bash -l
# Six-hour Switzerland 200 m production-candidate qualification.
# Requires the atomically published 00--06 UTC REA-L forcing list.
#SBATCH --job-name=hicar-adjoint-6h
#SBATCH --partition=normal
#SBATCH --nodes=4
#SBATCH --ntasks-per-node=5
#SBATCH --cpus-per-task=1
#SBATCH --gres=gpu:4
#SBATCH --time=01:00:00
#SBATCH --exclusive
#SBATCH --output=/scratch/mch/olifu/icon_hicar/case_studies/swiss_200m/validation/adjoint_6h_%j.out
#SBATCH --error=/scratch/mch/olifu/icon_hicar/case_studies/swiss_200m/validation/adjoint_6h_%j.err

set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module purge || true
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module load nvhpc/24.5 cray-mpich-nvhpc/8.1.30 cuda/12.3.0-gcc \
  netcdf-c/4.9.2-nvhpc netcdf-fortran/4.6.1-nvhpc \
  hdf5/1.14.3-nvhpc fftw/3.3.10-gcc

root=${HICAR_MULTILEVEL_ROOT:-$SCRATCH/icon_hicar/HICAR-wind-galerkin-multilevel}
case_root=${HICAR_SWISS_CASE:-$SCRATCH/icon_hicar/case_studies/swiss_200m}
build=${HICAR_MULTILEVEL_BUILD:-$root/build_gpu_nccl}
exe="$build/HICAR_gpu"
static_file="$case_root/static/domain_static_swiss_200m.nc"
forcing_list=${HICAR_SWISS_FORCING_LIST:-$case_root/forcing/rea_l_ch1/forcing_20100101_0000_0600.txt}
forcing_manifest="$case_root/forcing/rea_l_ch1/forcing_20100101_0000_0600.manifest.json"
production_plan="$case_root/config/production_6h_plan.json"
renderer="$case_root/scripts/render_hicar_namelist.py"
python=${HICAR_VALIDATION_PYTHON:-$SCRATCH/icon_hicar/venv_static/bin/python}
wrapper=${HICAR_GPU_WRAPPER:-$case_root/scripts/gpu_rank_wrapper.sh}
run="$case_root/runs/adjoint_swiss_6h_${SLURM_JOB_ID:?}"

for path in "$exe" "$static_file" "$static_file.ready" "$forcing_list" \
            "$forcing_list.ready" "$forcing_manifest" "$forcing_manifest.ready" \
            "$production_plan" "$renderer" "$python" "$wrapper"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done
test "$(sha256sum "$static_file" | awk '{print $1}')" = \
  bf03fa8ce45270bb1bfb4e7f987b2af59e0a18db7a16b1d83fd2ed33c64b7372

mkdir -p "$run/output" "$run/restart"
git -C "$root" diff --quiet
git -C "$root" rev-parse HEAD > "$run/source_commit.txt"
sha256sum "$exe" > "$run/executable.sha256"
cp "$forcing_manifest" "$run/forcing_manifest.json"
cp "$production_plan" "$run/production_plan.json"
"$python" "$renderer" \
  --static-file "$static_file" \
  --forcing-file-list "$forcing_list" \
  --start-date "2010-01-01 00:00:00" \
  --end-date "2010-01-01 06:00:00" \
  --output-interval 21600 \
  --output-profile engineering \
  --nz 80 \
  --wind-solver-iterations 2500 \
  --output-dir "$run/output" \
  --restart-dir "$run/restart" \
  --output "$run/input.nml"

grep -Eq '^[[:space:]]*nz[[:space:]]*=[[:space:]]*80([[:space:]]|$)' "$run/input.nml"
grep -Eq '^[[:space:]]*terrain_smooth_windowsize[[:space:]]*=[[:space:]]*5' "$run/input.nml"
grep -Eq '^[[:space:]]*terrain_smooth_cycles[[:space:]]*=[[:space:]]*10' "$run/input.nml"
grep -Eq '^[[:space:]]*decay_rate_L_topo[[:space:]]*=[[:space:]]*2' "$run/input.nml"
grep -Eq '^[[:space:]]*decay_rate_S_topo[[:space:]]*=[[:space:]]*6' "$run/input.nml"
grep -Eq "^[[:space:]]*wind[[:space:]]*=[[:space:]]*'variational solver'" "$run/input.nml"
grep -Eq '^[[:space:]]*outputinterval[[:space:]]*=[[:space:]]*21600' "$run/input.nml"
grep -q "'w_grid'" "$run/input.nml"

for asset in NoahmpTable.TBL rrtmgp_support mp_support; do
  cp -a "$root/run/$asset" "$run/"
done

export OMP_NUM_THREADS=1
export MPICH_GPU_SUPPORT_ENABLED=0
export MPICH_GPU_MANAGED_MEMORY_SUPPORT_ENABLED=0
export HICAR_WIND_ADJOINT_PROJECTION=1
export HICAR_WIND_RECYCLE_DIM=0
unset HICAR_WIND_MULTILEVEL
unset HICAR_WIND_OPERATOR_AUDIT
unset HICAR_WIND_OPERATOR_AUDIT_MAX_ITERS

cd "$run"
srun --distribution=block:block --cpu-bind=none "$wrapper" "$exe" "$run/input.nml" \
  2>&1 | tee "$run/model.out"

grep -q 'HICAR discretely adjoint wind projection enabled' "$run/model.out"
geometry=$(grep 'HICAR SLEVE geometry gate:' "$run/model.out" | tail -n 1)
test -n "$geometry"
minimum_jacobian=$(sed -n 's/.*minimum_mass_jacobian=[[:space:]]*\([^[:space:]]*\).*/\1/p' <<<"$geometry")
minimum_thickness=$(sed -n 's/.*minimum_interface_thickness=[[:space:]]*\([^[:space:]]*\).*/\1/p' <<<"$geometry")
awk -v jacobian="$minimum_jacobian" -v thickness="$minimum_thickness" \
  'BEGIN { exit !(jacobian + 0 >= 0.1 && thickness + 0 >= 5.0) }'
grep -q 'HICAR exact Galerkin hierarchy ready:' "$run/model.out"
grep -q 'HICAR terminal collective solve gate:' "$run/model.out"
grep -q 'HICAR terminal physical solve:.*status=0' "$run/model.out"
grep -q 'HICAR native FGMRES+line:' "$run/model.out"
grep -q 'HICAR adjoint conservation: relative_Bq=' "$run/model.out"
grep -q 'Simulation completed successfully!' "$run/model.out"
grep -q 'Timing across all compute images:' "$run/model.out"
awk -f "$root/tools/check_adjoint_projection_log.awk" "$run/model.out"

output="$run/output/domain_static_swiss_200m_2010-01-01_00-00-00.nc"
test -s "$output"
printf 'Six-hour Switzerland 200 m model gate passed: run=%s output=%s\n' "$run" "$output"
