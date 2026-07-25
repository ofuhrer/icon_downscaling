#!/bin/bash
#SBATCH --job-name=hicar_gpu_p2
#SBATCH --partition=normal
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=3
#SBATCH --cpus-per-task=1
#SBATCH --gres=gpu:2
#SBATCH --array=0-8
#SBATCH --time=04:00:00
#SBATCH --exclusive
#SBATCH --hint=nomultithread
set -euo pipefail
RUNS=(
  "/Users/fuhrer/Work/agentic/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_scaling/runs/strong_gpu_80x80_p2/repeat_1/run.sbatch"
  "/Users/fuhrer/Work/agentic/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_scaling/runs/strong_gpu_80x80_p2/repeat_2/run.sbatch"
  "/Users/fuhrer/Work/agentic/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_scaling/runs/strong_gpu_80x80_p2/repeat_3/run.sbatch"
  "/Users/fuhrer/Work/agentic/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_scaling/runs/strong_gpu_240x160_p2/repeat_1/run.sbatch"
  "/Users/fuhrer/Work/agentic/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_scaling/runs/strong_gpu_240x160_p2/repeat_2/run.sbatch"
  "/Users/fuhrer/Work/agentic/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_scaling/runs/strong_gpu_240x160_p2/repeat_3/run.sbatch"
  "/Users/fuhrer/Work/agentic/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_scaling/runs/weak_gpu_80x40_p2/repeat_1/run.sbatch"
  "/Users/fuhrer/Work/agentic/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_scaling/runs/weak_gpu_80x40_p2/repeat_2/run.sbatch"
  "/Users/fuhrer/Work/agentic/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_scaling/runs/weak_gpu_80x40_p2/repeat_3/run.sbatch"
)
bash "${RUNS[$SLURM_ARRAY_TASK_ID]}"
