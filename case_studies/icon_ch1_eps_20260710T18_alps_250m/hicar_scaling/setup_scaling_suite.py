#!/usr/bin/env python3
"""Generate a reproducible HICAR CPU/GPU scaling benchmark matrix."""
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

CASE = "icon_ch1_eps_20260710T18_alps_250m"
SCRATCH = os.environ.get("SCRATCH", "/scratch/mch/olifu")
CASE_ROOT = Path(os.environ.get("CASE_ROOT", f"{SCRATCH}/icon_hicar/case_studies/{CASE}"))
ROOT = Path(os.environ.get("SCALING_ROOT", str(CASE_ROOT / "hicar_scaling")))
START = datetime(2026, 7, 10, 18)
DURATION_HOURS = int(os.environ.get("HICAR_SCALING_DURATION_HOURS", "6"))
if DURATION_HOURS < 1:
    raise SystemExit("HICAR_SCALING_DURATION_HOURS must be positive")
END = START + timedelta(hours=DURATION_HOURS)
CPU = (1, 2, 4, 8, 16, 32, 64, 128, 192, 256, 384)
# NCCL uses a CPU-only I/O server; all four A100s remain available to compute.
GPU = (1, 2, 4, 8, 12, 16, 20, 24)
REPEATS = 3

@dataclass(frozen=True)
class Scenario:
    id: str; platform: str; kind: str; compute_ranks: int
    width_km: int; height_km: int; nodes: int; tasks_per_node: int; gpus_per_node: int

def factors(n: int) -> tuple[int, int]:
    return {1:(1,1),2:(2,1),3:(3,1),4:(2,2),6:(3,2),8:(4,2),9:(3,3),12:(4,3),15:(5,3),16:(4,4),18:(6,3),20:(5,4),24:(6,4),32:(8,4),64:(8,8),128:(16,8),192:(16,12),256:(16,16),384:(24,16)}[n]

