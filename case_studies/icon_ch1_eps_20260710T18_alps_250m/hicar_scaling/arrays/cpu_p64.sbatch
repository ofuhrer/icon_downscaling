#!/bin/bash
#SBATCH --job-name=hicar_cpu_p64
#SBATCH --partition=pp-long
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=65
#SBATCH --cpus-per-task=1
#SBATCH --array=0-8
#SBATCH --time=04:00:00
#SBATCH --exclusive
#SBATCH --hint=nomultithread
set -euo pipefail
RUNS=(
  "/Users/fuhrer/Work/agentic/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_scaling/runs/strong_cpu_80x80_p64/repeat_1/run.sbatch"
  "/Users/fuhrer/Work/agentic/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_scaling/runs/strong_cpu_80x80_p64/repeat_2/run.sbatch"
  "/Users/fuhrer/Work/agentic/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_scaling/runs/strong_cpu_80x80_p64/repeat_3/run.sbatch"
  "/Users/fuhrer/Work/agentic/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_scaling/runs/strong_cpu_240x160_p64/repeat_1/run.sbatch"
  "/Users/fuhrer/Work/agentic/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_scaling/runs/strong_cpu_240x160_p64/repeat_2/run.sbatch"
  "/Users/fuhrer/Work/agentic/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_scaling/runs/strong_cpu_240x160_p64/repeat_3/run.sbatch"
  "/Users/fuhrer/Work/agentic/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_scaling/runs/weak_cpu_80x80_p64/repeat_1/run.sbatch"
  "/Users/fuhrer/Work/agentic/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_scaling/runs/weak_cpu_80x80_p64/repeat_2/run.sbatch"
  "/Users/fuhrer/Work/agentic/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_scaling/runs/weak_cpu_80x80_p64/repeat_3/run.sbatch"
)
bash "${RUNS[$SLURM_ARRAY_TASK_ID]}"
