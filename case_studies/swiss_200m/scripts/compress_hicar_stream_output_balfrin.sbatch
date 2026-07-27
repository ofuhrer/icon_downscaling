#!/bin/bash -l
# Compress one HICAR daily output from OUTPUT_FILE_LIST as an array task.
#SBATCH --job-name=hicar-output-compress
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=01:00:00

set -euo pipefail
. "$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)/scripts/load_balfrin_site_config.sh"
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "${USER_ENV_ROOT:-/mch-environment/v8}/modules"
module load netcdf-c/4.9.2-nvhpc

list=${OUTPUT_FILE_LIST:?Set OUTPUT_FILE_LIST to one absolute source path per line}
index=${OUTPUT_INDEX:-${SLURM_ARRAY_TASK_ID:-}}
[[ "$index" =~ ^[0-9]+$ ]] || { echo "output index is required" >&2; exit 2; }
repo_root=${REPO_ROOT:-$SCRATCH/icon_hicar}
python=${HICAR_VALIDATION_PYTHON:-$repo_root/venv_static/bin/python}
compressor="$repo_root/case_studies/swiss_200m/streaming/compress_output_file.py"
target_dir=${COMPRESSED_OUTPUT_DIR:?Set COMPRESSED_OUTPUT_DIR}
source=$(sed -n "$((index + 1))p" "$list")
test -n "$source" || { echo "output index is outside the file list" >&2; exit 2; }
target="$target_dir/$(basename "$source")"
report="$target.compression.json"
mkdir -p "$target_dir"
"$python" "$compressor" \
  --source "$source" \
  --target "$target" \
  --report "$report" \
  --compression-level "${OUTPUT_COMPRESSION_LEVEL:-1}"