def layout(platform: str, n: int) -> tuple[int, int, int]:
    if platform == "cpu":
        nodes = max(1, (n + 63) // 64)
        return nodes, n // nodes + 1, 0
    # The last local rank is a CPU-only I/O server.  All compute ranks retain
    # one A100 each; the selected matrix is four-GPU-node aligned above p=4.
    if n <= 4:
        return 1, n + 1, n
    return n // 4, 5, 4

def all_scenarios() -> list[Scenario]:
    out = []
    for platform, counts in (("cpu", CPU), ("gpu", GPU)):
        for w, h in ((80, 80), (240, 160)):
            for n in counts:
                nodes, tasks, gpus = layout(platform, n)
                out.append(Scenario(f"strong_{platform}_{w}x{h}_p{n}", platform, "strong", n, w, h, nodes, tasks, gpus))
        tile = 10 if platform == "cpu" else 40
        for n in counts:
            fx, fy = factors(n); nodes, tasks, gpus = layout(platform, n)
            out.append(Scenario(f"weak_{platform}_{fx*tile}x{fy*tile}_p{n}", platform, "weak", n, fx*tile, fy*tile, nodes, tasks, gpus))
    return out

def namelist(run: Path, s: Scenario) -> str:
    domain = ROOT / "domains" / f"{s.width_km}x{s.height_km}km"
    return f"""&general
  start_date = '{START:%Y-%m-%d %H:%M:%S}'
  end_date = '{END:%Y-%m-%d %H:%M:%S}'
/
&restart
  restart_folder = '{run}/restart/'
/
&domain
  init_conditions_file = '{domain}/static/domain_static_relaxed.nc'
  wait_for_ready_file = .True.
  ready_file_timeout = 120
  dx = 250.0
  nz = 80
  lat_hi = 'lat'
  lon_hi = 'lon'
  hgt_hi = 'topo'
  landvar = 'landmask'
  vegtype_var = 'landuse'
  soiltype_var = 'soil_type'
  surface_temp_var = 'surface_temperature'
  soil_deept_var = 'soil_deep_temperature'
  use_map_factors = .True.
  auto_level = 1
  height_lowest_level = 15.0
  model_top_height = 12000.0
  stretch_fac = 0.65
  decay_rate_L_topo = 2.0
  decay_rate_S_topo = 6.0
/
&forcing
  forcing_file_list = '{domain}/forcing_file_list.txt'
  wait_for_ready_file = .True.
  ready_file_timeout = 120
  inputinterval = 3600
  time_var = 'time'
  pvar = 'P'
  tvar = 'T'
  qvvar = 'QV'
  uvar = 'U'
  vvar = 'V'
  wvar = 'W'
  hgtvar = 'HSURF'
  zvar = 'HFL'
  latvar = 'lat_1'
  lonvar = 'lon_1'
  qv_is_spec_humidity = .True.
  t_is_potential = .False.
  relax_filters = .True.
/
&physics
  wind = 'variational solver'
  mp = 'morrison'
  pbl = 'ysu'
  lsm = 'noahmp'
  sfc = 'revmm5'
  water = 'simple'
  rad = 'rrtmgp'
  sm = 'none'
/
&time_parameters
  RK3 = .True.
  cfl_reduction_factor = 1.6
/
&lt_parameters
/
&mp_parameters
/
&adv_parameters
  h_order = 3
  v_order = 3
  flux_corr = 1
  cz_diff_order = 0
/
&sm_parameters
/
&lsm_parameters
  LU_Categories = 'USGS'
/
&cu_parameters
/
&rad_parameters
/
&pbl_parameters
/
&sfc_parameters
/
&wind
  Sx = .True.
  smooth_wind_distance = 500.0
  wind_solver_iterations = 2500
/
&output
  output_folder = '{run}/output/'
  outputinterval = 3600
  output_vars = 'qv', 'temperature', 'pressure', 'u', 'v', 'w', 'w_grid', 'precipitation'
/
"""

def job(run: Path, s: Scenario, repeat: int) -> str:
    exe = "$HICAR_ROOT/bin/HICAR_release" if s.platform == "cpu" else "$HICAR_ROOT/bin/HICAR_gpu"
    part = "pp-long" if s.platform == "cpu" else "normal"
    modules = "gcc/12.3.0 cray-mpich-gcc/8.1.30 netcdf-c/4.8.1-gcc netcdf-fortran/4.5.4-gcc fftw/3.3.10-gcc" if s.platform == "cpu" else "nvhpc/24.5 cray-mpich-nvhpc/8.1.30 cuda/12.3.0-gcc netcdf-c/4.9.2-nvhpc netcdf-fortran/4.6.1-nvhpc hdf5/1.14.3-nvhpc fftw/3.3.10-gcc"
    gres = "" if s.platform == "cpu" else f"#SBATCH --gres=gpu:{s.gpus_per_node}\n"
    walltime = "10:00:00" if s.platform == "cpu" and s.compute_ranks == 1 else "04:00:00"
    gpu_env = "export MPICH_GPU_SUPPORT_ENABLED=0 MPICH_GPU_MANAGED_MEMORY_SUPPORT_ENABLED=0\n" if s.platform == "gpu" else ""
    if s.platform == "gpu":
        launcher = f"srun -u --distribution=block:block --cpu-bind=none \"{ROOT}/scripts/gpu_rank_wrapper.sh\" \"$EXE\" \"{run}/input/run.nml\""
    else:
        launcher = f"srun -u --cpu-bind=cores \"{ROOT}/scripts/cpu_memory_rank_wrapper.sh\" \"$HICAR_MEMORY_DIR\" \"$EXE\" \"{run}/input/run.nml\""
    return f"""#!/bin/bash
#SBATCH --job-name={s.id}_r{repeat}
#SBATCH --partition={part}
#SBATCH --nodes={s.nodes}
#SBATCH --ntasks-per-node={s.tasks_per_node}
#SBATCH --cpus-per-task=1
{gres}#SBATCH --time={walltime}
#SBATCH --exclusive
#SBATCH --hint=nomultithread
#SBATCH --output={run}/logs/slurm_%j.out
#SBATCH --error={run}/logs/slurm_%j.err
set -euo pipefail
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"; module purge || true; module use "$USER_ENV_ROOT/modules"
module load python/3.11.7 {modules} >/dev/null
HICAR_ROOT="${{HICAR_ROOT:-$SCRATCH/icon_hicar/HICAR-scaling}}"
EXE={exe}
DOMAIN="{ROOT}/domains/{s.width_km}x{s.height_km}km"
test -f "$DOMAIN/PREFLIGHT_OK" || echo "Proceeding under benchmark preflight waiver for $DOMAIN" >&2
test -x "$EXE" || {{ echo "missing executable: $EXE" >&2; exit 21; }}
test -f "{ROOT}/provenance/{s.platform}_READY" || {{ echo "binary validation gate missing" >&2; exit 22; }}
mkdir -p "{run}/output" "{run}/restart" "{run}/logs"
rm -f "{run}/output"/*.nc
RUN_INPUT="{run}/input"
test -f "$HICAR_ROOT/run/NoahmpTable.TBL" || {{ echo "missing NoahMP support table" >&2; exit 23; }}
test -d "$HICAR_ROOT/run/rrtmgp_support" || {{ echo "missing RRTMGP support data" >&2; exit 24; }}
test -d "$HICAR_ROOT/run/mp_support" || {{ echo "missing microphysics support data" >&2; exit 25; }}
cp -f "$HICAR_ROOT/run/NoahmpTable.TBL" "$RUN_INPUT/"
rm -rf "$RUN_INPUT/rrtmgp_support" "$RUN_INPUT/mp_support"
cp -a "$HICAR_ROOT/run/rrtmgp_support" "$RUN_INPUT/"
cp -a "$HICAR_ROOT/run/mp_support" "$RUN_INPUT/"
cd "$RUN_INPUT"
export HICAR_IO_PER_NODE=1
export OMP_NUM_THREADS=1
export HICAR_TASKS_PER_NODE={s.tasks_per_node}
{gpu_env}git -C "$HICAR_ROOT" rev-parse HEAD > "{run}/provenance_git_sha.txt"
git -C "$HICAR_ROOT" diff --binary | sha256sum > "{run}/provenance_dirty_diff.sha256"
sha256sum "$EXE" > "{run}/provenance_executable.sha256"
scontrol show job "$SLURM_JOB_ID" > "{run}/provenance_slurm.txt"
HICAR_LOG="{run}/logs/hicar_${{SLURM_JOB_ID}}.out"
export HICAR_MEMORY_DIR="{run}/logs/memory"
{launcher} 2>&1 | tee "$HICAR_LOG"
! grep -Eq 'HICAR BiCGStab status=[[:space:]]*[1-9]' "$HICAR_LOG"
python3 "{ROOT}/analysis/validate_run.py" --run "{run}" --expected-hours 6
"""

def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True); (ROOT / "analysis").mkdir(exist_ok=True); (ROOT / "arrays").mkdir(exist_ok=True)
    specs = all_scenarios()
    manifest = {"case": CASE, "start": START.isoformat(sep=" "), "end": END.isoformat(sep=" "), "duration_hours":DURATION_HOURS, "dx_m":250, "repeats":REPEATS, "memory_measurement":{"cpu":"per-rank /usr/bin/time maximum resident set size (KiB)","gpu":"per-compute-rank nvidia-smi peak device memory (MiB), sampled once per second"}, "scenarios":[asdict(s) for s in specs]}
    (ROOT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    for s in specs:
        for repeat in range(1, REPEATS + 1):
            run = ROOT / "runs" / s.id / f"repeat_{repeat}"
            (run / "input").mkdir(parents=True, exist_ok=True); (run / "logs").mkdir(exist_ok=True)
            (run / "input" / "run.nml").write_text(namelist(run, s))
            script = run / "run.sbatch"; script.write_text(job(run, s, repeat)); script.chmod(0o755)
    # One homogeneous Slurm array per platform/compute-count: each array has
    # the two strong domains and one weak domain, three repeats each.
    for platform in ("cpu", "gpu"):
        for count in sorted({s.compute_ranks for s in specs if s.platform == platform}):
            group = [s for s in specs if s.platform == platform and s.compute_ranks == count]
            ref = group[0]
            paths = [str(ROOT / "runs" / s.id / f"repeat_{r}" / "run.sbatch") for s in group for r in range(1, REPEATS + 1)]
            gres = "" if platform == "cpu" else f"#SBATCH --gres=gpu:{ref.gpus_per_node}\n"
            partition = "pp-long" if platform == "cpu" else "normal"
            walltime = "10:00:00" if platform == "cpu" and count == 1 else "04:00:00"
            array = f"""#!/bin/bash
#SBATCH --job-name=hicar_{platform}_p{count}
#SBATCH --partition={partition}
#SBATCH --nodes={ref.nodes}
#SBATCH --ntasks-per-node={ref.tasks_per_node}
#SBATCH --cpus-per-task=1
{gres}#SBATCH --array=0-{len(paths)-1}
#SBATCH --time={walltime}
#SBATCH --exclusive
#SBATCH --hint=nomultithread
set -euo pipefail
RUNS=(
""" + "\n".join(f'  "{path}"' for path in paths) + """\n)
bash "${RUNS[$SLURM_ARRAY_TASK_ID]}"
"""
            out = ROOT / "arrays" / f"{platform}_p{count}.sbatch"; out.write_text(array); out.chmod(0o755)
    print(f"generated {len(specs)} scenarios and {len(specs)*REPEATS} runs under {ROOT}")

if __name__ == "__main__": main()
