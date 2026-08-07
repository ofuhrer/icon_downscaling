#!/bin/bash -l
# Publish REA-L land states at each V29 mechanism-diagnostic timestamp.
# This is source-only post-processing; it never executes HICAR.
#SBATCH --job-name=rea-l-v29-land-history
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --time=00:30:00
#SBATCH --array=0-8%2

set -euo pipefail

repo_root=${REPO_ROOT:-$SCRATCH/icon_hicar}
producer=$repo_root/case_studies/swiss_200m/scripts/produce_rea_l_land_state_balfrin.sbatch
test -r "$producer" || { echo "missing producer: $producer" >&2; exit 2; }

# The 24-hour diagnostic starts from the 2020-07-01 00 UTC REA-L cycle.  The
# terminal 24-hour state is retained so every 3-hour HICAR interval has a
# matching right endpoint.
step_hours=$(( SLURM_ARRAY_TASK_ID * 3 ))
valid_stamp=$(date -u -d "2020-07-01 00:00:00 UTC + ${step_hours} hours" +%Y%m%d_%H%M)
valid_date=${valid_stamp%_*}
valid_time=${valid_stamp#*_}

export REA_L_LAND_DATE=$valid_date
export REA_L_LAND_TIME=$valid_time
export REA_L_LAND_CYCLE_DATE=20200701
export REA_L_LAND_CYCLE_TIME=0000
export REA_L_LAND_STEP_HOURS=$step_hours
export REA_L_LAND_OUTPUT_DIR=${V29_REA_L_LAND_HISTORY_DIR:-$SCRATCH/icon_hicar/mechanism_diagnosis/v2/rea_l_land_history}
export REA_L_LAND_WORK_ROOT=${V29_REA_L_LAND_HISTORY_WORK_ROOT:-$SCRATCH/icon_hicar/mechanism_diagnosis/v2/tmp}

exec bash "$producer"
