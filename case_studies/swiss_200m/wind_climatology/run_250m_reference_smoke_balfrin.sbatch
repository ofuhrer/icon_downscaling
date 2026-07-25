#!/bin/bash
#SBATCH --job-name=hicar-wind-agl
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=2
#SBATCH --cpus-per-task=4
#SBATCH --time=00:20:00
#SBATCH --output=hicar-wind-agl-%j.out
#SBATCH --error=hicar-wind-agl-%j.err

set -euo pipefail

[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:?}/modules"
module purge || true
module use "${USER_ENV_ROOT}/modules"
module load python/3.11.7 gcc/12.3.0 cray-mpich-gcc/8.1.30 cmake/3.24.4-gcc \
  gmake/4.4.1-gcc \
  netcdf-c/4.8.1-gcc netcdf-fortran/4.5.4-gcc fftw/3.3.10-gcc \
  openblas/0.3.26-gcc nco/5.0.1-gcc

validation_root="${VALIDATION_ROOT:-${SCRATCH:?}/icon_hicar/validation/wind-climatology}"
source_root="${HICAR_SOURCE_ROOT:-${validation_root}/HICAR}"
build_root="${validation_root}/build_cpu"
run_root="${validation_root}/runs/${SLURM_JOB_ID}"
case_root="${SCRATCH}/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m"
source_namelist="${case_root}/hicar_wind_design_sensitivity_24h/v2_auto1_n80_top12_s26/input/icon_hicar_250m_v2_auto1_n80_top12_s26.nml"

mkdir -p "${run_root}/input" "${run_root}/output" "${run_root}/restart"

nc_prefix=$(nc-config --prefix)
nf_prefix=$(nf-config --prefix)
fftw_prefix=$(pkg-config --variable=prefix fftw3)
mpi_prefix=$(dirname "$(dirname "$(command -v mpifort)")")

cmake -S "${source_root}" -B "${build_root}" \
  -DFC=mpifort -DMODE=release -DOPENACC=OFF -DNCCL=OFF \
  -DNETCDF_DIR="${nc_prefix}" \
  -DNETCDF_INCLUDES="${nc_prefix}/include;${nf_prefix}/include" \
  -DNETCDF_INCLUDES_F90="${nf_prefix}/include" \
  -DNETCDF_LIBRARIES_C="${nc_prefix}/lib/libnetcdf.so" \
  -DNETCDF_LIBRARIES_F90="${nf_prefix}/lib/libnetcdff.so" \
  -DFFTW_DIR="${fftw_prefix}" \
  -DMPI_DIR="${mpi_prefix}"
cmake --build "${build_root}" --target HICAR HICAR-tester --parallel 8

sed \
  -e "s#end_date = '2026-07-11 18:00:00'#end_date = '2026-07-10 19:00:00'#" \
  -e "s#restart_folder = '.*'#restart_folder = '${run_root}/restart/'#" \
  -e "s#output_folder = '.*'#output_folder = '${run_root}/output/'#" \
  -e "s#output_vars = .*#output_vars = 'u', 'v', 'density', 'z', 'u10m', 'v10m', 'u_agl', 'v_agl', 'rho_agl'#" \
  "${source_namelist}" > "${run_root}/input/run.nml"

export HICAR_IO_PER_NODE=1
srun "${build_root}/HICAR" "${run_root}/input/run.nml"

shopt -s nullglob
outputs=("${run_root}"/output/*.nc)
if (( ${#outputs[@]} == 0 )); then
  echo "No HICAR output was produced" >&2
  exit 1
fi

for path in "${outputs[@]}"; do
  ncks -m -v height_agl,u10m,v10m,u_agl,v_agl,rho_agl "${path}" >/dev/null
  metadata=$(ncks -m -v u10m,v10m,u_agl,v_agl,rho_agl "${path}")
  grep -Fq 'u10m:standard_name = "eastward_wind"' <<<"${metadata}"
  grep -Fq 'v10m:standard_name = "northward_wind"' <<<"${metadata}"
  grep -Fq 'u_agl:standard_name = "eastward_wind"' <<<"${metadata}"
  grep -Fq 'v_agl:standard_name = "northward_wind"' <<<"${metadata}"
  grep -Fq 'rho_agl:standard_name = "air_density"' <<<"${metadata}"
  ncks -H -C -v height_agl "${path}"
done
echo "PASS: ${#outputs[@]} output file(s) contain the fixed-height schema"
echo "RUN_ROOT=${run_root}"
