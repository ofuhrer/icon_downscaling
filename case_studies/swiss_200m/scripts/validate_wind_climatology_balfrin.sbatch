#!/bin/bash -l
# One-node GPU integration and hourly-boundary restart test for wind climatology.
#SBATCH --job-name=hicar-wind-clim
#SBATCH --partition=debug
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=5
#SBATCH --cpus-per-task=1
#SBATCH --gres=gpu:4
#SBATCH --time=00:20:00
#SBATCH --exclusive
#SBATCH --output=hicar_wind_climatology_%j.out
#SBATCH --error=hicar_wind_climatology_%j.err

set -euo pipefail

coordinator_root=${HICAR_COORDINATOR_ROOT:?set HICAR_COORDINATOR_ROOT}
source_root=${HICAR_SOURCE_ROOT:?set HICAR_SOURCE_ROOT}
exe=${HICAR_EXE:?set HICAR_EXE}
validation_root=${HICAR_VALIDATION_ROOT:?set HICAR_VALIDATION_ROOT}
expected_commit=${HICAR_EXPECTED_COMMIT:?set HICAR_EXPECTED_COMMIT}
python_exe=${HICAR_PYTHON:-python3}

. "$coordinator_root/scripts/load_balfrin_site_config.sh"
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module purge || true
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module load nvhpc/24.5 cray-mpich-nvhpc/8.1.30 cuda/12.3.0-gcc \
  netcdf-c/4.9.2-nvhpc netcdf-fortran/4.6.1-nvhpc \
  hdf5/1.14.3-nvhpc fftw/3.3.10-gcc
"$python_exe" -c \
  'import sys, netCDF4, numpy; assert sys.version_info >= (3, 9)'

partition=$(scontrol show partition "${SLURM_JOB_PARTITION:?}" -o)
case "$partition" in
  *AllowGroups=ALL*|*AllowGroups=s83*) ;;
  *) echo "$SLURM_JOB_PARTITION is not currently allowed for group s83" >&2; exit 2 ;;
esac

test "$(git -C "$source_root" rev-parse HEAD)" = "$expected_commit"
test -x "$exe"
test_data="$source_root/tests/Test_Cases"
example="$source_root/helpers/example_namelists/alpine_realdata.nml"
set_var="$source_root/helpers/example_namelists/set_nml_var.py"
support="$source_root/run"
wrapper="$coordinator_root/case_studies/swiss_200m/scripts/gpu_rank_wrapper.sh"
validator="$coordinator_root/case_studies/swiss_200m/validation/validate_wind_climatology_output.py"
compactor="$coordinator_root/case_studies/swiss_200m/validation/compact_wind_climatology_output.py"
for path in "$test_data/domains/Gaudergrat_250m.nc" "$test_data/forcing" \
  "$example" "$set_var" "$wrapper" "$validator" "$compactor" \
  "$support/NoahmpTable.TBL" "$support/rrtmg_support" \
  "$support/rrtmgp_support" "$support/mp_support"; do
  test -e "$path" || { echo "missing validation input: $path" >&2; exit 2; }
done

