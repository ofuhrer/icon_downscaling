#!/bin/bash -l
# One-node whole-model GPU benchmark using HICAR's bundled Alpine test inputs.
# Each job creates a new immutable result directory. graph_only, defer_uploads,
# and cuda_graph factor the NVHPC runtime settings for controlled A/B timing.
#SBATCH --job-name=hicar-gpu-bench
#SBATCH --partition=debug
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=5
#SBATCH --cpus-per-task=1
#SBATCH --gres=gpu:4
#SBATCH --time=00:20:00
#SBATCH --exclusive
#SBATCH --output=hicar_gpu_bench_%j.out
#SBATCH --error=hicar_gpu_bench_%j.err

set -euo pipefail

coordinator_root=${HICAR_COORDINATOR_ROOT:?set HICAR_COORDINATOR_ROOT}
source_root=${HICAR_SOURCE_ROOT:?set HICAR_SOURCE_ROOT}
exe=${HICAR_EXE:?set HICAR_EXE}
benchmark_root=${HICAR_BENCHMARK_ROOT:?set HICAR_BENCHMARK_ROOT}
mode=${HICAR_BENCHMARK_MODE:-baseline}
end_hour=${HICAR_BENCHMARK_END_HOUR:-01}
restart_interval=${HICAR_BENCHMARK_RESTART_INTERVAL:-1}
case_end="2017-02-14 ${end_hour}:00:00"

. "$coordinator_root/scripts/load_balfrin_site_config.sh"
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module purge || true
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module load nvhpc/24.5 cray-mpich-nvhpc/8.1.30 cuda/12.3.0-gcc \
  netcdf-c/4.9.2-nvhpc netcdf-fortran/4.6.1-nvhpc \
  hdf5/1.14.3-nvhpc fftw/3.3.10-gcc

partition_name=${SLURM_JOB_PARTITION:?}
partition=$(scontrol show partition "$partition_name" -o)
case "$partition" in
  *AllowGroups=ALL*|*AllowGroups=s83*) ;;
  *) echo "$partition_name is not currently allowed for group s83" >&2; exit 2 ;;
esac

case "$mode" in
  baseline|graph_only|defer_uploads|cuda_graph|nsys) ;;
  *) echo "unsupported HICAR_BENCHMARK_MODE: $mode" >&2; exit 2 ;;
esac
[[ "$end_hour" =~ ^(0[1-3])$ ]] || {
  echo "HICAR_BENCHMARK_END_HOUR must be 01, 02, or 03" >&2; exit 2;
}
[[ "$restart_interval" =~ ^[1-9][0-9]*$ ]] || {
  echo "HICAR_BENCHMARK_RESTART_INTERVAL must be a positive integer" >&2; exit 2;
}

test_data="$source_root/tests/Test_Cases"
example="$source_root/helpers/example_namelists/alpine_realdata.nml"
set_var="$source_root/helpers/example_namelists/set_nml_var.py"
support="$source_root/run"
wrapper="$coordinator_root/case_studies/swiss_200m/scripts/gpu_rank_wrapper.sh"
if [[ "$mode" == nsys ]]; then
  wrapper="$coordinator_root/case_studies/swiss_200m/scripts/gpu_rank_nsys_wrapper.sh"
fi
for path in "$exe" "$test_data/domains/Gaudergrat_250m.nc" \
  "$test_data/forcing" "$example" "$set_var" "$wrapper" \
  "$support/NoahmpTable.TBL" "$support/rrtmg_support" \
  "$support/rrtmgp_support" "$support/mp_support"; do
  test -e "$path" || { echo "missing benchmark input: $path" >&2; exit 2; }
done

