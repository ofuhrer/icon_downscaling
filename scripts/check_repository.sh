#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: check_repository.sh [--syntax-only]

Check tracked Python and Bash syntax. The default also checks Git whitespace
and runs the configured Ruff rules. It does not compile external models.
EOF
}

die() {
  echo "error: $*" >&2
  exit 2
}

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
SYNTAX_ONLY=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --syntax-only) SYNTAX_ONLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

cd "$REPO_ROOT"

python_files=()
while IFS= read -r -d '' path; do
  if [ -f "$path" ]; then
    python_files+=("$path")
  fi
done < <(git ls-files -z -- '*.py')

if [ "${#python_files[@]}" -gt 0 ]; then
  python3 -m py_compile "${python_files[@]}"
fi

shell_files=()
while IFS= read -r -d '' path; do
  if [ -f "$path" ]; then
    shell_files+=("$path")
  fi
done < <(git ls-files -z -- '*.sh' '*.sbatch')

for path in "${shell_files[@]}"; do
  bash -n "$path"
done

if [ "$SYNTAX_ONLY" -eq 0 ]; then
  git diff --check
  git diff --cached --check
  command -v ruff >/dev/null 2>&1 ||
    die "ruff is required; install requirements/dev.txt"
  # Keep repository CI focused on correctness while historical experiment
  # scripts are gradually brought under the broader style rules.
  ruff check --select E9,F63,F7,F82 scripts tests \
    case_studies/swiss_100m \
    case_studies/swiss_200m
fi

printf 'checked %d Python and %d shell files\n' \
  "${#python_files[@]}" "${#shell_files[@]}"