run="$validation_root/wind_climatology_${SLURM_JOB_ID:?}"
test ! -e "$run" || { echo "refusing existing run: $run" >&2; exit 2; }
mkdir -p "$run/input" "$run/output" "$run/output_restarted" "$run/restart" "$run/logs"
for asset in "$support"/*.TBL; do
  ln -s "$asset" "$run/input/$(basename "$asset")"
done
for asset in rrtmg_support rrtmgp_support mp_support; do
  ln -s "$support/$asset" "$run/input/$asset"
done
"$source_root/helpers/filelist_script.sh" "$test_data/forcing/*" \
  "$run/input/forcing_files.txt"

cp "$example" "$run/input/continuous.nml"
set_nml() {
  local namelist=$1
  shift
  "$python_exe" "$set_var" "$namelist" "$@" --insert
}
set_nml "$run/input/continuous.nml" end_date "'2017-02-14 02:00:00'" --group general
set_nml "$run/input/continuous.nml" restart_folder "'$run/restart/'" --group restart
set_nml "$run/input/continuous.nml" restartinterval 1 --group restart
set_nml "$run/input/continuous.nml" init_conditions_file \
  "'$test_data/domains/Gaudergrat_250m.nc'" --group domain
set_nml "$run/input/continuous.nml" forcing_file_list \
  "'$run/input/forcing_files.txt'" --group forcing
set_nml "$run/input/continuous.nml" rad "'rrtmgp'" --group physics
set_nml "$run/input/continuous.nml" update_interval_rad 600.0 --group rad_parameters
set_nml "$run/input/continuous.nml" rrtmgp_block_N 256 --group rad_parameters
set_nml "$run/input/continuous.nml" Sx .False. --group wind
set_nml "$run/input/continuous.nml" outputinterval 3600 --group output
set_nml "$run/input/continuous.nml" output_folder "'$run/output/'" --group output
set_nml "$run/input/continuous.nml" output_vars \
  "'u_agl_mean_1h','v_agl_mean_1h','wind_speed_agl_mean_1h','wind_speed_agl_10min_max_1h','u10m_mean_1h','v10m_mean_1h','wind_speed_10m_mean_1h','wind_speed_10m_10min_max_1h','psfc','taix','hus2m','ustar','sfc_Ri','hpbl'" \
  --group output

export OMP_NUM_THREADS=1
export MPICH_GPU_SUPPORT_ENABLED=0
export MPICH_GPU_MANAGED_MEMORY_SUPPORT_ENABLED=0
export HICAR_WIND_ADJOINT_PROJECTION=1
export HICAR_WIND_RECYCLE_DIM=0

{
  printf 'job_id=%s\nsource_commit=%s\n' "$SLURM_JOB_ID" "$expected_commit"
  printf 'executable_sha256='; sha256sum "$exe" | awk '{print $1}'
  printf 'namelist_sha256='; sha256sum "$run/input/continuous.nml" | awk '{print $1}'
  printf 'host=%s\n' "$(hostname)"
  module -t list 2>&1
} >"$run/provenance.txt"

cd "$run/input"
srun --distribution=block:block --cpu-bind=none "$wrapper" \
  "$exe" "$run/input/continuous.nml" >"$run/logs/continuous.out" 2>&1
grep -q 'Simulation completed successfully!' "$run/logs/continuous.out"
mapfile -t output_files < <(find "$run/output" -maxdepth 1 -type f -name '*.nc' -size +0c)
test "${#output_files[@]}" -eq 1
cp "${output_files[0]}" "$run/continuous.nc"

cp "$run/input/continuous.nml" "$run/input/restarted.nml"
set_nml "$run/input/restarted.nml" restart_run .True. --group restart
set_nml "$run/input/restarted.nml" restart_date \
  "'2017-02-14 01:00:00'" --group restart
set_nml "$run/input/restarted.nml" output_folder \
  "'$run/output_restarted/'" --group output
srun --distribution=block:block --cpu-bind=none "$wrapper" \
  "$exe" "$run/input/restarted.nml" >"$run/logs/restarted.out" 2>&1
grep -q 'Simulation completed successfully!' "$run/logs/restarted.out"
mapfile -t restarted_output_files < <(
  find "$run/output_restarted" -maxdepth 1 -type f -name '*.nc' -size +0c
)
test "${#restarted_output_files[@]}" -eq 1

"$python_exe" "$validator" \
  --continuous "$run/continuous.nc" \
  --restarted "${restarted_output_files[0]}" \
  --report "$run/validation.json"
"$python_exe" "$compactor" \
  "$run/continuous.nc" "$run/continuous.deflate1.nc" \
  --deflate-level 1 --report "$run/compression.json"
touch "$run/validation.complete"
printf 'PASS run=%s validation=%s compression=%s\n' \
  "$run" "$run/validation.json" "$run/compression.json"