run="$benchmark_root/${mode}_${SLURM_JOB_ID:?}"
test ! -e "$run" || { echo "refusing existing run: $run" >&2; exit 2; }
mkdir -p "$run/input" "$run/output" "$run/restart" "$run/traces"
ln -s "$test_data/domains" "$run/domains"
ln -s "$test_data/forcing" "$run/forcing"
for asset in "$support"/*.TBL; do
  ln -s "$asset" "$run/input/$(basename "$asset")"
done
for asset in rrtmg_support rrtmgp_support mp_support; do
  ln -s "$support/$asset" "$run/input/$asset"
done

"$source_root/helpers/filelist_script.sh" "$test_data/forcing/*" \
  "$run/input/forcing_files.txt"
cp "$example" "$run/input/input.nml"
set_nml() {
  python3 "$set_var" "$run/input/input.nml" "$1" "$2" \
    --group "$3" --insert
}
set_nml end_date "'$case_end'" general
set_nml restart_folder "'$run/restart/'" restart
set_nml init_conditions_file "'$test_data/domains/Gaudergrat_250m.nc'" domain
set_nml forcing_file_list "'$run/input/forcing_files.txt'" forcing
set_nml rad "'rrtmgp'" physics
set_nml update_interval_rad 600.0 rad_parameters
set_nml rrtmgp_block_N 256 rad_parameters
set_nml Sx .False. wind
set_nml outputinterval 3600 output
set_nml output_folder "'$run/output/'" output
set_nml restartinterval "$restart_interval" restart

export OMP_NUM_THREADS=1
export MPICH_GPU_SUPPORT_ENABLED=0
export MPICH_GPU_MANAGED_MEMORY_SUPPORT_ENABLED=0
export HICAR_WIND_ADJOINT_PROJECTION=1
export HICAR_WIND_RECYCLE_DIM=0
unset NVCOMPILER_ACC_USE_GRAPH NVCOMPILER_ACC_DEFER_UPLOADS \
  NVCOMPILER_ACC_DEFER_UPLOADS_THRESHOLD
if [[ "$mode" == graph_only || "$mode" == cuda_graph ]]; then
  export NVCOMPILER_ACC_USE_GRAPH=1
fi
if [[ "$mode" == defer_uploads || "$mode" == cuda_graph ]]; then
  export NVCOMPILER_ACC_DEFER_UPLOADS=1
  export NVCOMPILER_ACC_DEFER_UPLOADS_THRESHOLD=1
fi
if [[ "$mode" == nsys ]]; then
  export HICAR_NSYS_OUTPUT_DIR="$run/traces"
fi

{
  printf 'job_id=%s\nmode=%s\nsource_commit=%s\n' \
    "$SLURM_JOB_ID" "$mode" "$(git -C "$source_root" rev-parse HEAD)"
  printf 'executable_sha256='; sha256sum "$exe" | awk '{print $1}'
  printf 'namelist_sha256='; sha256sum "$run/input/input.nml" | awk '{print $1}'
  printf 'NVCOMPILER_ACC_USE_GRAPH=%s\n' "${NVCOMPILER_ACC_USE_GRAPH:-unset}"
  printf 'NVCOMPILER_ACC_DEFER_UPLOADS=%s\n' "${NVCOMPILER_ACC_DEFER_UPLOADS:-unset}"
  printf 'NVCOMPILER_ACC_DEFER_UPLOADS_THRESHOLD=%s\n' \
    "${NVCOMPILER_ACC_DEFER_UPLOADS_THRESHOLD:-unset}"
  printf 'host=%s\n' "$(hostname)"
  module -t list 2>&1
} >"$run/provenance.txt"

cd "$run/input"
srun --distribution=block:block --cpu-bind=none "$wrapper" \
  "$exe" "$run/input/input.nml" >"$run/model.out" 2>&1
grep -q 'Simulation completed successfully!' "$run/model.out"
find "$run/output" -type f -name '*.nc' -size +0c -print -quit | grep -q .
find "$run/restart" -type f -name '*.nc' -size +0c -print -quit | grep -q .
touch "$run/benchmark.complete"
printf 'PASS mode=%s run=%s\n' "$mode" "$run"
