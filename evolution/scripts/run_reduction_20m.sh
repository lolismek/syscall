#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/run_alt_problem_20m.sh" reduction_v1 "${1:-.runs/search-reduction-20m}"
