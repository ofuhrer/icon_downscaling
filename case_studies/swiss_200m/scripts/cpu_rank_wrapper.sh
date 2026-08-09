#!/usr/bin/env bash
# CPU control runs need the same MPI rank layout without GPU/NUMA pinning.
set -euo pipefail

exec "$@"
